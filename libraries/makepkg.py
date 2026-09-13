"""Build Azzio's OWN packages (calamares, librewolf) with makepkg and drop the
resulting *.pkg.tar.zst into the offline repo the rest of the build already uses.

Everything not in the official Arch repos is built from recipes WE author in
pkgbuild -- never the AUR, never an AUR helper. This module is the
runner: it emits those recipes into a scratch dir, ensures the host has the
makedepends, runs `makepkg` as an UNPRIVILEGED user (makepkg refuses root), and
copies the built packages into cache/pkgs/repo/ so the normal index-reconcile
step (packages._reconcile_index) folds them into pacstrap-azzio-repo.db next to
the Arch packages. `calamares`/`librewolf` in packages.x86_64 then resolve from
the local repo like anything else.

Tiers: BOTH calamares and librewolf are built here in every tier -- neither is
in an official Arch repo (librewolf never was; calamares was dropped from extra/
and is now AUR-only). --full-compile only changes the RECIPE, not the set:
  * librewolf -> default = repackage the verified upstream binary tarball;
                 full = build from Firefox source.
  * calamares -> always compiled from the pinned-sha256 source tarball (there is
                 no Arch binary to install anymore).
pkgbuild.recipe_dirs(full_compile) picks the recipe set; produced_names()
below returns the (now tier-independent) set of names built HERE.

Offline policy: makepkg needs to FETCH sources (the calamares/firefox/librewolf
tarballs + git). When the build is fully offline (BUILD_OFFLINE) we SKIP the
makepkg stage if the packages are already present in the repo, and fail loudly
if they are not -- exactly like the rest of the cache-first design.
"""

from __future__ import annotations

import math
import os
import pwd
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import emit
import logstream
import paths
import pkgbuild as pkgbuild_cfg

ProgressCb = Callable[[int], None]

# Unprivileged user makepkg runs as (makepkg aborts as root). Created on demand
# on a native/root build; on a rootless build we already are unprivileged and
# just use the current user.
BUILDER_USER = "azziobuilder"

# Package NAMES built by this stage. BOTH are built in EVERY tier because neither
# is in an official Arch repo: librewolf never was; calamares USED to live in
# extra/ but Arch dropped it (it is now AUR-only), so the default tier can no
# longer `pacman -S calamares` and must build it from our own recipe like the
# full tier already did. The only thing --full-compile still changes is HOW each
# is built (see produced_names / recipe_dirs), not WHICH are built.
# This set is used to (a) exclude own packages from the Arch `pacman -Sw` download
# and (b) know which built packages to re-add/refresh in the offline repo + cache.
#
# `file_manager` is Azzio's OWN file manager (pkgbuild.pkgbuild_file_manager), built from the
# vendored source. It is NOT an Arch package -- it REPLACES stock `thunar`: pkgname=file_manager
# with provides/conflicts/replaces=('thunar'). The manifest lists `file_manager` (not `thunar`),
# and this name is what the staleness gate globs for (file_manager-*.pkg.tar.zst) and what
# _emit_recipes builds. Listed here so the offline-repo bookkeeping re-adds/refreshes it; there is
# no Arch `thunar` fetch to exclude anymore because the manifest no longer names `thunar` (the two
# consumers that depend on `thunar` -- thunar-volman/thunar-archive-plugin -- are satisfied by our
# provide, and conflicts/replaces keep stock thunar out).
PRODUCED = ("calamares", "librewolf", "file_manager")


def produced_names(full_compile: bool) -> tuple[str, ...]:
    """Names of packages the makepkg stage produces. Tier-independent now:
    calamares + librewolf are ALWAYS built here (both are AUR-only / in no Arch
    repo). --full-compile only changes the RECIPE used (source vs repackage),
    handled by recipe_dirs, not the set of names."""
    return PRODUCED


class MakepkgError(RuntimeError):
    pass


# --- compile parallelism cap ------------------------------------------------
# Every compiler this stage drives (calamares' cmake, LibreWolf/Firefox's bsys6
# make, and any raw `make` in a recipe) auto-detects the core count and, left
# alone, spawns ONE job per logical CPU. On a 24-thread host that pins all 24
# cores at 100% for the whole (multi-minute-to-multi-hour) compile and makes the
# machine unusable. Nothing upstream pins a job count: the archlinux:latest
# container ships MAKEFLAGS commented out, so makepkg passes no -j and each build
# system falls back to its own nproc default.
#
# build_jobs() decides ONE job count for the whole stage; _makepkg_one exports it
# as MAKEFLAGS/NPROC/AZZIO_JOBS so every build system obeys the same ceiling.
#
# THE CAP IS A FLAT 75% OF THE CORES (PROMPT: "just hardcode 75% so I can move
# on"). Earlier attempts scaled the reserve with machine size and still "lagged
# my PC", so the policy is now the dead-simple, predictable floor(cores * 0.75):
# a quarter of the machine is always left for the desktop/UI, at every size.
# floor keeps it an integer and guarantees at least one free core once there are
# >=2 (e.g. 2->1, 4->3, 8->6, 12->9, 24->18, 64->48); a 1-core box still gets 1.
#
# THE --use-each-cpu ESCAPE HATCH (PROMPT: "add some sort of flag --use-each-cpu
# as well"). When the operator opts in -- compile.sh --use-each-cpu, which
# compiler.main() records via set_use_each_cpu(), or the AZZIO_USE_EACH_CPU=1
# environment variable as a belt-and-braces fallback that survives into any
# subprocess -- the cap is lifted to ALL cores (one job per logical CPU, the old
# pin-everything behaviour) for a maximum-speed build on a machine the operator
# does not need to use meanwhile.
CPU_FRACTION = 0.75         # hardcoded: use 75% of the cores, leave 25% for the UI
_USE_EACH_CPU_ENV = "AZZIO_USE_EACH_CPU"  # env opt-in mirrored from the --use-each-cpu flag

