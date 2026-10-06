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
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import logstream
import pacman as pacman_cfg
import paths

ProgressCb = Callable[[int], None]

# Whole-transaction retry-with-backoff ladder for the `pacman -Sw` cache fetch.
#
# archive.archlinux.org (the PINNED snapshot host -- see pacman.ARCH_SNAPSHOT) is both
# slow and flaky: it rate-limits aggressive parallel pulls ("too many errors ... download
# library error") AND serves individual files at a crawl. The conf now fetches through a
# curl XferCommand with per-file stall/abort/retry recovery (pacman.download_conf /
# _DOWNLOAD_XFERCOMMAND), which also means downloads are SERIAL -- so the old
# per-attempt parallelism no longer varies the transfer. This ladder is therefore a coarse
# retry-with-backoff around curl's own per-file retries: if a whole `pacman -Sw` still
# comes back non-zero (e.g. curl exhausted its retries on a package while the host was
# having a bad minute), pause and run it again. Because `pacman -Sw --cachedir` is RESUMABLE
# (finished packages are durable, partial files continue via curl -C -), each retry
# re-fetches only what is still missing. The values are passed to ParallelDownloads for
# documentation/compat but are inert while XferCommand is set (see download_conf); what
# matters is the RETRY COUNT and the backoff between the attempts.
#
# SIX attempts, not three: the single archive origin does not just throttle -- it FLAPS
# (goes refuse-connections/unreachable for a minute or two, then recovers; observed as
# "curl: (7) Failed to connect ... Could not connect to server" for a whole ladder run
# even though the host was fine seconds before and after). curl's own 5 per-file retries
# only span ~25s, so a flap outlasts them and fails the whole -Sw; the OUTER ladder is what
# rides out a multi-minute outage. Because `pacman -Sw --cachedir` is resumable, each extra
# rung is nearly free (it re-fetches only what is still missing), so we trade a few cheap
# retries for surviving a transient origin outage instead of throwing away a 90%-complete
# download. The trailing 1s are just "run it again" -- parallelism is inert under XferCommand.
_PARALLEL_LADDER = (5, 2, 1, 1, 1, 1)

# Seconds to pause before the Nth retry (index 0 == the pause after the first failure).
# A growing back-off gives a throttling OR flapping archive origin time to recover; the later
# rungs wait up to a minute so the ladder as a whole rides out a ~3-4 minute outage rather
# than hammering a downed host. len == ladder-1: there is no pause after the last rung.
# Cumulative pause is ~3.5 min (15+30+45+60+60), so with curl's own per-file retries the
# ladder as a whole rides out a multi-minute origin outage.
_RETRY_BACKOFF = (15, 30, 45, 60, 60)


class PackageError(RuntimeError):
    pass


# The `pacman -Sw` download band inside step 12's progress slice (permille of the
# STEP, as handed to ProgressBar.sub): _sync_and_download opens at 20 and the whole
# transaction ends at 440 (see build_cache, which calls progress(440) right after).
# The live watcher below sweeps the bar across [20, 430] as package files land, and
# the existing progress(440) snaps the last sliver once the download returns. Without
# this the bar sits frozen at 20 (an apparent "stuck at 6%") for the ENTIRE multi-GB
# serial fetch from the throttled archive host -- the single longest wait in the build.
_DL_PERMILLE_LO = 20
_DL_PERMILLE_HI = 430


