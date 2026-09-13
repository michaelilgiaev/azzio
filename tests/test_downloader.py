"""downloader -- the offline package cache builder (was packages.py).

The subprocess-heavy parts (pacman -Sw, repo-add, chown) are not unit-tested.
Two things ARE pure and high-value:

  _split_pkg      parses name-ver-rel out of a .pkg.tar.zst basename. This keys
                  the whole incremental index reconcile; a mis-parse silently
                  desyncs the DB from the files on disk (pacstrap then rejects a
                  "corrupted" package). Hyphenated names and epoch versions are
                  the traps.

  the manifest tokenizer (in _sync_and_download) strips `#` comments the SAME
                  way mkarchiso does. It is inlined, so we re-derive it here and
                  pin the contract against a representative packages.x86_64 body.
"""

from __future__ import annotations

import pytest

import downloader


# --- _split_pkg: (db_key, name, verrel) ------------------------------------

def test_split_pkg_simple():
    assert downloader._split_pkg("librewolf-1.0-1-x86_64.pkg.tar.zst") == (
        "librewolf-1.0-1", "librewolf", "1.0-1",
    )


def test_split_pkg_hyphenated_name():
    # gcc-libs: the hyphen in the NAME must survive; only the arch tail is stripped.
    assert downloader._split_pkg("gcc-libs-13.2.1-3-x86_64.pkg.tar.zst") == (
        "gcc-libs-13.2.1-3", "gcc-libs", "13.2.1-3",
    )


def test_split_pkg_dotted_version():
    assert downloader._split_pkg("linux-6.9.1.arch1-1-x86_64.pkg.tar.zst") == (
        "linux-6.9.1.arch1-1", "linux", "6.9.1.arch1-1",
    )


def test_split_pkg_epoch_version():
    # Epoch (2:) stays inside verrel.
    key, name, verrel = downloader._split_pkg("python-2:3.11.5-1-any.pkg.tar.zst")
    assert name == "python"
    assert verrel == "2:3.11.5-1"


# --- the manifest tokenizer (comment/blank stripping) -----------------------

def _tokenize(text: str):
    # Independent re-derivation of downloader.manifest_packages()'s parse, kept as an
    # oracle so the shared parser's contract is testable without invoking pacman.
    return [tok for line in text.splitlines()
            if (tok := line.split("#", 1)[0].strip())]


def test_manifest_tokenizer_drops_comments_and_blanks():
    body = (
        "# Azzio package manifest\n"
        "\n"
        "base\n"
        "linux    # the kernel\n"
        "  \n"
        "# ---- Stock / Azzio delimiter ----\n"
        "firefox\n"
    )
    assert _tokenize(body) == ["base", "linux", "firefox"]


def test_manifest_tokenizer_matches_real_packages_file():
    # The real manifest must tokenize to a clean, comment-free, non-empty list --
    # every token is a plausible package name (no '#', no whitespace, non-empty).
    text = downloader.paths.PACKAGES_FILE.read_text()
    toks = _tokenize(text)
    assert toks, "packages.x86_64 tokenized to nothing"
    for t in toks:
        assert "#" not in t
        assert t == t.strip()
        assert " " not in t


def test_manifest_packages_matches_oracle_tokenizer():
    # The shared parser the download AND the offline-completeness check both call
    # must agree byte-for-byte with the independent oracle above.
    text = downloader.paths.PACKAGES_FILE.read_text()
    assert downloader.manifest_packages() == _tokenize(text)


def test_downloadable_packages_excludes_own_built_packages():
    # calamares/librewolf are compiled by the makepkg stage and exist on no mirror,
    # so they must be dropped from the set handed to `pacman -Sw` (and from the set
    # the offline repo is required to cover).
    from makepkg import produced_names
    own = set(produced_names(full_compile=False))
    dl = set(downloader.downloadable_packages(full_compile=False))
    assert own, "expected at least one own-built package"
    assert own.isdisjoint(dl), f"own packages leaked into the download set: {own & dl}"
    # everything else in the manifest is still there.
    manifest = set(downloader.manifest_packages())
    assert dl == manifest - own


def test_missing_from_repo_flags_uncached_manifest_package(monkeypatch, tmp_path):
    # A manifest package with no file in the repo is reported missing; one that has a
    # file is not. Own-built packages are never reported (they are excluded).
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "present-1.0-1-x86_64.pkg.tar.zst").write_text("")
    manifest = tmp_path / "packages.x86_64"
    manifest.write_text("# header\npresent\nabsent\n")
    monkeypatch.setattr(downloader.paths, "PACKAGES_FILE", manifest)
    assert downloader.missing_from_repo(repo, full_compile=False) == ["absent"]