# Process-wide opt-in, set once from argv by compiler.main() (see set_use_each_cpu).
# None means "not explicitly set" -> fall back to the environment variable, so the
# setting still reaches build systems/tools that only see the process environment.
_USE_EACH_CPU: bool | None = None


def set_use_each_cpu(enabled: bool) -> None:
    """Record the operator's --use-each-cpu choice process-wide AND in the
    environment, so every consumer agrees: build_jobs() reads the flag directly,
    while any child process (and profile.profiledef_sh's mkarchiso thread cap)
    inherits AZZIO_USE_EACH_CPU. compiler.main() calls this once at startup."""
    global _USE_EACH_CPU
    _USE_EACH_CPU = bool(enabled)
    os.environ[_USE_EACH_CPU_ENV] = "1" if enabled else "0"


def use_each_cpu() -> bool:
    """True if the operator asked to use every CPU (--use-each-cpu). Prefers the
    process-wide flag set by set_use_each_cpu(); falls back to the AZZIO_USE_EACH_CPU
    environment variable (1/true/yes/on) so a subprocess or an externally-set env
    still opts in. Default is False -> the 75% cap applies."""
    if _USE_EACH_CPU is not None:
        return _USE_EACH_CPU
    return os.environ.get(_USE_EACH_CPU_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _cpu_count() -> int:
    """Logical CPUs usable by this process. Prefers the affinity mask so a
    cgroup/`--cpuset-cpus` limit inside Docker is respected; falls back to the
    machine total. Mirrors estimate._cores so both reason about the same count."""
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def build_jobs(cores: int | None = None) -> int:
    """Number of parallel compile jobs to allow. This is the ONE place the cap is
    decided; _makepkg_one exports it as MAKEFLAGS/NPROC/AZZIO_JOBS so every build
    system (make, cmake --build, Firefox's bsys6 make) obeys the same ceiling
    instead of grabbing all cores. `cores` is injectable for testing; it defaults
    to the live count.

    Policy (see the block comment above):
      * --use-each-cpu opted in -> ALL cores (one job per logical CPU, no cap).
      * otherwise               -> floor(cores * 0.75), a hardcoded 75%.
    Invariant: always >= 1 (a 1-core box gets 1), and under the default 75% cap at
    least one core is left free whenever the machine has >= 2 -- so it stays usable
    during a build unless the operator explicitly asked for every CPU."""
    if cores is None:
        cores = _cpu_count()
    cores = max(1, cores)
    if use_each_cpu():
        return cores                           # every core -- the operator opted in
    return max(1, math.floor(cores * CPU_FRACTION))


# --- retry-hardened source downloads ----------------------------------------
# makepkg fetches source=() tarballs with curl via /etc/makepkg.conf's DLAGENTS.
# Arch's stock https agent is:
#     curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u
# It HAS --retry 3, yet a real build still died fetching the calamares tarball:
#     curl: (92) HTTP/2 stream 1 reset by server (error 0x8 CANCEL)
# two failure modes the stock agent does NOT recover from:
#   1. A mid-stream HTTP/2 stream reset (0x8 CANCEL) AFTER bytes have flowed is a
#      *transfer* error, and plain --retry only retries connection-time / transient
#      HTTP-status failures -- so it gives up instead of retrying. --retry-all-errors
#      makes curl retry ANY error, including that reset.
#   2. The log showed the transfer crawling (105 KB/s and dropping) for 45s before
#      the server cut it: a stall that slow never trips a retry because the socket
#      never actually dies. --speed-time 30 --speed-limit 1024 aborts a transfer
#      that stays under 1 KB/s for 30s, turning a slow-crawl-to-death into a fast
#      failure that --retry-all-errors then retries against (possibly) a better path.
# We inject these flags into curl-based DLAGENTS by rewriting the system config into
# a repo-local makepkg.conf and pointing makepkg at it with --config. Every other
# setting (CFLAGS, PACKAGER, compression, ...) is preserved verbatim; only DLAGENTS
# curl lines are hardened. rsync/scp/file agents are left untouched.
_RETRY_FLAGS = ("--retry", "5", "--retry-delay", "3", "--retry-all-errors",
                "--speed-time", "30", "--speed-limit", "1024")


def _harden_dlagents(conf_text: str) -> str:
    """Return `conf_text` (a makepkg.conf) with network curl DLAGENTS entries given
    retry/stall-recovery flags. Pure string transform, unit-tested.

    A DLAGENTS entry looks like `  'https::/usr/bin/curl -qg... -o %o %u'`. For every
    such line whose agent shells out to curl over a NETWORK protocol (http/https/ftp;
    the local `file::` copy is skipped -- retry/speed flags are meaningless for it),
    we:
      1. strip any curl retry/speed flags the stock line already carries
         (--retry[-delay|-all-errors], --speed-time, --speed-limit and their values),
         so ours are authoritative and curl doesn't see a duplicate --retry whose last
         value would silently win; then
      2. insert _RETRY_FLAGS immediately after the `curl` token.
    Idempotent: re-hardening an already-hardened line strips our own flags in step 1
    and re-adds the identical set in step 2, so output is stable. Non-curl agents
    (rsync, scp), the file:: agent, and every non-DLAGENTS line pass through
    byte-for-byte."""
    flags = " ".join(_RETRY_FLAGS)
    # Matches ` --retry 5`, ` --retry-delay 3`, ` --retry-all-errors`,
    # ` --speed-time 30`, ` --speed-limit 1024` -- the flag plus its value if any.
    strip_re = re.compile(
        r"\s+--(?:retry(?:-delay|-all-errors)?|speed-(?:time|limit))(?:\s+\d+)?")
    # The protocol prefix of the agent this line defines -- the token just before the
    # first `::`, ignoring leading `DLAGENTS=(`, quotes and whitespace. Only network
    # curl transfers get hardened; the local `file::` copy (and rsync/scp/vcs) do not.
    NET = {"http", "https", "ftp", "ftps"}
    proto_re = re.compile(r"(?:DLAGENTS\s*=\s*\()?\s*['\"]?([a-z]+)::")
    out = []
    for line in conf_text.splitlines(keepends=True):
        m = proto_re.match(line.lstrip())
        proto = m.group(1) if m else None
        if proto in NET and "curl" in line:
            line = strip_re.sub("", line)              # step 1: drop pre-existing ones
            line = re.sub(r"(/curl\b|(?<![\w/])curl\b)",  # step 2: insert ours once
                          lambda mm: f"{mm.group(0)} {flags}", line, count=1)
        out.append(line)
    return "".join(out)


def _write_hardened_conf(recipe_dir: Path, system_conf: Path = Path("/etc/makepkg.conf")) -> Path | None:
    """Materialise a retry-hardened makepkg.conf next to the recipe and return its
    path, or None if the system config can't be read (then makepkg just uses its own
    default and we lose only the extra resilience, not the build). The written file
    is `source`-free: we harden the FULL system config in place so makepkg needs no
    include and behaves identically save for the tougher DLAGENTS."""
    try:
        text = system_conf.read_text()
    except OSError:
        return None
    hardened = _harden_dlagents(text)
    dest = recipe_dir / "makepkg.hardened.conf"
    dest.write_text(hardened)
    return dest


def _sudo() -> list[str]:
    return [] if paths.is_root() else ["sudo"]


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kw)


