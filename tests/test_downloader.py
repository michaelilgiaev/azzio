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

from pathlib import Path

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
# archive.archlinux.org is slow and flaky: it rate-limits aggressive parallel pulls AND
# serves individual files at a crawl. Per-file stall/abort/retry recovery now lives in the
# conf's curl XferCommand (pacman._DOWNLOAD_XFERCOMMAND), which also serializes downloads.
# _download_with_retry() is the coarse WHOLE-TRANSACTION retry around that: if a full
# `pacman -Sw` still returns non-zero, pause and run it again (up to len(ladder) attempts).
# Because `pacman -Sw --cachedir` is resumable, each retry re-fetches only what is missing.
# It is the pure orchestration of that loop, injectable so it needs no real pacman/sleep.
# (The ladder values are still passed to ParallelDownloads for compat, but that line is
# inert while XferCommand is set -- what these tests pin is the attempt count and backoff.)

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


def test_download_retry_ladder_is_patient_enough_to_ride_out_a_flap():
    # The single archive origin FLAPS (unreachable for a minute or two, then recovers), and
    # curl's own per-file retries only span ~25s -- so the OUTER ladder must have enough
    # attempts AND enough total backoff to outlast a multi-minute outage instead of throwing
    # away a resumable, nearly-complete download. Guard both: >= 6 attempts and >= 3 minutes
    # of cumulative backoff across the retries.
    assert len(downloader._PARALLEL_LADDER) >= 6
    assert len(downloader._RETRY_BACKOFF) == len(downloader._PARALLEL_LADDER) - 1
    assert sum(downloader._RETRY_BACKOFF) >= 180


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


# --- db-sync safety: no stale .db.part resumed onto a mutable, changed database -------------
#
# The `pacman -Sy` sync fetches MUTABLE databases; it must use a NON-resumable XferCommand
# (db_sync=True -> no `-C -`) and start from a clean slate. A SIGKILL'd prior sync can leave
# a `<repo>.db.part` on disk, and resuming that stale prefix onto a changed db corrupts it.
# These pin the two halves of the fix: the conf writer passes db_sync through, and the sweep
# removes leftover db partials without touching the (safely resumable) package partials.

def _xfer_line(conf_text):
    # The active XferCommand line's value (the comment lines also mention "-C -", so match the
    # directive, not the whole file -- mirrors test_configuration_pacman._xfer).
    return next(l.split("=", 1)[1].strip() for l in conf_text.splitlines()
                if l.split("#", 1)[0].strip().startswith("XferCommand"))


def test_write_download_conf_threads_db_sync_flag(tmp_path):
    # _write_download_conf(..., db_sync=True) must emit the db-sync XferCommand (no -C -), and
    # the default must emit the resumable package one (-C -). This is what makes the -Sy call
    # use the safe variant while -Sw keeps resuming.
    pkg = downloader._write_download_conf(tmp_path / "pkg.conf")
    db = downloader._write_download_conf(tmp_path / "db.conf", db_sync=True)
    assert "-C -" in _xfer_line(pkg.read_text())
    assert "-C -" not in _xfer_line(db.read_text())


def test_clear_stale_db_parts_removes_db_partials(tmp_path):
    sync = tmp_path / "sync"
    sync.mkdir()
    (sync / "core.db.part").write_text("stale")
    (sync / "extra.db.part").write_text("stale")
    (sync / "extra.files.part").write_text("stale")
    (sync / "core.db").write_text("a real, complete db")   # must be kept
    downloader._clear_stale_db_parts(tmp_path)
    remaining = sorted(p.name for p in sync.iterdir())
    assert remaining == ["core.db"], f"stale .part files not swept: {remaining}"


def test_clear_stale_db_parts_leaves_resumable_package_partials(tmp_path):
    # Package partials live in the CACHEDIR (not the sync dir) and ARE safely resumable
    # (immutable per version), so the sweep must never touch them -- only the mutable db
    # partials under sync/ are swept.
    sync = tmp_path / "sync"
    sync.mkdir()
    (sync / "core.db.part").write_text("stale")
    pkg_part = tmp_path / "firefox-1.0-1-x86_64.pkg.tar.zst.part"
    pkg_part.write_text("half a package, resume me")
    downloader._clear_stale_db_parts(tmp_path)
    assert not (sync / "core.db.part").exists(), "stale db .part must be swept"
    assert pkg_part.exists(), "resumable package .part must be preserved"


def test_clear_stale_db_parts_tolerates_missing_sync_dir(tmp_path):
    # A first-ever build has no sync/ dir yet; the sweep must be a no-op, not a crash.
    downloader._clear_stale_db_parts(tmp_path / "pkgs" / "db")  # does not exist


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


# --- _sync_and_download wiring: the -Sy sync uses the SAFE (non-resumable) conf -------------
#
# This is the regression's wiring, not just the string generators: it pins that the actual
# `pacman -Sy` invocation is handed the db-sync conf (no -C -) while the `pacman -Sw`
# invocations keep the resumable package conf, and that the stale-db-part sweep runs before
# the sync. We fake logstream.run_teed (capture the argv) and the manifest, so no real pacman
# runs. Everything after the download (reconcile/stage) is not reached: the -Sw attempts all
# "succeed" (rc 0) so _download_with_retry returns and the function completes its download half.

def test_sync_and_download_uses_safe_conf_for_db_sync(monkeypatch, tmp_path):
    workdir = tmp_path / "work"
    pkg_db = tmp_path / "db"
    pkg_repo = tmp_path / "repo"
    (pkg_db / "sync").mkdir(parents=True)
    pkg_repo.mkdir(parents=True)
    workdir.mkdir()
    # a leftover db partial from a "killed prior sync" -- the sweep must remove it before -Sy.
    (pkg_db / "sync" / "core.db.part").write_text("stale")
    dlconf = workdir / ".cache-pkgs-pacman.conf"

    # Keep the package list tiny and deterministic (no real manifest / makepkg).
    monkeypatch.setattr(downloader, "downloadable_packages", lambda full_compile=False: ["pkga"])

    captured = []

    def fake_run_teed(cmd, **kw):
        captured.append(list(cmd))
        return 0  # every pacman call "succeeds" -> no retry loop, function returns

    monkeypatch.setattr(downloader.logstream, "run_teed", fake_run_teed)

    downloader._sync_and_download([], dlconf, tmp_path / "gpg", pkg_db, pkg_repo,
                                  progress=lambda _p: None)

    # The sweep ran: the stale db partial is gone before the sync.
    assert not (pkg_db / "sync" / "core.db.part").exists()

    sy = next(c for c in captured if "-Sy" in c)
    sw = next(c for c in captured if "-Sw" in c)
    sy_conf = Path(sy[sy.index("--config") + 1])
    sw_conf = Path(sw[sw.index("--config") + 1])
    # -Sy must use the dedicated db-sync conf (a DIFFERENT file from the package conf), and that
    # conf must carry the NON-resumable XferCommand; -Sw keeps the resumable package conf.
    assert sy_conf != sw_conf, "db sync must use its own conf, not the package conf"
    assert "-C -" not in _xfer_line(sy_conf.read_text()), "db-sync conf must drop -C -"
    assert "-C -" in _xfer_line(sw_conf.read_text()), "package conf must keep -C -"