def _watch_download_progress(pkg_repo: Path, target: int, baseline: int,
                             progress: ProgressCb, phase: Callable[[str], None],
                             stop: "threading.Event", interval: float = 2.0) -> None:
    """Drive the progress bar from the cache dir's package-file count while `pacman -Sw`
    runs, so the bar visibly climbs instead of freezing for the whole download.

    The curl XferCommand fetch is SERIAL and its per-file output is silenced (-sS) and
    redrawn in place with \\r, so pacman's "downloading foo..." lines cannot be counted
    off the teed stdout (see run_teed). The reliable, decoupled signal is the number of
    finished `*.pkg.tar.zst` files on disk: pacman writes each via a `.part` temp and
    renames it on completion, so a bare-name match counts only DONE packages.

    target   : how many downloadable packages the manifest wants (len(pkgs)).
    baseline : package files already present when the download began -- a warm/resumed
               cache starts part-done, so progress is measured from here, not zero, or
               the bar would teleport to the end on a near-complete re-run.
    stop     : set by the caller the instant `-Sw` returns; the loop exits promptly.

    Thread-safe: ProgressBar.sub is monotonic and ProgressBar.draw holds its own lock,
    so polling from this daemon thread only ever nudges the bar forward. A poll that
    races a rename at worst reports one stale count, corrected on the next tick."""
    # Remaining packages to fetch this run. If the cache is already complete (nothing
    # missing) there is nothing to animate -- the download returns ~instantly anyway.
    todo = max(target - baseline, 0)
    last_emitted = -1
    while not stop.wait(interval):
        done = len(list(pkg_repo.glob("*.pkg.tar.zst")))
        fetched = max(done - baseline, 0)
        if todo:
            frac = min(fetched / todo, 1.0)
        else:
            frac = 1.0
        permille = _DL_PERMILLE_LO + int(frac * (_DL_PERMILLE_HI - _DL_PERMILLE_LO))
        progress(permille)
        # Narrate the live count in the pinned bar's label, but only when it changes,
        # so the sub-checkpoint log is not spammed with an identical line every tick.
        if fetched != last_emitted:
            last_emitted = fetched
            phase(f"downloading packages into cache ({baseline + fetched}/{target})")


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


def _write_download_conf(dest: Path, parallel_downloads: int = 5, *, db_sync: bool = False) -> Path:
    dest.write_text(
        pacman_cfg.download_conf(parallel_downloads, db_sync=db_sync) + "\n", encoding="utf-8"
    )
    return dest


def _clear_stale_db_parts(pkg_db: Path) -> None:
    """Remove any leftover `*.db.part` / `*.files.part` partials under the sync dir before a
    `pacman -Sy`.

    pacman hands curl `<file>.part` and renames it on success, so a SIGKILL'd prior sync can
    leave `<repo>.db.part` on disk. The sync databases change server-side; even though the
    db-sync XferCommand no longer passes `-C -` (so curl would truncate-and-refetch, not
    append), sweeping the stale partials is a cheap, explicit guarantee that a mutable db is
    always fetched clean -- no resume, no half-written leftover mistaken for a real db. The
    packages' own `*.pkg.tar.zst.part` under the cachedir are deliberately NOT touched: those
    ARE safely resumable (immutable per version -- see pacman._DOWNLOAD_XFERCOMMAND)."""
    sync = pkg_db / "sync"
    if not sync.is_dir():
        return
    for part in list(sync.glob("*.part")):
        part.unlink(missing_ok=True)


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