def _sanitize_build_path(path: str, home: str) -> str:
    """Return `path` (a PATH string) with every entry under `home`/.local removed,
    so a package build resolves toolchains from the SYSTEM only.

    A uv-/pipx-/pyenv-managed shim on the user's PATH (e.g.
    ~/.local/bin/python3.12 -> ~/.local/share/uv/python/cpython-3.12/...) is
    otherwise discovered by cmake's find_package(Python ...) and linked into the
    package, producing a binary that depends on a libpython the target ISO does not
    ship (the calamares libpython3.12.so.1.0 breakage). Recipes ALSO pin
    Python_*/Python3_* to /usr, so this is belt-and-suspenders -- but it protects
    every other package generically. If stripping empties PATH (or `home` is
    falsy), fall back to a minimal system PATH so the build still finds coreutils.
    """
    if not home:
        return path
    local = os.path.join(home, ".local")
    kept = [p for p in path.split(os.pathsep)
            if p and not (p == local or p.startswith(local + os.sep))]
    return os.pathsep.join(kept) or "/usr/bin:/bin"


def _repo_has_all(pkg_repo: Path, names: tuple[str, ...]) -> bool:
    """True if a built package file exists for every name this tier produces.

    Existence ONLY -- says nothing about whether the file matches the CURRENT
    recipe. The staleness gates (cache_is_complete / the offline-skip in
    build_own_packages) call _repo_is_current instead, which pairs this with a
    recipe-fingerprint check so a package built from an OLDER recipe is not reused.
    Kept as the low-level primitive (its name-prefix match is still what those
    callers need)."""
    for name in names:
        if not any(pkg_repo.glob(f"{name}-*.pkg.tar.zst")):
            return False
    return True