def test_missing_from_repo_empty_when_repo_covers_manifest(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "alpha-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "beta-2.0-1-x86_64.pkg.tar.zst").write_text("")
    manifest = tmp_path / "packages.x86_64"
    manifest.write_text("# header\nalpha\nbeta\n")
    monkeypatch.setattr(downloader.paths, "PACKAGES_FILE", manifest)
    assert downloader.missing_from_repo(repo, full_compile=False) == []


# --- download retry / archive-server back-off -------------------------------
#
# archive.archlinux.org rate-limits aggressive parallel pulls: a 1.8 GiB transaction
# at ParallelDownloads=5 trips its abuse throttle mid-download ("too many errors from
# archive.archlinux.org, ... failed to retrieve some files"). Because `pacman -Sw
# --cachedir` is resumable, the cure is to RETRY, each attempt gentler on the server
# (fewer parallel streams) with a back-off pause between. _download_with_retry() is
# the pure orchestration of that ladder, injectable so it needs no real pacman/sleep.

def test_download_retry_succeeds_first_try_uses_max_parallelism():
    # A clean success on the first attempt runs exactly once, at the top of the ladder,
    # and never sleeps.
    seen, slept = [], []
    downloader._download_with_retry(
        lambda parallel: (seen.append(parallel), 0)[1],   # rc 0 == success
        sleep=slept.append,
    )
    assert seen == [downloader._PARALLEL_LADDER[0]]
    assert slept == []


def test_download_retry_backs_off_parallelism_then_succeeds():
    # First attempt fails (throttled); the retry drops to the next, gentler
    # parallelism level and succeeds. The failed attempt must incur one back-off sleep.
    seen, slept = [], []

    def attempt(parallel):
        seen.append(parallel)
        return 0 if len(seen) == 2 else 1     # fail once, then succeed

    downloader._download_with_retry(attempt, sleep=slept.append)
    assert seen == list(downloader._PARALLEL_LADDER[:2])   # 5 then 2 -- monotonically gentler
    assert len(slept) == 1                                  # exactly one pause, after the failure


def test_download_retry_ladder_is_monotonically_gentler():
    # The whole point is to be LESS aggressive on each retry; a ladder that ever
    # increased parallelism would hammer the server harder after it already complained.
    ladder = downloader._PARALLEL_LADDER
    assert len(ladder) >= 2
    assert ladder == tuple(sorted(ladder, reverse=True))
    assert ladder[-1] == 1                                  # gentlest possible: fully serial


def test_download_retry_raises_after_exhausting_ladder():
    # Every attempt fails -> one attempt per rung, a PackageError naming the download,
    # and NO sleep after the final failure (nothing left to wait for).
    seen, slept = [], []
    with pytest.raises(downloader.PackageError):
        downloader._download_with_retry(
            lambda parallel: (seen.append(parallel), 1)[1],  # always fail
            sleep=slept.append,
        )
    assert seen == list(downloader._PARALLEL_LADDER)
    assert len(slept) == len(downloader._PARALLEL_LADDER) - 1


def test_download_conf_honours_parallel_downloads_override():
    # The retry lowers aggression by REGENERATING the download conf with fewer parallel
    # streams (pacman exposes ParallelDownloads only via config, never a CLI flag), so
    # the override must actually reach the emitted ParallelDownloads line.
    assert "ParallelDownloads = 2" in downloader.pacman_cfg.download_conf(parallel_downloads=2)
    assert "ParallelDownloads = 1" in downloader.pacman_cfg.download_conf(parallel_downloads=1)
    # default is unchanged when the caller does not override.
    assert "ParallelDownloads = 5" in downloader.pacman_cfg.download_conf()


def test_no_duplicates_within_azzio_additions_block():
    # packages.x86_64 has two blocks: STOCK ARCH (the upstream releng baseline) and
    # AZZIO ADDITIONS (the block the maintainer actually edits). A package listed
    # in BOTH blocks is intentional and benign -- releng ships e.g. grub/lvm2 and the
    # installer re-declares them; pacman/mkarchiso dedup the manifest. The real
    # editing hazard is a package listed twice WITHIN the additions block, so that
    # is what we guard.
    lines = downloader.paths.PACKAGES_FILE.read_text().splitlines()
    banner = max(i for i, l in enumerate(lines) if "AZZIO ADDITIONS" in l)
    # additions content starts after the closing ===== banner line following the text.
    close = next(i for i in range(banner + 1, len(lines))
                 if set(lines[i].strip()) <= set("#= "))
    additions = _tokenize("\n".join(lines[close + 1:]))
    dupes = {t for t in additions if additions.count(t) > 1}
    assert not dupes, f"duplicate packages within the Azzio-additions block: {sorted(dupes)}"
