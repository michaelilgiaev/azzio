"""qvm source acquisition + ISO build wiring for the `qvm` command.

`qvm` (https://github.com/michaelilgiaev/qvm) is our per-directory QEMU/KVM VM runner:
run it INSIDE a directory and that directory IS the VM -- its name, disk, UEFI NVRAM,
shared folder and forwarded SSH port all derive from the folder you are in, with every
setting in a per-directory `hypervisor.cfg`. It used to live INLINE in azzio as
libraries/packages/hypervisor/; it is now its OWN project, and GitHub is the single
source of truth for BOTH azzio and codelis. codelis obtains it at install time
(install.sh install_qvm_from_github: clone -> Nuitka build -> /usr/bin/qvm); azzio
obtains it HERE, at ISO-build time, and bakes it into the image.

THE DIFFERENCE FROM codelis: azzio ships `qvm` as plain Python SOURCE baked into the
airootfs (a flat dir of modules + a launcher), exactly as it shipped the old inline
`hypervisor` -- NOT the Nuitka binary codelis builds. qvm's app is Python standard
library only and runs fine flat, so there is nothing to compile into the ISO (keeping
nuitka/gcc out of the mkarchiso pipeline). The OFFLINE Calamares install rsyncs the
live rootfs, so the baked files carry onto the installed system unchanged.

Two responsibilities, mirroring the old packages/hypervisor/packaging.py contract so
compiler.py drives it the same way (iterate emit_plan(), write each entry):

  1. ACQUIRE the source -- ensure_checkout() puts a qvm checkout under azzio's
     persistent cache/ (cache/qvm) and returns its libraries/ dir. Cache-first like
     the package cache: a checkout already present is REUSED untouched (so a warm or
     fully-offline rebuild contacts no server); only a cold cache triggers a `git
     clone`. The remote + ref are overridable (QVM_REPO_URL / QVM_REPO_REF) for a
     local mirror or a pinned build; AZZIO_QVM_DIR points the whole thing at an
     existing checkout (air-gapped / CI) and skips git entirely.

  2. EMIT -- emit_plan() returns the declarative [{builder,dest,mode}] list compiler.py
     writes into the airootfs: every qvm libraries/*.py flat into LIB_DIR, the
     /usr/bin/qvm launcher, and the qvm bash-completion. Same builder/dest/mode shape
     as the old packaging.emit_plan(), so the compiler's write loop is unchanged.

Installed layout (root-owned; /usr so the OFFLINE install's unpackfs rsync carries it):
    /usr/lib/qvm/command_line_interface.py       the `qvm` entry the launcher execs
    /usr/lib/qvm/<module>.py                      every qvm runtime module (flat)
    /usr/bin/qvm                                  the launcher (execs the entry, NO cd)
    /usr/share/bash-completion/completions/qvm    the qvm bash completion

Runtime dependencies (system binaries qvm shells out to): `qemu-system-x86_64` /
`qemu-img` (qemu-full), the OVMF UEFI firmware (edk2-ovmf), `remote-viewer`
(virt-viewer), and `pgrep` (procps-ng, in base) -- all named in the manifest
(packages.x86_64), unchanged from when `hypervisor` shipped inline. python itself is
already present; everything else qvm uses is Python standard library.
"""

from __future__ import annotations

import os
import subprocess

import paths

# --- Source acquisition ------------------------------------------------------
# The upstream repo + ref azzio bakes into the ISO. Overridable, matching codelis's
# QVM_REPO_URL knob so both projects point at the same baby (and both can be aimed at a
# local mirror for an air-gapped build). QVM_REPO_REF pins the build to a tag/branch/commit;
# it defaults to the repo's default branch. Keeping these as env overrides (not hardcoded)
# means a pinned or mirrored build needs no edit here.
QVM_REPO_URL = os.environ.get("QVM_REPO_URL", "https://github.com/michaelilgiaev/qvm.git")
QVM_REPO_REF = os.environ.get("QVM_REPO_REF", "")  # "" => clone the remote's default branch

# Where the checkout lands: under azzio's persistent cache/ (git-ignored, bind-mounted in
# Docker, reused across builds) -- the SAME store the package cache uses, so a warm rebuild
# reuses it and a fully-offline rebuild (AZZIO_OFFLINE, with cache/qvm already warmed by a
# prior online run) never needs the network. Overridable with AZZIO_QVM_DIR to point at an
# existing checkout (air-gapped / CI), which skips git entirely.
QVM_CHECKOUT = paths.CACHEDIR / "qvm"