# --- own-package recipe fingerprinting --------------------------------------
# The staleness gate lives in fingerprint.py (split out to keep this module within the
# size budget). Re-exported here so every caller/test keeps using them as makepkg.<name>.
# _repo_is_current stays below: it is the orchestration gate that pairs the fingerprint
# check with _repo_has_all (the package-file-exists check), both of which live here.
#
# Why it exists: the offline default tier SKIPS makepkg when the own packages are already
# in the repo (the fast rerun). That skip used to be content-BLIND -- it only checked a
# file named `calamares-*.pkg.tar.zst` existed, never whether it was built from the CURRENT
# recipe. So editing a recipe (the networkq patch on calamares, or file_manager's vendored C) did
# NOT invalidate the cached package: the stale binary was reused and the ISO/box shipped it.
# The fingerprint (see fingerprint.py) folds in every recipe file AND any vendored source
# tree, so any change forces a rebuild.
from fingerprint import (  # noqa: E402  (grouped with the fingerprinting section)
    FINGERPRINT_SUFFIX,
    FingerprintError,
    _current_recipe_fingerprints,
    _fingerprint_dir,
    _fingerprint_path,
    _read_recipe_fingerprint,
    _recipe_fingerprint,
    _source_tree_fingerprint,
    _write_recipe_fingerprint,
)


def _repo_is_current(pkg_repo: Path, full_compile: bool,
                     fp_dir: Path | None = None) -> bool:
    """True iff, for EVERY produced package, a package file exists in pkg_repo AND its
    recorded recipe fingerprint matches the recipe that would build it now. This is the
    staleness gate: a missing package, a missing/old fingerprint sidecar, or a changed
    recipe all make it False, so the caller goes online and rebuilds. fp_dir defaults
    to the real fingerprint dir; it is injectable so tests can point it at a tmp dir.

    A package cached by an OLDER build of Azzio (before fingerprints existed) has
    no sidecar -> None != current -> False -> rebuilt once, after which the sidecar
    is present and offline reruns are fast again."""
    if fp_dir is None:
        fp_dir = _fingerprint_dir()
    wanted = _current_recipe_fingerprints(full_compile)
    if not _repo_has_all(pkg_repo, tuple(wanted)):
        return False
    for name, current in wanted.items():
        if _read_recipe_fingerprint(fp_dir, name) != current:
            return False
    return True


def _emit_recipes(scratch: Path, full_compile: bool) -> list[Path]:
    """Write each recipe dir (PKGBUILD + companions) from pkgbuild into
    scratch/. Returns the list of recipe dirs to build, in order.

    A recipe may also declare a local source TREE (pkgbuild.recipe_source_trees) -- a vendored,
    version-controlled directory that cannot be carried as a text companion. For such a recipe
    the tree is COPIED into the recipe dir under its own basename so the PKGBUILD's local
    source=() entry finds it (file_manager's git-cloned tree works this way). The copy is what makepkg
    consumes, so the vendored original is never mutated by the build."""
    source_trees = pkgbuild_cfg.recipe_source_trees()
    dirs: list[Path] = []
    for dirname, files in pkgbuild_cfg.recipe_dirs(full_compile):
        d = scratch / dirname
        d.mkdir(parents=True, exist_ok=True)
        for filename, content in files.items():
            emit.write_text(d / filename, content)
        tree = source_trees.get(dirname)
        if tree is not None:
            dest = d / tree.name
            if dest.exists():
                shutil.rmtree(dest)
            # copy the vendored source tree into the recipe dir (dest basename == tree basename,
            # which is what the PKGBUILD's local source=() names). Dereference symlinks so the
            # recipe dir holds a real, self-contained tree.
            shutil.copytree(tree, dest, symlinks=False)
        dirs.append(d)
    return dirs


def _ensure_builder_user() -> str:
    """Return the username makepkg should run as. On a root build, create an
    unprivileged builder; otherwise use the current (already unprivileged) user."""
    if not paths.is_root():
        return pwd.getpwuid(os.getuid()).pw_name
    try:
        pwd.getpwnam(BUILDER_USER)
    except KeyError:
        _run(["useradd", "-m", "-s", "/bin/bash", BUILDER_USER], check=True)
    # passwordless sudo for the builder is NOT granted; makepkg installs its
    # makedepends via a separate root `pacman -S` we run below, so the builder
    # only ever runs the unprivileged compile.
    return BUILDER_USER


