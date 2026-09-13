"""specification_db -- fetch and parse the Arch `core`/`extra`/`multilib` package databases.

Every version, dependency edge, virtual-provides link and group membership that
the whole Azzio specification is computed from enters the program through this module.
It reads the *official* Arch `.db` tarballs (gzipped tar of `<pkg>-<ver>/desc`
records) rather than the host's pacman databases, so a non-Arch build host
cannot poison the specification with the wrong versions/names. The parsing is brittle in
exactly the silent ways that matter: a botched `dep_name` token-strip would drop
real edges or keep version noise, a wrong duplicate-resolution order would pick a
`multilib` record over the `core` one, a mis-built `provides`/`groups` index
would break resolution, and the offline guard is the only thing standing between
`--offline` and an accidental network fetch. These functions are pure or
single-seam (the online path just calls `urllib.request.urlopen`), so we pin
their exact outputs against hand-built tarball fixtures and a faked opener.
"""

from __future__ import annotations

import io
import os
import tarfile

import pytest

import specification_db
from specification_db import (
    REPOS,
    db_path,
    dep_name,
    fetch_databases,
    load_databases,
)


# --- fixtures --------------------------------------------------------------

def _make_db(path, records):
    """Write a gzipped-tar `.db` fixture at `path`.

    `records` is an iterable of (name, fields) where fields is a dict mapping a
    `%KEY%` name (without the percent signs) to a list of value lines. This
    mirrors the on-disk `<pkg>-<ver>/desc` layout the real Arch repos ship.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, fields in records:
            ver = (fields.get("VERSION", ["0-0"]) or ["0-0"])[0]
            lines = []
            for key, vals in fields.items():
                lines.append(f"%{key}%")
                lines.extend(vals)
                lines.append("")
            text = "\n".join(lines) + "\n"
            data = text.encode("utf-8")
            member = tarfile.TarInfo(name=f"{name}-{ver}/desc")
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    return path


class _FakeResp:
    """Minimal stand-in for the urlopen context manager: only `read()`."""

    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


# --- db_path ---------------------------------------------------------------

def test_db_path_joins_cache_dir_and_repo():
    # The cache layout the fetch/load pair agree on: <cache>/<repo>.db.
    assert db_path("/tmp/cache", "core") == "/tmp/cache/core.db"


def test_db_path_uses_os_join_not_naive_concat():
    # A trailing slash on the cache dir must not double up.
    assert db_path("/tmp/cache/", "extra") == "/tmp/cache/extra.db"


# --- REPOS -----------------------------------------------------------------

def test_repos_exact_order():
    # core > extra > multilib is the priority order load_databases relies on for
    # duplicate resolution; the tuple *is* that contract.
    assert REPOS == ("core", "extra", "multilib")


def test_repos_is_immutable_tuple():
    assert isinstance(REPOS, tuple)


# --- dep_name --------------------------------------------------------------

def test_dep_name_strips_gte():
    assert dep_name("foo>=1.2") == "foo"


def test_dep_name_keeps_soname_before_equals():
    # A shared-library provides token: the .so name is kept, the =1-64 is dropped.
    assert dep_name("libfoo.so=1-64") == "libfoo.so"


def test_dep_name_strips_description_after_colon():
    # optdepends style "pkg: what it does" -> just the pkg name.
    assert dep_name("foo: desc") == "foo"


def test_dep_name_strips_lte():
    assert dep_name("a<=2") == "a"


def test_dep_name_strips_gt():
    assert dep_name("x>3") == "x"


def test_dep_name_strips_lt():
    assert dep_name("y<9") == "y"


def test_dep_name_strips_plain_equals():
    assert dep_name("bar=2.0-1") == "bar"


def test_dep_name_strips_surrounding_whitespace():
    assert dep_name("  spaced  ") == "spaced"


def test_dep_name_passthrough_bare_name():
    assert dep_name("plain") == "plain"


def test_dep_name_colon_takes_precedence_over_operator():
    # The colon-split happens first, so a description that itself contains an
    # operator never leaks the operator into the name.
    assert dep_name("pkg: needs >=1.2 of something") == "pkg"


# --- load_databases: field extraction --------------------------------------

def test_load_databases_parses_core_fields(tmp_path):
    path = _make_db(
        str(tmp_path / "core.db"),
        [
            (
                "foo",
                {
                    "NAME": ["foo"],
                    "VERSION": ["1.0-1"],
                    "DESC": ["a foo"],
                    "URL": ["https://example.invalid"],
                    "ISIZE": ["2048"],
                    "CSIZE": ["512"],
                    "LICENSE": ["MIT", "GPL"],
                },
            )
        ],
    )
    packages, provides, groups = load_databases([path])

    rec = packages["foo"]
    assert rec["name"] == "foo"
    assert rec["version"] == "1.0-1"
    assert rec["desc"] == "a foo"
    assert rec["url"] == "https://example.invalid"
    # LICENSE lines are space-joined into a single string.
    assert rec["license"] == "MIT GPL"


def test_load_databases_repo_from_db_filename(tmp_path):
    # The repo tag is derived from the .db basename, not from any field.
    path = _make_db(
        str(tmp_path / "extra.db"),
        [("foo", {"NAME": ["foo"], "VERSION": ["1-1"]})],
    )
    packages, _, _ = load_databases([path])
    assert packages["foo"]["repo"] == "extra"


def test_load_databases_isize_and_csize_are_ints(tmp_path):
    path = _make_db(
        str(tmp_path / "core.db"),
        [("foo", {"NAME": ["foo"], "VERSION": ["1-1"], "ISIZE": ["4096"], "CSIZE": ["777"]})],
    )
    packages, _, _ = load_databases([path])
    rec = packages["foo"]
    assert rec["isize"] == 4096 and isinstance(rec["isize"], int)
    assert rec["csize"] == 777 and isinstance(rec["csize"], int)


def test_load_databases_missing_isize_defaults_to_zero(tmp_path):
    path = _make_db(
        str(tmp_path / "core.db"),
        [("foo", {"NAME": ["foo"], "VERSION": ["1-1"]})],
    )
    packages, _, _ = load_databases([path])
    assert packages["foo"]["isize"] == 0
    assert packages["foo"]["csize"] == 0


def test_load_databases_depends_run_through_dep_name(tmp_path):
    # Version constraints and sonames in DEPENDS are stripped to bare names.
    path = _make_db(
        str(tmp_path / "core.db"),
        [
            (
                "foo",
                {
                    "NAME": ["foo"],
                    "VERSION": ["1-1"],
                    "DEPENDS": ["bar>=1.2", "libz.so=1-64", "baz"],
                },
            )
        ],
    )
    packages, _, _ = load_databases([path])
    assert packages["foo"]["depends"] == ["bar", "libz.so", "baz"]


def test_load_databases_replaces_run_through_dep_name(tmp_path):
    path = _make_db(
        str(tmp_path / "core.db"),
        [("foo", {"NAME": ["foo"], "VERSION": ["1-1"], "REPLACES": ["old>=2"]})],
    )
    packages, _, _ = load_databases([path])
    assert packages["foo"]["replaces"] == ["old"]


def test_load_databases_optdepends_and_provides_raw_kept_verbatim(tmp_path):
    # optdepends keeps its "pkg: reason" text; provides_raw keeps the raw token.
    path = _make_db(
        str(tmp_path / "core.db"),
        [
            (
                "foo",
                {
                    "NAME": ["foo"],
                    "VERSION": ["1-1"],
                    "OPTDEPENDS": ["extra: for the thing"],
                    "PROVIDES": ["virtfoo=1"],
                },
            )
        ],
    )
    packages, _, _ = load_databases([path])
    assert packages["foo"]["optdepends"] == ["extra: for the thing"]
    assert packages["foo"]["provides_raw"] == ["virtfoo=1"]


def test_load_databases_record_has_expected_keys(tmp_path):
    path = _make_db(
        str(tmp_path / "core.db"),
        [("foo", {"NAME": ["foo"], "VERSION": ["1-1"]})],
    )
    packages, _, _ = load_databases([path])
    assert set(packages["foo"]) == {
        "name",
        "repo",
        "version",
        "desc",
        "url",
        "isize",
        "csize",
        "license",
        "depends",
        "optdepends",
        "provides_raw",
        "groups",
        "replaces",
    }


# --- load_databases: indexes -----------------------------------------------

def test_load_databases_provides_index_uses_dep_name(tmp_path):
    # The virtual name in the provides index is the stripped token, mapping to
    # the real provider package name.
    path = _make_db(
        str(tmp_path / "core.db"),
        [
            ("foo", {"NAME": ["foo"], "VERSION": ["1-1"], "PROVIDES": ["virtfoo=1"]}),
            ("bar", {"NAME": ["bar"], "VERSION": ["1-1"], "PROVIDES": ["virtfoo=2"]}),
        ],
    )
    _, provides, _ = load_databases([path])
    assert provides == {"virtfoo": ["foo", "bar"]}


def test_load_databases_groups_index(tmp_path):
    path = _make_db(
        str(tmp_path / "core.db"),
        [
            ("foo", {"NAME": ["foo"], "VERSION": ["1-1"], "GROUPS": ["base", "devel"]}),
            ("bar", {"NAME": ["bar"], "VERSION": ["1-1"], "GROUPS": ["base"]}),
        ],
    )
    _, _, groups = load_databases([path])
    assert groups == {"base": ["foo", "bar"], "devel": ["foo"]}


def test_load_databases_indexes_are_plain_dicts(tmp_path):
    # load_databases converts its internal defaultdicts to plain dicts, so a
    # lookup of an unknown key raises KeyError instead of silently inserting [].
    path = _make_db(
        str(tmp_path / "core.db"),
        [("foo", {"NAME": ["foo"], "VERSION": ["1-1"], "PROVIDES": ["v=1"], "GROUPS": ["g"]})],
    )
    _, provides, groups = load_databases([path])
    assert type(provides) is dict
    assert type(groups) is dict


# --- load_databases: duplicate resolution ----------------------------------

def test_load_databases_first_repo_wins_on_duplicate(tmp_path):
    # A name present in both core and extra keeps the core record (core listed
    # first in db_paths). This is the whole point of the REPOS priority order.
    core = _make_db(
        str(tmp_path / "core.db"),
        [("dup", {"NAME": ["dup"], "VERSION": ["1-1"]})],
    )
    extra = _make_db(
        str(tmp_path / "extra.db"),
        [
            ("dup", {"NAME": ["dup"], "VERSION": ["2-2"]}),
            ("only", {"NAME": ["only"], "VERSION": ["9-9"]}),
        ],
    )
    packages, _, _ = load_databases([core, extra])
    assert packages["dup"]["version"] == "1-1"
    assert packages["dup"]["repo"] == "core"
    # The non-duplicate from extra is still picked up.
    assert packages["only"]["repo"] == "extra"


def test_load_databases_duplicate_does_not_double_index(tmp_path):
    # The losing duplicate record must NOT contribute its provides/groups either,
    # or the index would list a package that isn't in `packages`.
    core = _make_db(
        str(tmp_path / "core.db"),
        [("dup", {"NAME": ["dup"], "VERSION": ["1-1"], "PROVIDES": ["v=1"]})],
    )
    extra = _make_db(
        str(tmp_path / "extra.db"),
        [("dup", {"NAME": ["dup"], "VERSION": ["2-2"], "PROVIDES": ["v=2"]})],
    )
    _, provides, _ = load_databases([core, extra])
    assert provides == {"v": ["dup"]}


def test_load_databases_ignores_non_desc_members(tmp_path):
    # Real .db tars carry `<pkg>-<ver>/` dir entries and sometimes `depends`
    # files; only `.../desc` members are records.
    path = str(tmp_path / "core.db")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        desc = b"%NAME%\nfoo\n%VERSION%\n1-1\n"
        m = tarfile.TarInfo(name="foo-1-1/desc")
        m.size = len(desc)
        tar.addfile(m, io.BytesIO(desc))
        # A sibling non-desc file that must be skipped.
        junk = b"bar\n"
        m2 = tarfile.TarInfo(name="foo-1-1/depends")
        m2.size = len(junk)
        tar.addfile(m2, io.BytesIO(junk))
    with open(path, "wb") as f:
        f.write(buf.getvalue())

    packages, _, _ = load_databases([path])
    assert set(packages) == {"foo"}


def test_load_databases_skips_record_with_no_name(tmp_path):
    # A desc lacking %NAME% is dropped rather than crashing.
    path = str(tmp_path / "core.db")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        desc = b"%VERSION%\n1-1\n"
        m = tarfile.TarInfo(name="nameless-1-1/desc")
        m.size = len(desc)
        tar.addfile(m, io.BytesIO(desc))
    with open(path, "wb") as f:
        f.write(buf.getvalue())

    packages, _, _ = load_databases([path])
    assert packages == {}


# --- fetch_databases: offline branch ---------------------------------------

def test_fetch_offline_missing_raises(tmp_path):
    # --offline with an empty cache must refuse rather than reach the network.
    with pytest.raises(FileNotFoundError):
        fetch_databases(str(tmp_path), offline=True)


def test_fetch_offline_present_returns_all_three_paths(tmp_path, monkeypatch):
    # With the three cached files present, offline returns them in REPOS order
    # and never opens a URL.
    def _boom(*a, **k):
        raise AssertionError("offline path must not touch the network")

    monkeypatch.setattr(specification_db.urllib.request, "urlopen", _boom)
    for repo in REPOS:
        (tmp_path / f"{repo}.db").write_bytes(b"")

    paths = fetch_databases(str(tmp_path), offline=True)
    assert paths == [
        str(tmp_path / "core.db"),
        str(tmp_path / "extra.db"),
        str(tmp_path / "multilib.db"),
    ]


def test_fetch_offline_partial_still_raises(tmp_path):
    # Only two of three cached -> still an error (all three are required).
    (tmp_path / "core.db").write_bytes(b"")
    (tmp_path / "extra.db").write_bytes(b"")
    with pytest.raises(FileNotFoundError):
        fetch_databases(str(tmp_path), offline=True)


# --- fetch_databases: online branch ----------------------------------------

def test_fetch_online_first_url_and_timeout(tmp_path, monkeypatch):
    # The first fetched URL is the canonical geo-mirror core.db path, and the
    # caller-supplied timeout is forwarded to urlopen unchanged.
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append((url, timeout))
        return _FakeResp(b"DATA-" + url.encode())

    monkeypatch.setattr(specification_db.urllib.request, "urlopen", fake_urlopen)

    fetch_databases(str(tmp_path), timeout=55)
    assert calls[0][0] == "https://geo.mirror.pkgbuild.com/core/os/x86_64/core.db"
    assert calls[0][1] == 55


def test_fetch_online_all_three_urls(tmp_path, monkeypatch):
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return _FakeResp(b"x")

    monkeypatch.setattr(specification_db.urllib.request, "urlopen", fake_urlopen)

    fetch_databases(str(tmp_path))
    assert calls == [
        "https://geo.mirror.pkgbuild.com/core/os/x86_64/core.db",
        "https://geo.mirror.pkgbuild.com/extra/os/x86_64/extra.db",
        "https://geo.mirror.pkgbuild.com/multilib/os/x86_64/multilib.db",
    ]


def test_fetch_online_writes_bytes_to_dest(tmp_path, monkeypatch):
    # The exact bytes returned by read() land in <cache>/<repo>.db.
    def fake_urlopen(url, timeout=None):
        return _FakeResp(b"payload-for-" + url.split("/")[-1].encode())

    monkeypatch.setattr(specification_db.urllib.request, "urlopen", fake_urlopen)

    paths = fetch_databases(str(tmp_path))
    assert (tmp_path / "core.db").read_bytes() == b"payload-for-core.db"
    assert paths == [
        str(tmp_path / "core.db"),
        str(tmp_path / "extra.db"),
        str(tmp_path / "multilib.db"),
    ]


def test_fetch_online_custom_mirror(tmp_path, monkeypatch):
    # A non-default mirror is used verbatim in the constructed URL.
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return _FakeResp(b"x")

    monkeypatch.setattr(specification_db.urllib.request, "urlopen", fake_urlopen)

    fetch_databases(str(tmp_path), mirror="https://mirror.example.invalid")
    assert calls[0] == "https://mirror.example.invalid/core/os/x86_64/core.db"


def test_fetch_creates_cache_dir(tmp_path, monkeypatch):
    # A not-yet-existing cache dir is created before writing.
    def fake_urlopen(url, timeout=None):
        return _FakeResp(b"x")

    monkeypatch.setattr(specification_db.urllib.request, "urlopen", fake_urlopen)
    target = tmp_path / "new" / "cache"
    assert not target.exists()

    fetch_databases(str(target))
    assert target.is_dir()
    assert (target / "core.db").read_bytes() == b"x"


# --- round trip: fetch (faked) then load -----------------------------------

def test_fetch_then_load_round_trip(tmp_path, monkeypatch):
    # A faked online fetch that returns real .db bytes feeds straight into
    # load_databases, proving the two halves agree on the on-disk layout.
    dbs = {}
    for repo in REPOS:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            desc = f"%NAME%\npkg-{repo}\n%VERSION%\n1-1\n".encode()
            m = tarfile.TarInfo(name=f"pkg-{repo}-1-1/desc")
            m.size = len(desc)
            tar.addfile(m, io.BytesIO(desc))
        dbs[repo] = buf.getvalue()

    def fake_urlopen(url, timeout=None):
        repo = url.split("/")[-1].removesuffix(".db")
        return _FakeResp(dbs[repo])

    monkeypatch.setattr(specification_db.urllib.request, "urlopen", fake_urlopen)

    paths = fetch_databases(str(tmp_path))
    packages, _, _ = load_databases(paths)
    assert set(packages) == {"pkg-core", "pkg-extra", "pkg-multilib"}
    assert packages["pkg-core"]["repo"] == "core"
