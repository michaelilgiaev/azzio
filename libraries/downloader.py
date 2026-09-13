"""Real-time, resumable package cache -- the port of the old cache-pkgs.sh.

Downloads (or reuses) the ~1200 packages that get baked into the ISO's offline
install repo, reconciles a local repo index incrementally, and stages the result
into the airootfs. Progress is reported via a callback so the build's bar moves.

pacman -Sw --cachedir <persistent> makes caching incremental & resumable: only
missing packages are fetched, finished ones are durable immediately, and a
re-run skips what's present. The repo index (.db) is likewise persistent and
reconciled by DELTA (only new/changed packages re-indexed), so a warm re-run is
near-instant.

Cache-first: when BUILD_OFFLINE=1 (a complete cache exists) BOTH the -Sy DB sync
and the -Sw download are skipped -- no server is contacted at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import logstream
import pacman as pacman_cfg
import paths

ProgressCb = Callable[[int], None]

# Parallel-download back-off ladder for the `pacman -Sw` cache fetch.
#
# archive.archlinux.org (the PINNED snapshot host -- see pacman.ARCH_SNAPSHOT) rate-
# limits aggressive parallel pulls: a full ~1.8 GiB transaction at 5 streams reliably
# trips its abuse throttle partway through ("too many errors from archive.archlinux.org
# ... failed to retrieve some files ... download library error"). Because `pacman -Sw
# --cachedir` is RESUMABLE (finished packages are durable, partial files continue), the
# fix is simply to retry -- each attempt gentler on the server: 5 streams, then 2, then
# 1 (fully serial). Monotonically non-increasing so we never hammer the host harder
# after it has already complained. Each retry re-fetches only what is still missing.
_PARALLEL_LADDER = (5, 2, 1)

# Seconds to pause before the Nth retry (index 0 == the pause after the first failure).
# A short, growing back-off gives the archive's throttle a moment to relax without
# stalling the build for long. len == ladder-1: there is no pause after the last rung.
_RETRY_BACKOFF = (10, 30)


class PackageError(RuntimeError):
    pass


def _download_with_retry(attempt: Callable[[int], int],
                         sleep: Callable[[float], None] = time.sleep) -> None:
    """Run `attempt(parallel)` down the _PARALLEL_LADDER until one returns rc 0.

    attempt(parallel) performs ONE `pacman -Sw` with that many parallel streams and
    returns its exit code (0 == success). On a non-zero code we pause (per _RETRY_BACKOFF)
    and drop to the next, gentler rung; the resumable cachedir means the retry only picks
    up the packages the throttled attempt failed to fetch. If every rung fails, raise
    PackageError. `sleep` is injected so the orchestration is unit-testable without real
    pauses or a real pacman."""
    for i, parallel in enumerate(_PARALLEL_LADDER):
        if attempt(parallel) == 0:
            return
        remaining = len(_PARALLEL_LADDER) - i - 1
        if remaining:
            nxt = _PARALLEL_LADDER[i + 1]
            pause = _RETRY_BACKOFF[min(i, len(_RETRY_BACKOFF) - 1)]
            print(f"[!] Package download hit the archive server's rate limit "
                  f"(parallel={parallel}). Backing off {pause}s, then retrying with "
                  f"parallel={nxt} ({remaining} attempt(s) left).")
            sleep(pause)
    raise PackageError("package download")


def manifest_packages() -> list[str]:
    """The package names in packages.x86_64, parsed EXACTLY as mkarchiso (and the
    on-disk installer) parse it: drop full-line and trailing `# ...` comments and
    blank lines, keep the bare package names in order. This is the single source of
    truth for "what the manifest asks for" -- _sync_and_download() (what to
    download) and compiler.cache_is_complete() (what the offline repo must already
    hold) both read it, so the download set and the completeness check can never
    diverge. (Package names never contain '#'.)"""
    return [tok for line in paths.PACKAGES_FILE.read_text().splitlines()
            if (tok := line.split("#", 1)[0].strip())]


# Dependency names our OWN packages PROVIDE but that no manifest entry names directly, so
# `pacman -Sw` must be told to treat them as already satisfied (--assume-installed) instead of
# downloading the stock Arch package to fill the dep. This is EMPTY now: the only entry was
# `thunar`, which existed because the manifest's thunar-volman / thunar-archive-plugin both
# `depend=('thunar')` and our `file_manager` provider is not built until the makepkg stage (after
# this download step), so `-Sw` would have pulled stock extra/thunar to satisfy them. Those two
# plugins were dropped from the manifest, so nothing depends on `thunar` at download time and there
# is nothing left to assume-installed.
ASSUME_INSTALLED = ()


def downloadable_packages(full_compile: bool = False) -> list[str]:
    """manifest_packages() minus the packages the makepkg stage builds ITSELF
    (calamares, librewolf, file_manager). Those exist on no Arch mirror, so `pacman -Sw` would
    abort on them, and they are never expected in the DOWNLOADED set -- they are
    folded into the offline repo by the makepkg stage instead. This is the exact set
    `-Sw` is given AND the exact set the offline repo must cover to be complete."""
    from makepkg import produced_names
    own = set(produced_names(full_compile))
    return [p for p in manifest_packages() if p not in own]


def missing_from_repo(pkg_repo: Path, full_compile: bool = False) -> list[str]:
    """The downloadable manifest packages that have NO package file in pkg_repo.
    Empty => the offline repo covers every package the manifest will pacstrap; a
    non-empty result means an offline build would fail with 'target not found' for
    exactly these, so the caller must go online and fetch them. Matching mirrors
    makepkg._repo_has_all: a name is covered iff `<name>-*.pkg.tar.zst` exists."""
    return [p for p in downloadable_packages(full_compile)
            if not any(pkg_repo.glob(f"{p}-*.pkg.tar.zst"))]


def _sudo() -> list[str]:
    return [] if paths.is_root() else ["sudo"]


def _run(cmd: list[str], *, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, **kw)


def _vercmp(a: str, b: str) -> int:
    out = subprocess.run(["vercmp", a, b], capture_output=True, text=True).stdout.strip()
    try:
        return int(out)
    except ValueError:
        return 0


def _split_pkg(basename: str) -> tuple[str, str, str]:
    """basename -> (db_key, name, verrel). db_key = name-ver-rel (arch+suffix
    stripped) -- exactly the entry-dir name repo-add stores."""
    key = basename.rsplit("-", 1)[0]  # drop -<arch>.pkg.tar.zst tail piece
    # key is name-ver-rel; verrel is last field, name-ver before it.
    nv, rel = key.rsplit("-", 1)
    name, ver = nv.rsplit("-", 1)
    return key, name, f"{ver}-{rel}"


def _write_download_conf(dest: Path, parallel_downloads: int = 5) -> Path:
    dest.write_text(pacman_cfg.download_conf(parallel_downloads) + "\n", encoding="utf-8")
    return dest


def build_cache(workdir: Path, cachedir: Path, offline: bool, progress: ProgressCb,
                phase: Callable[[str], None] = lambda _s: None,
                full_compile: bool = False) -> None:
    """Sync/download (unless offline), reconcile the index, and stage the cache.

    workdir      : the disposable profile tree (holds the transient sync DB + gpg dir)
    cachedir     : the persistent cache root (survives builds)
    offline      : BUILD_OFFLINE -- skip all network when the cache is complete
    progress     : called with a permille (0..1000) as milestones are reached
    phase        : called with a short sub-phase label to narrate the bar (optional)
    full_compile : the build tier. It NO LONGER changes which packages the
                   makepkg stage produces -- calamares AND librewolf are built
                   here in every tier (neither is in an Arch repo), so both are
                   always EXCLUDED from the Arch `pacman -Sw` download (they exist
                   on no mirror). The flag only changes librewolf's recipe.
                   See makepkg.produced_names().
    """
    sudo = _sudo()
    pkg_repo = cachedir / "pkgs" / "repo"
    pkg_db = cachedir / "pkgs" / "db"
    final_db = workdir / "airootfs/root/azzio/pacstrap-azzio-db"
    final_cache = workdir / "airootfs/root/azzio/pacstrap-azzio-repo"
    gpgdir = workdir / ".pkgs-gnupg"
    dlconf = _write_download_conf(workdir / ".cache-pkgs-pacman.conf")

    pkg_repo.mkdir(parents=True, exist_ok=True)
    (pkg_db / "sync").mkdir(parents=True, exist_ok=True)
    if gpgdir.exists():
        subprocess.run(["rm", "-rf", str(gpgdir)], check=False)
    gpgdir.mkdir(parents=True, exist_ok=True)

    # clear a stale db lock from a killed prior run (may be root-owned).
    _run(sudo + ["rm", "-f", str(pkg_db / "db.lck")], check=False)

    if offline:
        print("[*] Complete cache present -- skipping DB sync and download (fully offline).")
        phase("cache complete, using offline packages")
        if not any((pkg_db / "sync").iterdir()):
            raise PackageError(
                "BUILD_OFFLINE set but no cached sync DB -- wipe cache/ and rebuild online."
            )
        progress(20)
    else:
        _sync_and_download(sudo, dlconf, gpgdir, pkg_db, pkg_repo, progress, phase, full_compile)

    # hand the cache subtree back so the later unprivileged steps here can read it.
    own_uid = os.environ.get("HOST_UID") or str(os.getuid())
    own_gid = os.environ.get("HOST_GID") or str(os.getgid())
    _run(sudo + ["chown", "-R", f"{own_uid}:{own_gid}", str(pkg_repo), str(pkg_db)], check=False)

    print("[*] Reconciling local repository index with the cache...")
    phase("reconciling local repo index")
    progress(440)
    _reconcile_index(pkg_repo, progress)

    print("[*] Staging cached packages into the ISO working tree...")
    phase("staging cache into ISO tree")
    progress(880)
    final_db.mkdir(parents=True, exist_ok=True)
    final_cache.mkdir(parents=True, exist_ok=True)
    _run(["cp", "-r", f"{pkg_db}/.", f"{final_db}/"])
    _run(["cp", "-r", f"{pkg_repo}/.", f"{final_cache}/"])
    if not any(final_cache.iterdir()):
        raise PackageError("Package cache is empty after staging.")

    subprocess.run(["rm", "-rf", str(gpgdir)], check=False)
    progress(1000)
    print("[✓] Package cache is complete and staged (offline-ready, resumable).")


def _sync_and_download(sudo, dlconf, gpgdir, pkg_db, pkg_repo, progress, phase=lambda _s: None,
                       full_compile: bool = False) -> None:
    phase("syncing package databases")
    print("[*] Syncing package databases...")
    # Teed (Popen+pipe pumped through the _Tee) so pacman's DB-sync lines land in
    # compile-full.log in real time; a bare subprocess.run would inherit the PTY and never
    # reach the log.
    rc = logstream.run_teed(
        sudo + ["pacman", "-Sy", "--config", str(dlconf), "--gpgdir", str(gpgdir),
                "--dbpath", str(pkg_db), "--cachedir", str(pkg_repo), "--noconfirm"],
    )
    if rc != 0:
        if any((pkg_db / "sync").iterdir()):
            print("    [+] DB sync failed but a cached DB exists -- continuing offline.")
        else:
            raise PackageError("Could not sync package databases and no cached DB to fall back on.")

    print("[*] Preparing package list...")
    # The manifest minus our own built packages -- see downloadable_packages().
    # (calamares was in extra/ once, but Arch dropped it; librewolf lives on no
    # mirror either -- both are compiled by the makepkg stage right after this
    # download and folded into the same offline repo, so `pacman -Sw` must NOT be
    # asked for them or it aborts the whole download with "target not found".)
    # Sharing this with compiler.cache_is_complete() keeps the download set and the
    # offline-completeness check from ever drifting apart.
    pkgs = downloadable_packages(full_compile)

    print("[*] Downloading missing packages into the persistent cache (resumable)...")
    phase(f"downloading {len(pkgs)} packages into cache")
    progress(20)

    def _attempt(parallel: int) -> int:
        # Rewrite the SAME download conf with this rung's parallelism (pacman has no
        # CLI knob for it), then run one resumable `pacman -Sw`. Teed so the download's
        # per-package lines reach compile-full.log live (run_teed feeds stdin from /dev/null,
        # as this call did explicitly).
        _write_download_conf(dlconf, parallel_downloads=parallel)
        # --assume-installed <dep> for every name our own (not-yet-built) packages provide, so the
        # dep is satisfied without downloading the stock Arch package (see ASSUME_INSTALLED).
        assume = [arg for name in ASSUME_INSTALLED for arg in ("--assume-installed", name)]
        return logstream.run_teed(
            sudo + ["pacman", "-Sw", "--config", str(dlconf), "--gpgdir", str(gpgdir),
                    "--noconfirm", "--cachedir", str(pkg_repo), "--dbpath", str(pkg_db)]
            + assume + pkgs,
        )

    # Retry down the parallelism ladder: archive.archlinux.org throttles aggressive
    # pulls, and the resumable cachedir makes each gentler retry cheap -- see
    # _download_with_retry / _PARALLEL_LADDER. Raises PackageError if every rung fails.
    _download_with_retry(_attempt)
    progress(440)


def _readd_own_packages(pkg_repo: Path, full_compile: bool = False) -> None:
    """Force `repo-add` of the packages the makepkg stage BUILT so their DB entry's
    SHA256/CSIZE match the file currently on disk.

    _reconcile_index keys its delta by name-ver-rel and SKIPS a package whose key
    is already indexed. A makepkg-built package (calamares and librewolf, both
    tiers) keeps its version across rebuilds, but makepkg is not reproducible
    bit-for-bit, so the rebuilt file's checksum changes while its key does not --
    the delta skips it and the DB keeps a stale checksum. pacstrap then rejects
    the current file as corrupted. repo-add (WITHOUT -n) overwrites an existing
    same-version entry, so this simply refreshes SHA256+CSIZE to the on-disk
    bytes. Only OUR built packages need it; the downloaded Arch packages are
    immutable per version, so their DB entry from the download is always correct
    and must NOT be forced here."""
    from makepkg import produced_names
    db = pkg_repo / "pacstrap-azzio-repo.db.tar.gz"
    files: list[str] = []
    for name in produced_names(full_compile):
        files += [str(p) for p in sorted(pkg_repo.glob(f"{name}-*.pkg.tar.zst"))]
    if not files:
        return
    r = subprocess.run(["repo-add", "-q", str(db)] + files,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode != 0:
        raise PackageError("repo-add (own-package refresh)")


def _reconcile_index(pkg_repo: Path, progress: ProgressCb) -> None:
    """Incrementally reconcile pacstrap-azzio-repo.db with the .pkg files on disk.
    Only new/changed packages are added; stale names removed; duplicate older
    versions pruned. Byte-for-byte equivalent to a full rebuild's db."""
    db = pkg_repo / "pacstrap-azzio-repo.db.tar.gz"
    pkgfiles = sorted(pkg_repo.glob("*.pkg.tar.zst"))
    if not pkgfiles:
        raise PackageError("no packages in cache to index")

    have_key: dict[str, str] = {}       # db_key -> basename on disk
    have_name: dict[str, int] = {}
    file_of_name: dict[str, Path] = {}
    ver_of_name: dict[str, str] = {}
    key_of_name: dict[str, str] = {}
    superseded: list[Path] = []

    for f in pkgfiles:
        b = f.name
        key, name, verrel = _split_pkg(b)
        if name in ver_of_name:
            if _vercmp(verrel, ver_of_name[name]) > 0:
                superseded.append(file_of_name[name])
                have_key.pop(key_of_name[name], None)
            else:
                superseded.append(f)
                continue
        have_key[key] = b
        have_name[name] = 1
        file_of_name[name] = f
        ver_of_name[name] = verrel
        key_of_name[name] = key

    usable = db.is_file() and subprocess.run(
        ["bsdtar", "-tf", str(db)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0

    if not usable:
        _seed_fresh_index(pkg_repo, db, have_key, progress)
    else:
        _delta_index(pkg_repo, db, have_key, have_name)

    if superseded:
        print(f"    [-] Removing {len(superseded)} superseded package file(s) from cache.")
        for f in superseded:
            f.unlink(missing_ok=True)


def _seed_fresh_index(pkg_repo, db, have_key, progress) -> None:
    add = [str(pkg_repo / bn) for bn in have_key.values()]
    print(f"    [+] No usable index -- building fresh from {len(add)} package(s) (one-time).")
    for old in pkg_repo.glob("pacstrap-azzio-repo.db*"):
        old.unlink(missing_ok=True)
    for old in pkg_repo.glob("pacstrap-azzio-repo.files*"):
        old.unlink(missing_ok=True)
    tot, chunk = len(add), 50
    for i in range(0, tot, chunk):
        r = subprocess.run(["repo-add", "-q", str(db)] + add[i:i + chunk],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            raise PackageError("repo-add (fresh)")
        n = min(i + chunk, tot)
        print(f"    [+] Indexing {n}/{tot} packages...")
        # map the seed onto the index band 440..880
        progress(440 + (n * 440 // tot if tot else 440))


def _delta_index(pkg_repo, db, have_key, have_name) -> None:
    db_key: dict[str, int] = {}
    db_name: dict[str, int] = {}
    out = subprocess.run(["bsdtar", "-tf", str(db)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.endswith("/desc"):
            ekey = line[:-5]
            db_key[ekey] = 1
            # name = ekey minus the trailing -ver-rel (two fields)
            db_name[ekey.rsplit("-", 2)[0]] = 1

    add = [str(pkg_repo / bn) for k, bn in have_key.items() if k not in db_key]
    rm = [n for n in db_name if n not in have_name]

    if rm:
        print(f"    [-] Dropping {len(rm)} stale entr(y/ies) from the index.")
        if subprocess.run(["repo-remove", "-q", str(db)] + rm).returncode != 0:
            raise PackageError("repo-remove")
    if add:
        print(f"    [+] Indexing {len(add)} new/updated package(s).")
        if subprocess.run(["repo-add", "-q", str(db)] + add).returncode != 0:
            raise PackageError("repo-add (delta)")
    if not rm and not add:
        print("    [=] Index already up to date -- nothing to re-index.")