def _collect_makedepends(dirs: list[Path]) -> list[str]:
    """Union of makedepends + depends across the recipes, so the host can build
    (makedepends) and so runtime deps are present for any check phase."""
    want: set[str] = set()
    for d in dirs:
        pb = d / "PKGBUILD"
        # Source the PKGBUILD in bash and print its dep arrays -- authoritative,
        # avoids reparsing bash arrays in Python. Bounded: a recipe should read its
        # own arrays in well under a second; a hang here (e.g. a PKGBUILD that
        # accidentally runs a network/blocking command at source time) would freeze
        # the whole stage with no output, so we cap it and move on.
        try:
            out = subprocess.run(
                ["bash", "-c", f'source "{pb}"; printf "%s\\n" "${{makedepends[@]}}" "${{depends[@]}}"'],
                capture_output=True, text=True, timeout=30,
            ).stdout
        except subprocess.TimeoutExpired:
            print(f"    [!] Reading deps from {d.name}/PKGBUILD timed out; continuing without them.")
            continue
        for tok in out.split():
            tok = tok.strip()
            if tok:
                want.add(tok)
    return sorted(want)


def _install_host_build_deps(sudo: list[str], deps: list[str], offline: bool) -> None:
    """Install makedepends on the BUILD HOST so makepkg can compile. Skipped when
    offline (assumes a warm host / already-built packages)."""
    if offline or not deps:
        return
    print(f"    [+] Installing {len(deps)} build-host dependencies for makepkg...")
    # Teed so pacman's download/install lines reach compile-full.log in real time.
    rc = logstream.run_teed(sudo + ["pacman", "-S", "--needed", "--noconfirm", *deps])
    if rc != 0:
        # Non-fatal: makepkg will still try and fail clearly if something's truly
        # missing. Some listed deps are runtime-only and may not be needed to build.
        print("    [!] Some build-host deps failed to install; continuing (makepkg will verify).")


def _import_librewolf_key(builder: str, sudo: list[str], offline: bool) -> None:
    """Import the LibreWolf release key into the BUILDER's gpg keyring so the
    librewolf recipe's detached-signature check passes. Fails closed online: if
    the key can't be fetched, the signature check would be skipped, so we abort."""
    if offline:
        return
    key = pkgbuild_cfg.LIBREWOLF_PGP_KEY
    print(f"    [+] Importing LibreWolf signing key {key[-8:]} from a keyserver...")
    # Run gpg AS the builder (its keyring is what makepkg checks). Bounded per
    # keyserver: gpg --recv-keys over hkps can hang for minutes on an unreachable
    # or slow keyserver with ZERO output -- that was a prime cause of the stage
    # sitting silent at "own packages". A timeout turns an unreachable keyserver
    # into a fast failover to the next one instead of an invisible stall.
    def as_builder(args: list[str]) -> int:
        full = ["sudo", "-u", builder, *args] if paths.is_root() else args
        try:
            return subprocess.run(full, timeout=90).returncode
        except subprocess.TimeoutExpired:
            print(f"    [!] gpg timed out after 90s; trying the next keyserver.")
            return 1

    for ks in ("hkps://keyserver.ubuntu.com", "hkps://keys.openpgp.org"):
        print(f"    [+]   contacting {ks} ...")
        if as_builder(["gpg", "--keyserver", ks, "--recv-keys", key]) == 0:
            print(f"    [+] Imported LibreWolf signing key {key[-8:]} from {ks}.")
            return
    raise MakepkgError(
        f"Could not import the LibreWolf signing key {key} from any keyserver.\n"
        "    The signature check would be bypassed, so the build fails closed.\n"
        "    Check network/keyservers, or use --full-compile (source build, no tarball sig)."
    )