# HARD timeout (seconds) on EVERY git network call (clone / fetch). THE reason this exists:
# the clone runs mid-build (compiler step 8, _emit_desktop) UNDER the pinned progress bar and
# BEFORE the build has resolved mirrors / the offline switch (step 11). A bare
# subprocess.run() with no timeout would block FOREVER on a stalled connect, a half-open TLS
# handshake, or a throttling GitHub -- and because the bar is pinned with no heartbeat for
# this step, that hang is INVISIBLE: it looks exactly like "the build is idle / frozen".
# Bounding every git call turns a network stall into a FAST, explicit QvmSourceError (caught
# by compiler.main) instead of a silent hang. Overridable for a slow link / a huge mirror;
# the default is generous for a shallow single-branch clone of a small pure-Python repo.
# git also gets GIT_HTTP_LOW_SPEED_* (see _git_env) so a mid-transfer CRAWL aborts too -- the
# subprocess timeout covers a dead-stop connect, the low-speed limit covers a trickle.
_CLONE_TIMEOUT = int(os.environ.get("AZZIO_QVM_CLONE_TIMEOUT", "180"))


def _is_offline(offline: "bool | None" = None) -> bool:
    """Whether this build is fully offline, so a qvm clone would be a pointless doomed fetch.

    The AUTHORITATIVE signal is the `offline` argument compiler.py threads in -- the SAME
    boolean the rest of the build uses (offline = cache_is_complete(), passed to build_cache).
    emit_plan()/ensure_checkout() forward it here so the qvm fetch fails fast on an offline
    rebuild with a cold cache/qvm, exactly like the package cache does, instead of burning the
    whole _CLONE_TIMEOUT on a connect that cannot succeed.

    When it is None (the no-arg emit_plan() contract the old inline packaging kept, and the
    unit tests), fall back to the AZZIO_OFFLINE env var so an operator can still force the
    fail-fast by hand. NB: azzio sets AZZIO_OFFLINE only for makepkg's CHILD process (a later
    build step), NOT in this process before the desktop emit -- so in the real build the
    `offline` ARGUMENT is what makes this reachable; the env var is just a manual override.
    Truthiness mirrors the C side's AZZIO_FORCE_LIVE (1/true/yes/on)."""
    if offline is not None:
        return bool(offline)
    return os.environ.get("AZZIO_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


class QvmSourceError(RuntimeError):
    """The qvm source could not be obtained (no checkout and the clone failed). Unlike
    codelis -- where qvm is optional and a failure only WARNS -- azzio CANNOT build the
    ISO without it (there would be no `qvm` command baked in), so this is fatal."""


def _checkout_libraries(root) -> "paths.Path":
    """The libraries/ dir inside a qvm checkout root (where its flat modules + completion
    live). Factored out so ensure_checkout() and the override path agree on the layout."""
    return root / "libraries"


def ensure_checkout(offline: "bool | None" = None):
    """Return the libraries/ dir of a usable qvm checkout, obtaining it if needed.

    offline is the build-wide offline signal compiler.py threads in (offline =
    cache_is_complete()); None falls back to the AZZIO_OFFLINE env var (see _is_offline).

    Resolution order (cache-first, so a warm/offline rebuild contacts no server):
      1. AZZIO_QVM_DIR set -> use that checkout verbatim (air-gapped / CI). Git is never
         run; the dir MUST already contain libraries/command_line_interface.py.
      2. cache/qvm already a usable checkout -> REUSE it untouched (no fetch).
      3. offline + no warm checkout + no override -> fail fast (a clone can only fail).
      4. otherwise -> `git clone` QVM_REPO_URL (at QVM_REPO_REF if pinned) into cache/qvm.

    Raises QvmSourceError if, after all that, there is no usable checkout -- azzio needs
    the `qvm` source to bake into the ISO, so a miss is fatal (not a warning)."""
    override = os.environ.get("AZZIO_QVM_DIR")
    if override:
        root = paths.Path(override).expanduser().resolve()
        libs = _checkout_libraries(root)
        if not (libs / "command_line_interface.py").is_file():
            raise QvmSourceError(
                f"AZZIO_QVM_DIR={override} is not a qvm checkout "
                f"(missing {libs / 'command_line_interface.py'})."
            )
        return libs

    libs = _checkout_libraries(QVM_CHECKOUT)
    if (libs / "command_line_interface.py").is_file():
        # Warm cache: reuse the existing checkout untouched (offline-safe, resumable).
        return libs

    # No warm checkout and no override. If the build is OFFLINE, a clone can only reach the
    # network and fail -- so fail NOW with an actionable message instead of stalling for the
    # whole clone timeout on a connect that cannot succeed. (A warm cache/qvm from a prior
    # online run is the supported offline path; so is AZZIO_QVM_DIR.)
    if _is_offline(offline):
        raise QvmSourceError(
            f"offline build but no qvm checkout at {QVM_CHECKOUT} and no AZZIO_QVM_DIR "
            f"override -- qvm must be fetched online at least once. Run one online build to "
            f"warm cache/qvm, or set AZZIO_QVM_DIR to an existing checkout."
        )

    _clone(QVM_CHECKOUT)
    libs = _checkout_libraries(QVM_CHECKOUT)
    if not (libs / "command_line_interface.py").is_file():
        raise QvmSourceError(
            f"cloned qvm into {QVM_CHECKOUT} but {libs / 'command_line_interface.py'} is "
            f"missing -- the remote {QVM_REPO_URL} is not a qvm checkout."
        )
    return libs


def _git_env() -> dict:
    """The environment for the git network calls: inherit the caller's, then add
    GIT_HTTP_LOW_SPEED_LIMIT/-TIME so git's OWN http transport aborts a mid-transfer CRAWL
    (bytes still trickling, so the process is not 'stuck' and the subprocess timeout would
    not fire, yet the transfer makes no real progress). Together with the subprocess
    `timeout=` -- which catches a DEAD-STOP connect/handshake -- this covers both ways a
    clone can hang the build. 1024 B/s over 30 s mirrors the curl --speed-limit/--speed-time
    the package downloader uses (pacman._DOWNLOAD_XFERCOMMAND), so both fetch paths give up
    on a trickle at the same threshold. GIT_TERMINAL_PROMPT=0 makes a private/auth-required
    remote fail immediately instead of blocking on an (unanswerable, no-tty) credential
    prompt -- another silent-hang trap under the PTY re-exec."""
    env = dict(os.environ)
    env.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1024")
    env.setdefault("GIT_HTTP_LOW_SPEED_TIME", "30")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(cmd: list, *, what: str) -> subprocess.CompletedProcess:
    """Run one git command with the hardened env and the HARD _CLONE_TIMEOUT. A timeout is
    converted into a QvmSourceError (NOT a silent hang): this is the single most important
    behaviour of this module -- see _CLONE_TIMEOUT. `what` names the step for the message."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_CLONE_TIMEOUT, env=_git_env())
    except subprocess.TimeoutExpired:
        raise QvmSourceError(
            f"{what} timed out after {_CLONE_TIMEOUT}s talking to {QVM_REPO_URL} -- the "
            f"network (or GitHub) is unreachable or stalled. Fix connectivity and retry, run "
            f"one online build to warm cache/qvm, or set AZZIO_QVM_DIR to an existing "
            f"checkout. (Raise the limit with AZZIO_QVM_CLONE_TIMEOUT if the link is just slow.)"
        ) from None


def _clone(dest) -> None:
    """Shallow-clone QVM_REPO_URL into dest (at QVM_REPO_REF if set). Any stale/partial
    dir from a killed prior clone is removed first so the clone starts clean. git is in the
    build image (Dockerfile) and on a native Arch build host. Raises QvmSourceError with the
    git output on failure, OR a clear timeout error if the clone stalls (fatal -- see
    ensure_checkout / _CLONE_TIMEOUT)."""
    if not _have_git():
        raise QvmSourceError(
            "git is required to obtain qvm from GitHub but was not found on PATH "
            "(install git, or set AZZIO_QVM_DIR to an existing qvm checkout)."
        )
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], check=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1"]
    if QVM_REPO_REF:
        # --branch takes a branch OR a tag; for a raw commit the shallow clone + checkout
        # is done in two steps below.
        cmd += ["--branch", QVM_REPO_REF]
    cmd += [QVM_REPO_URL, str(dest)]
    print(f"[*] Obtaining qvm from {QVM_REPO_URL}"
          f"{f' @ {QVM_REPO_REF}' if QVM_REPO_REF else ''} -> {dest} "
          f"(timeout {_CLONE_TIMEOUT}s)...", flush=True)
    proc = _run_git(cmd, what="git clone of qvm")
    if proc.returncode != 0:
        # --branch rejects a raw commit SHA; retry as a full-ref fetch + checkout so a
        # pinned COMMIT (not just a tag/branch) still works.
        if QVM_REPO_REF and _try_clone_commit(dest):
            return
        raise QvmSourceError(
            f"git clone of {QVM_REPO_URL} failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )
    print("[✓] qvm source obtained.", flush=True)


def _try_clone_commit(dest) -> bool:
    """Fallback for a pinned raw COMMIT (which `git clone --branch` rejects): init, fetch
    just that commit, and check it out. Returns True on success, False so the caller raises
    the original clone error if this also fails. Every git step is bounded by _CLONE_TIMEOUT
    (a stalled fetch raises QvmSourceError rather than hanging the build)."""
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], check=False)
    dest.mkdir(parents=True, exist_ok=True)
    steps = [
        (["git", "-C", str(dest), "init", "-q"], "git init"),
        (["git", "-C", str(dest), "remote", "add", "origin", QVM_REPO_URL], "git remote add"),
        (["git", "-C", str(dest), "fetch", "-q", "--depth", "1", "origin", QVM_REPO_REF],
         "git fetch of qvm commit"),
        (["git", "-C", str(dest), "checkout", "-q", "FETCH_HEAD"], "git checkout"),
    ]
    for cmd, what in steps:
        if _run_git(cmd, what=what).returncode != 0:
            return False
    return True


def _have_git() -> bool:
    import shutil
    return shutil.which("git") is not None


# --- Installed system paths (root-owned) ------------------------------------
# Where qvm lands in the live/installed rootfs. Under /usr (NOT /usr/local) to match qvm's
# OWN documented install (sudo install -m 755 output/qvm /usr/bin/) and codelis's
# install.sh, so the on-PATH command is `/usr/bin/qvm` on every one of the three projects.
# The OFFLINE install's unpackfs rsync carries /usr unchanged onto the target.
LIB_DIR = "/usr/lib/qvm"
# The CLI entry the launcher execs. It lands in LIB_DIR beside the other modules; its own
# `sys.path.insert(0, <dir of __file__>)` (see qvm's command_line_interface.py) makes the
# flat sibling imports (`import configuration`, `import virtual_machine`, ...) resolve from
# wherever it is run, so the launcher needs no PYTHONPATH.
ENTRY_SYSTEM_PATH = f"{LIB_DIR}/command_line_interface.py"
# The bin entry point on PATH -- the actual `qvm` command. A tiny wrapper that execs the
# system python on the entry's ABSOLUTE path in LIB_DIR WITHOUT changing directory. The
# no-cd is LOAD-BEARING: `qvm` derives the whole VM identity from the caller's CURRENT
# WORKING DIRECTORY (Config.from_cwd()), so the launcher MUST preserve it -- a `cd` into
# LIB_DIR would make every VM resolve to LIB_DIR. Ships 0o755 (see profile.py
# file_permissions -- archiso would otherwise normalise it to 0644 on the squashfs).
LAUNCHER_SYSTEM_PATH = "/usr/bin/qvm"

# The bash completion for `qvm`, shipped to the SYSTEM completion dir (root-owned, under
# /usr/share so the OFFLINE install's unpackfs rsync carries it to the target). The
# bash-completion lazy loader looks up a file named after the command on first TAB, so the
# basename MUST be exactly `qvm`. The source is completion.bash in qvm's libraries/ (a DATA
# file, not a .py, so _shipped_module_names() does not sweep it into LIB_DIR). The
# `bash-completion` package (the loader, sourced by /etc/bash.bashrc) is named in the manifest.
COMPLETION_SOURCE_NAME = "completion.bash"
COMPLETION_SYSTEM_PATH = "/usr/share/bash-completion/completions/qvm"

# --- Which source files ship -------------------------------------------------
# qvm's app is a flat directory, so we ship every .py in its libraries/. qvm_main.py (its
# Nuitka freeze entry) is harmless to ship but never used in the ISO -- the launcher execs
# command_line_interface.py directly, exactly as the old inline `hypervisor` launcher did.
# Discovering the set (rather than listing each module) means a module added or removed
# upstream needs no edit here.


def _shipped_module_names(libs) -> list[str]:
    """Every .py file qvm ships to LIB_DIR (sorted): the whole qvm libraries/ dir. The
    entry (command_line_interface.py) and every working module travel together so the flat
    sibling imports resolve. completion.bash is a data file (not .py) so it is excluded here
    and shipped only via its own emit_plan() entry."""
    return sorted(
        p.name
        for p in libs.iterdir()
        if p.is_file() and p.suffix == ".py"
    )


class _FileBuilder:
    """A zero-arg builder that reads `path`'s text verbatim on each call (late, so the
    bytes always match the checkout). Two builders for the same path compare EQUAL (keyed on
    the path) so emit_plan() is a pure function whose repeated results are equal -- the same
    contract the old packaging._ModuleBuilder gave compiler.py."""

    def __init__(self, path) -> None:
        self.path = paths.Path(path)

    def __call__(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FileBuilder) and other.path == self.path

    def __hash__(self) -> int:
        return hash(self.path)


def launcher_sh() -> str:
    """The `qvm` launcher installed on PATH.

    Execs the system python on the entry's ABSOLUTE path in LIB_DIR, forwarding any
    arguments (so `qvm install foo.iso` / `qvm -h` reach the script). It deliberately does
    NOT `cd` -- `qvm` derives the whole VM identity from the caller's CURRENT WORKING
    DIRECTORY, so the caller's cwd MUST be preserved (a `cd` into LIB_DIR would make every VM
    resolve to LIB_DIR; this is the port's single most important correctness point). The
    sibling imports still resolve without the `cd` because command_line_interface.py does
    `sys.path.insert(0, <dir of __file__>)` at startup, keyed off the script's own absolute
    path rather than the cwd. `exec` so the python process replaces the shell (clean signals
    -- qvm installs SIGTERM/SIGINT handling around the viewer/VM). `"$@"` is quoted so
    arguments with spaces survive."""
    return f"""\