def _resolved_download_count(sudo, dlconf, gpgdir, pkg_db, pkg_repo, pkgs) -> int:
    """How many package FILES `pacman -Sw` will ultimately land for `pkgs` -- the full
    dependency closure, not just the explicit list. Used as the progress-bar denominator
    so the download band tracks real completion.

    `pacman -Sw --print` lists one line (a URL) per resolved target and exits WITHOUT
    downloading, so it is fast (the DB is already synced) and side-effect-free. The
    explicit `pkgs` expand to the whole closure here (observed: ~700 explicit -> ~1200
    resolved), so counting these lines is the honest total; len(pkgs) alone undercounts
    by the entire transitive-dependency set and would peg the bar at 100% of its band
    while half the bytes are still coming. Returns len(pkgs) as a safe floor if --print
    fails for any reason -- the watcher still animates, just against a smaller total."""
    assume = [arg for name in ASSUME_INSTALLED for arg in ("--assume-installed", name)]
    try:
        out = subprocess.run(
            sudo + ["pacman", "-Sw", "--print", "--config", str(dlconf),
                    "--gpgdir", str(gpgdir), "--noconfirm", "--disable-download-timeout",
                    "--cachedir", str(pkg_repo), "--dbpath", str(pkg_db)] + assume + pkgs,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        return len(pkgs)
    # Count non-empty stdout lines that look like a package URL/target. pacman prints
    # provider-selection chatter to stderr (captured separately), so stdout is just the
    # resolved target list; fall back to the explicit count if it came back empty.
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    return len(lines) or len(pkgs)


# /etc/hosts marker so a re-run (resumable cache) does not stack duplicate pin lines.
_HOSTS_MARKER = "# azzio: pinned archive host (see downloader._pin_archive_host)"


def _pin_archive_host(hosts_path: Path = Path("/etc/hosts"),
                      resolve: "Callable[[str], list[str]] | None" = None) -> list[str]:
    """Resolve the pinned archive origin ONCE and write its IPv4 address(es) into
    /etc/hosts, so every subsequent curl/pacman lookup is answered from the static file
    instead of the network resolver. Returns the pinned IPs ([] if nothing was pinned).

    WHY THIS EXISTS -- the actual "stuck at 6%" death. `pacman -Sw` fetches ~1200 packages
    SERIALLY, each a separate curl invocation under the XferCommand (see pacman.download_conf),
    and curl does a FRESH DNS lookup per file. On a QEMU user-mode-networking (slirp) guest the
    only nameserver is the lightweight built-in forwarder (10.0.2.3); under a burst of hundreds
    of rapid lookups it drops one, curl aborts that file with "curl: (6) Could not resolve
    host: archive.archlinux.org", and the whole transaction dies mid-download (observed: the
    build ran ~420s then died on exactly that error). The host resolves fine when asked
    occasionally -- it is the per-file volume that trips the forwarder. Resolving the single
    origin ONCE here and pinning it makes all ~1200 lookups hit /etc/hosts: zero DNS traffic
    during the fetch, so a flaky forwarder can no longer kill the build.

    SAFE and SELF-HEALING: the host is a SINGLE origin (pacman.ARCHIVE_HOST), so pinning one
    IP loses nothing a live lookup would have given. We resolve through the SAME resolver the
    build would otherwise use, so the pinned IP is as fresh as DNS can offer; if the origin's
    IP ever rotated mid-build the resumable --cachedir + retry ladder re-fetch only what is
    missing on the next run. A no-op (returns []) when: resolution fails (fall back to live
    DNS -- strictly no worse than before), the marker is already present (idempotent across
    the retry ladder / a warm re-run), or /etc/hosts is not writable (e.g. not in a container);
    in every one of those cases the fetch simply proceeds on normal DNS."""
    host = pacman_cfg.ARCHIVE_HOST
    resolve = resolve or _resolve_ipv4
    try:
        existing = hosts_path.read_text(encoding="utf-8")
    except OSError:
        return []
    # Idempotent: our own prior pin, OR a hosts entry for the archive host from any source,
    # means there is already a static answer -- do not append another.
    if _HOSTS_MARKER in existing or any(
        host in line.split("#", 1)[0].split() for line in existing.splitlines()
    ):
        return []
    try:
        ips = resolve(host)
    except OSError:
        return []
    # De-dupe, preserving resolver order: getaddrinfo can repeat an A record, and an injected
    # resolver might too -- a pinned host must map to each IP exactly once.
    ips = list(dict.fromkeys(ips))
    if not ips:
        return []
    block = "\n" + _HOSTS_MARKER + "\n" + "".join(f"{ip}\t{host}\n" for ip in ips)
    try:
        with hosts_path.open("a", encoding="utf-8") as fh:
            fh.write(block)
    except OSError:
        return []
    print(f"[*] Pinned {host} -> {', '.join(ips)} in {hosts_path} "
          f"(avoids per-file DNS during the serial package fetch).")
    return ips


def _resolve_ipv4(host: str) -> list[str]:
    """All distinct IPv4 addresses for `host`, in resolver order. The download forces curl
    `-4` (see pacman._CURL_COMMON), so only A records matter; a pinned AAAA the guest cannot
    route would reintroduce the very failover stall `-4` exists to avoid."""
    infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.append(ip)
    return seen


def _sync_and_download(sudo, dlconf, gpgdir, pkg_db, pkg_repo, progress, phase=lambda _s: None,
                       full_compile: bool = False) -> None:
    # Pin the single archive origin's IPv4 into /etc/hosts BEFORE any network call, so the
    # db sync and the ~1200-package serial fetch answer every DNS lookup from the static file
    # instead of the flaky QEMU-slirp forwarder that otherwise kills the build mid-download
    # with "curl: (6) Could not resolve host" (see _pin_archive_host). No-op off-container or
    # if resolution fails -- the fetch then just uses live DNS, exactly as before.
    _pin_archive_host()
    phase("syncing package databases")
    print("[*] Syncing package databases...")
    # The db sync (-Sy) fetches MUTABLE databases, so it must NOT use the resumable (-C -)
    # curl XferCommand the package download uses: a stale <repo>.db.part would be resumed onto
    # a changed db and corrupt it (see pacman._DB_SYNC_XFERCOMMAND). Use a dedicated db-sync
    # conf (db_sync=True -> no -C -, no -f) and sweep any leftover *.db.part from a killed
    # prior sync first, so the db is always fetched clean.
    _clear_stale_db_parts(pkg_db)
    dbsyncconf = _write_download_conf(dlconf.with_name(".cache-pkgs-dbsync.conf"), db_sync=True)
    # Teed (Popen+pipe pumped through the _Tee) so pacman's DB-sync lines land in
    # compile-full.log in real time; a bare subprocess.run would inherit the PTY and never
    # reach the log.
    rc = logstream.run_teed(
        sudo + ["pacman", "-Sy", "--config", str(dbsyncconf), "--gpgdir", str(gpgdir),
                "--dbpath", str(pkg_db), "--cachedir", str(pkg_repo), "--noconfirm",
                "--disable-download-timeout"],
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
    progress(_DL_PERMILLE_LO)
    # Count packages already on disk so a warm/resumed cache animates from where it left
    # off, not from zero (see _watch_download_progress). Glob the bare names: finished
    # packages only, never the in-flight `.part` temps.
    baseline = len(list(pkg_repo.glob("*.pkg.tar.zst")))
    # The honest progress denominator is the full resolved closure, not the explicit
    # manifest list (the latter undercounts by every transitive dependency). Resolve it
    # once up front with --print (no download). Needs a conf on disk; write the default
    # rung now (the retry loop rewrites it per attempt anyway).
    _write_download_conf(dlconf, parallel_downloads=_PARALLEL_LADDER[0])
    total = _resolved_download_count(sudo, dlconf, gpgdir, pkg_db, pkg_repo, pkgs)
    phase(f"downloading packages into cache ({baseline}/{total})")

    def _attempt(parallel: int) -> int:
        # Rewrite the SAME download conf with this rung's parallelism (pacman has no
        # CLI knob for it), then run one resumable `pacman -Sw`. Teed so the download's
        # per-package lines reach compile-full.log live (run_teed feeds stdin from /dev/null,
        # as this call did explicitly).
        _write_download_conf(dlconf, parallel_downloads=parallel)
        # --assume-installed <dep> for every name our own (not-yet-built) packages provide, so the
        # dep is satisfied without downloading the stock Arch package (see ASSUME_INSTALLED).
        assume = [arg for name in ASSUME_INSTALLED for arg in ("--assume-installed", name)]
        # --disable-download-timeout relaxes pacman's built-in CONNECT timeout for the slow
        # archive host (the Dockerfile passes it for the same reason). The mid-transfer
        # slow-crawl abort -- the actual "Operation too slow" break -- is handled by the
        # conf's curl XferCommand (pacman.download_conf), not this flag.
        return logstream.run_teed(
            sudo + ["pacman", "-Sw", "--config", str(dlconf), "--gpgdir", str(gpgdir),
                    "--noconfirm", "--disable-download-timeout",
                    "--cachedir", str(pkg_repo), "--dbpath", str(pkg_db)]
            + assume + pkgs,
        )

    # Retry down the parallelism ladder: archive.archlinux.org throttles aggressive
    # pulls, and the resumable cachedir makes each gentler retry cheap -- see
    # _download_with_retry / _PARALLEL_LADDER. Raises PackageError if every rung fails.
    #
    # While it runs, a daemon thread sweeps the bar across the download band by polling
    # the cache dir's finished-package count, so the pinned bar climbs live instead of
    # freezing at _DL_PERMILLE_LO for the whole serial multi-GB fetch (the "stuck at 6%"
    # symptom). The watcher is purely observational -- it never touches the download --
    # so it is stopped and joined on EVERY exit path, success or PackageError.
    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch_download_progress,
        args=(pkg_repo, total, baseline, progress, phase, stop),
        name="pkg-download-progress", daemon=True,
    )
    watcher.start()
    try:
        _download_with_retry(_attempt)
    finally:
        stop.set()
        watcher.join(timeout=5)
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