def build_own_packages(offline: bool, full_compile: bool, progress: ProgressCb,
                        phase: Callable[[str], None] = lambda _s: None) -> None:
    """Emit our recipes, build them with makepkg, and drop the packages into the
    offline repo. Idempotent: an already-built package is not rebuilt."""
    sudo = _sudo()
    pkg_repo = paths.PKG_REPO
    pkg_repo.mkdir(parents=True, exist_ok=True)
    names = produced_names(full_compile)

    # calamares is compiled from source in BOTH tiers; the tier only changes how
    # librewolf is produced (from-source vs repackage the verified upstream tarball).
    tier = ("full-compile (calamares + librewolf from source)" if full_compile
            else "default (calamares from source, librewolf repackaged)")
    print(f"[*] Building Azzio's own packages -- tier: {tier}")
    phase(f"own packages: {tier}")
    progress(20)

    if offline:
        if not full_compile:
            # DEFAULT tier, offline: the own packages are deterministic cached
            # artifacts (calamares from a pinned source, librewolf repackaged from a
            # verified tarball). Present AND built from the CURRENT recipe -> SKIP
            # makepkg (the fast rerun the user wants). A recipe change (e.g. a new
            # calamares patch) flips the fingerprint so this is False and we fall
            # through to the loud error, which sends the next run online to rebuild --
            # cache_is_complete() uses the same _repo_is_current check, so an offline
            # run only ever reaches here when the recipe is unchanged; a changed recipe
            # is demoted to online BEFORE this function is called offline.
            if _repo_is_current(pkg_repo, full_compile):
                print("    [+] Own packages already present in the offline repo -- skipping makepkg.")
                progress(1000)
                return
            raise MakepkgError(
                f"Offline build but the built package(s) {', '.join(names)} are absent or\n"
                "    were built from an OLDER recipe (their recipe fingerprint no longer\n"
                "    matches). Re-run online (FORCE_ONLINE=1) so makepkg rebuilds them from\n"
                "    the current recipe, or wipe cache/ (or `git clean -Xdf`).\n"
                "    (An incomplete/stale cache already forces an online run automatically;\n"
                "    this fires only if an offline run is forced past that demotion.)"
            )
        # FULL tier, offline: the user asked for a from-source rerun to actually
        # RE-COMPILE, not trust the cached package. Rebuild librewolf (and calamares)
        # from the sources the prior ONLINE run fetched into the makepkg scratch --
        # entirely offline. Do NOT skip, do NOT wipe the scratch (the fetched Firefox
        # tree lives there), do NOT re-fetch (the recipe's `make fetch` is gated off
        # by AZZIO_OFFLINE and makepkg is told --noextract so it reuses the tree).
        scratch = paths.CACHEDIR / "makepkg"
        if not _scratch_has_sources(scratch, full_compile=True):
            raise MakepkgError(
                "Offline --full-compile rerun but the cached makepkg source tree is\n"
                f"    missing or empty under {scratch}. The prior online run's fetched\n"
                "    Firefox/bsys6 sources are gone (e.g. cache/ was cleared). Re-run once\n"
                "    online (FORCE_ONLINE=1) to refetch, or wipe cache/ to rebuild fresh."
            )
        print("    [+] --full-compile offline: recompiling from cached sources (no network).")
        phase("own packages: offline recompile")
        _offline_full_recompile(scratch, pkg_repo, progress, phase)
        return

    phase("own packages: preparing recipes")
    print("    [+] Preparing makepkg scratch tree and emitting recipes...")
    scratch = paths.CACHEDIR / "makepkg"
    if scratch.exists():
        _run(sudo + ["rm", "-rf", str(scratch)], check=False)
    scratch.mkdir(parents=True, exist_ok=True)

    dirs = _emit_recipes(scratch, full_compile)
    print(f"    [+] Emitted {len(dirs)} recipe(s): {', '.join(d.name for d in dirs)}.")
    progress(80)

    builder = _ensure_builder_user()
    phase("own packages: collecting build deps")
    print("    [+] Reading makedepends/depends from the recipes...")
    deps = _collect_makedepends(dirs)
    phase("own packages: installing build deps")
    _install_host_build_deps(sudo, deps, offline)
    progress(200)

    phase("own packages: importing signing key")
    _import_librewolf_key(builder, sudo, offline)
    progress(260)

    # The builder must own the scratch tree to write src//pkg/ during makepkg.
    if paths.is_root():
        _run(["chown", "-R", f"{builder}:{builder}", str(scratch)], check=True)

    _build_recipe_dirs(builder, dirs, pkg_repo, progress, phase,
                       offline=False, full_compile=full_compile)

    progress(1000)
    print("[✓] Azzio's own packages built and staged into the offline repo.")