#!/bin/sh
# qvm -- run a per-directory QEMU/KVM VM (the directory you are in IS the VM).
# Generated by azzio's libraries/qvm_source.py (edit the Python, not this file).
# The source is baked from GitHub (michaelilgiaev/qvm) at ISO-build time.
# The caller's working directory is preserved (deliberately NOT changed) so the VM
# identity resolves against it; the entry does sys.path.insert for its sibling imports.
exec python -u '{ENTRY_SYSTEM_PATH}' "$@"
"""


# --- Emit plan --------------------------------------------------------------
# Declarative list (builder -> dest -> mode), the SAME shape the old packaging.emit_plan()
# returned, so compiler.py iterates it unchanged. All absolute SYSTEM paths (root-owned):
# the OFFLINE Calamares install rsyncs the live rootfs, so these carry onto the installed
# system unchanged. Every qvm .py ships as its own entry (0644) into LIB_DIR, plus the
# launcher (0755) on PATH and the completion (0644). No systemd service (interactive command).
_EXEC = 0o755
_CONF = 0o644


def emit_plan(offline: "bool | None" = None) -> list[dict]:
    """Return the emit plan (builder/dest/mode) for compiler.py to write into the airootfs:
    one entry per qvm module file (into LIB_DIR), the /usr/bin/qvm launcher, and the qvm
    bash-completion. Obtains the qvm checkout first (ensure_checkout(), cache-first), so the
    builders read from a guaranteed-present source tree. Built fresh each call (compiler.py
    may call this more than once per build), so a mutated returned entry can never corrupt
    module state.

    offline is forwarded to ensure_checkout() so an offline rebuild with a cold cache/qvm
    fails fast instead of attempting a doomed clone (None -> AZZIO_OFFLINE env fallback). The
    no-arg call is still valid -- the old inline packaging.emit_plan() took no args and the
    unit tests call it bare."""
    libs = ensure_checkout(offline)
    plan = [
        {"builder": _FileBuilder(libs / name), "dest": f"{LIB_DIR}/{name}", "mode": _CONF}
        for name in _shipped_module_names(libs)
    ]
    plan.append({"builder": _FileBuilder(libs / COMPLETION_SOURCE_NAME),
                 "dest": COMPLETION_SYSTEM_PATH, "mode": _CONF})
    plan.append({"builder": launcher_sh, "dest": LAUNCHER_SYSTEM_PATH, "mode": _EXEC})
    return plan