def _build_recipe_dirs(builder: str, dirs: list[Path], pkg_repo: Path,
                       progress: ProgressCb, phase: Callable[[str], None],
                       offline: bool, full_compile: bool) -> None:
    """Build each recipe dir with makepkg, copy the resulting *.pkg.tar.zst into
    the offline repo, and hand the repo back to the invoking user. Shared by the
    online build tail and the offline --full-compile recompile; only the makepkg
    invocation differs (offline adds --noextract/--nocheck + AZZIO_OFFLINE so it
    reuses the already-fetched scratch tree and never touches the network).

    `full_compile` names the tier so the recipe fingerprints stamped below reflect
    the recipe set actually being built (it is NOT inferable from `offline`: the
    online path can be either tier, while the offline recompile is always full)."""
    # Fingerprint of the recipe that produces each package NOW, keyed by dir/pkg
    # name -- stamped next to each built package so a later run can detect a recipe
    # change and rebuild instead of reusing a stale binary.
    fingerprints = _current_recipe_fingerprints(full_compile=full_compile)
    total = len(dirs)
    for i, d in enumerate(dirs):
        name = d.name
        phase(f"makepkg: building {name}")
        print(f"[*] makepkg: building {name} ({i + 1}/{total})...")
        _makepkg_one(builder, d, offline=offline)
        # copy the freshly built package(s) into the offline repo
        built = sorted(d.glob("*.pkg.tar.zst"))
        if not built:
            raise MakepkgError(f"makepkg produced no package for {name} in {d}")
        for pkgfile in built:
            shutil.copy2(pkgfile, pkg_repo / pkgfile.name)
            print(f"    [+] {pkgfile.name} -> offline repo")
        # Record which recipe built this package (see _repo_is_current), in the
        # dedicated fingerprint dir (NOT pkg_repo -- that gets staged into the ISO).
        # Keyed by the recipe dir name; a name absent from the map (should not happen
        # -- dirs come from the same recipe_dirs) simply gets no stamp and is rebuilt
        # next run.
        if name in fingerprints:
            _write_recipe_fingerprint(_fingerprint_dir(), name, fingerprints[name])
        progress(260 + (i + 1) * 700 // total)

    # hand the repo back to the invoking user (parity with packages.build_cache).
    own_uid = os.environ.get("HOST_UID") or str(os.getuid())
    own_gid = os.environ.get("HOST_GID") or str(os.getgid())
    _run(_sudo() + ["chown", "-R", f"{own_uid}:{own_gid}", str(pkg_repo)], check=False)


def _scratch_has_sources(scratch: Path, full_compile: bool) -> bool:
    """True iff every recipe dir under scratch exists with a PKGBUILD AND a
    NON-EMPTY .build tree. The .build tree (BUILDDIR in _makepkg_one) is where
    makepkg extracts $srcdir and where the librewolf recipe's `make fetch` wrote
    the Firefox source on the prior ONLINE run -- so its presence is the real
    "sources are cached, an offline recompile can succeed" signal. SRCDEST (the
    upstream-tarball cache) now lives OUTSIDE the scratch (paths.MAKEPKG_SRC_CACHE)
    and holds only fetched source=() tarballs, so it is NOT what we check here.
    Missing or empty -> False -> the offline-recompile caller fails loudly instead
    of silently going online. Pure given the filesystem; unit-tested with tmp_path."""
    for dirname, _files in pkgbuild_cfg.recipe_dirs(full_compile):
        d = scratch / dirname
        if not (d / "PKGBUILD").is_file():
            return False
        build_dir = d / ".build"
        if not build_dir.is_dir() or not any(build_dir.iterdir()):
            return False
    return True


def _offline_full_recompile(scratch: Path, pkg_repo: Path, progress: ProgressCb,
                            phase: Callable[[str], None]) -> None:
    """Rebuild each recipe from its already-populated scratch dir, entirely offline.
    Unlike the online path it does NOT emit recipes, install host deps, import
    signing keys, or wipe the scratch -- it reuses exactly what the prior online run
    fetched. Only the per-dir makepkg/copy loop runs, with offline=True so makepkg
    reuses the extracted tree (--noextract) and the recipe skips `make fetch`."""
    pkg_repo.mkdir(parents=True, exist_ok=True)
    dirs = [scratch / dirname for dirname, _files
            in pkgbuild_cfg.recipe_dirs(full_compile=True)]
    builder = _ensure_builder_user()
    # The builder must own the scratch tree to write into it during makepkg.
    if paths.is_root():
        _run(["chown", "-R", f"{builder}:{builder}", str(scratch)], check=True)
    progress(260)
    # The offline recompile path is the --full-compile rerun, so the tier is full.
    _build_recipe_dirs(builder, dirs, pkg_repo, progress, phase,
                       offline=True, full_compile=True)
    progress(1000)
    print("[✓] Azzio's own packages recompiled offline and staged into the repo.")


def _makepkg_one(builder: str, recipe_dir: Path, offline: bool = False) -> None:
    """Run makepkg in recipe_dir as the unprivileged builder. -f force rebuild,
    -c clean, --skippgpcheck NOT passed (sig checks must run for librewolf); -s
    would auto-install deps via sudo which the builder lacks, so deps were
    installed on the host already and we pass --nodeps=False by omitting -s and
    relying on the host having them.

    offline: an offline --full-compile RERUN. Then makepkg MUST NOT re-fetch or
    re-extract: --noextract makes it reuse the ALREADY-extracted $srcdir tree
    (the bsys6 checkout plus the Firefox source `make fetch` pulled into it last
    run) and just re-run build()+package(). Without --noextract, `makepkg -f`
    would re-extract the source=() array -- re-checking-out librewolf-bsys6 and
    DESTROYING that fetched Firefox tree -- and then build() (whose `make fetch`
    is gated off by AZZIO_OFFLINE) would have no source. --nocheck skips the
    (absent) check() phase. AZZIO_OFFLINE=1 is read by the recipe's build() to
    skip `make fetch`. On the default/online path (offline=False) none of this
    applies and the invocation is byte-identical to before."""
    # --holdver: don't let makepkg bump pkgver from VCS. --noconfirm: unattended.
    cmd = ["makepkg", "-f", "--noconfirm", "--needed", "--noprogressbar"]
    if offline:
        cmd += ["--holdver", "--noextract", "--nocheck"]
    env = dict(os.environ)
    # Keep the user's ~/.local toolchain shims (uv/pipx/pyenv) out of the build PATH
    # so cmake's find_package(Python ...) resolves the SYSTEM interpreter, not a
    # stray user-local one (see _sanitize_build_path for the full rationale).
    env["PATH"] = _sanitize_build_path(env.get("PATH", ""), env.get("HOME", ""))
    # Point makepkg at a retry-hardened copy of the system makepkg.conf so a flaky
    # source download (mid-stream HTTP/2 reset, slow-crawl stall) is retried instead
    # of aborting the whole build (see _harden_dlagents). Written into recipe_dir,
    # which is chowned to the builder below, so the unprivileged makepkg can read it.
    # On the offline rerun no source is fetched, but passing --config is harmless
    # (makepkg reads the same settings) so we do it unconditionally for one code path.
    hardened_conf = _write_hardened_conf(recipe_dir)
    if hardened_conf is not None:
        cmd += ["--config", str(hardened_conf)]
    if offline:
        env["AZZIO_OFFLINE"] = "1"  # recipe build() skips `make fetch` when set
    # Cap compile parallelism so a build does not pin every core (see build_jobs).
    # Three env vars because the compilers pick up the limit three different ways:
    #   MAKEFLAGS  -> GNU make (and cmake's Makefiles generator / Firefox's mach,
    #                 which both forward it) sees `-j N`;
    #   NPROC      -> makepkg's own core-count knob, used by some check()/build()
    #                 helpers and honoured by makepkg when computing defaults;
    #   AZZIO_JOBS-> the explicit `-j"${AZZIO_JOBS:-1}"` our recipes append to
    #                 `cmake --build` / `make build`, which is the belt-and-braces
    #                 guarantee for build systems (Ninja, cargo) that ignore
    #                 MAKEFLAGS. Default of 1 in the recipe keeps them safe even if
    #                 this var somehow doesn't propagate.
    jobs = build_jobs()
    env["MAKEFLAGS"] = f"-j{jobs}"
    env["NPROC"] = str(jobs)
    env["AZZIO_JOBS"] = str(jobs)
    # Keep makepkg's build/cache under the scratch dir, not the builder's $HOME,
    # so a root build doesn't scatter files and offline reruns are clean.
    env["PKGDEST"] = str(recipe_dir)
    # SRCDEST is the PERSISTENT source-tarball cache (paths.MAKEPKG_SRC_CACHE), a
    # sibling of the scratch that the per-build `rm -rf` never touches -- so a
    # tarball fetched once (e.g. calamares' Codeberg release) is reused on every
    # later run and makepkg skips the download when it still matches the pinned
    # sha256. Per-recipe subdir keeps each package's sources separate. BUILDDIR
    # stays under the scratch: it is disposable extract/build space, not a cache.
    src_dir = paths.MAKEPKG_SRC_CACHE / recipe_dir.name
    build_dir = recipe_dir / ".build"
    env["SRCDEST"] = str(src_dir)
    env["BUILDDIR"] = str(build_dir)
    src_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(exist_ok=True)

    if paths.is_root():
        # These dirs are created here as ROOT, AFTER build_own_packages' one-shot
        # chown of the scratch tree already ran, so they're root-owned. makepkg
        # runs as the unprivileged builder below and would abort with "You do not
        # have write permission for the directory $BUILDDIR". Chown them (and the
        # recipe dir itself, since PKGDEST is recipe_dir and makepkg writes the
        # built package there) to the builder before handing off.
        _run(["chown", "-R", f"{builder}:{builder}",
              str(recipe_dir), str(src_dir), str(build_dir)], check=True)
        # Re-exec as the builder, preserving the makepkg env vars. AZZIO_OFFLINE is
        # only present in env on the offline path, so the online envargs list is
        # unchanged (the key is simply absent).
        # MAKEFLAGS/NPROC/AZZIO_JOBS carry the parallelism cap (build_jobs); they
        # MUST be forwarded across the sudo -u builder re-exec or the root/container
        # build -- the very path that saturates all cores -- would drop the cap and
        # each compiler would fall back to its all-cores default again.
        keys = ("PKGDEST", "SRCDEST", "BUILDDIR", "MAKEFLAGS", "NPROC", "AZZIO_JOBS")
        if offline:
            keys += ("AZZIO_OFFLINE",)
        envargs = [f"{k}={env[k]}" for k in keys]
        full = ["sudo", "-u", builder, "env", *envargs, *cmd]
        # run_teed pumps the compile's stdout/stderr through the _Tee so the
        # multi-hour gcc/rustc output lands in compile-full.log in real time instead of
        # vanishing into the inherited PTY (whose `script` capture goes to /dev/null).
        rc = logstream.run_teed(full, cwd=str(recipe_dir))
    else:
        rc = logstream.run_teed(cmd, cwd=str(recipe_dir), env=env)

    if rc != 0:
        raise MakepkgError(f"makepkg failed for {recipe_dir.name} (exit {rc})")
