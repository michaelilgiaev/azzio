"""virtual_machine.py - the subcommand logic: install / run / share / status / stop.

Ported from libraries/install.sh, libraries/run.sh, libraries/share.sh and the
status/stop halves of hypervisor.sh. The QEMU command carries the smooth-1080p
config: virtio-vga-gl seeded with a 1920x1080 EDID mode, egl-headless GL offload
on the host GPU, SPICE with streaming-video/playback-compression off, and the
remote-viewer window opened full-screen. SPICE stays gl=off in every branch
(gl=on black-screens the NVIDIA driver at runtime; egl-headless does not).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time

# Flat app, dual-mode sibling import (see configuration.py for the full rationale): use
# package-relative imports when loaded as packages.hypervisor.virtual_machine (the test suite)
# so the sibling modules (and their HypervisorError class) are the SAME objects the tests
# hold, and a sys.path bootstrap + bare imports when loaded flat by absolute path (via the
# launcher, which execs command_line_interface.py). Mirrors packages/backup/archive.py's bootstrap.
if __package__:
    from . import checks
    from . import configuration_watcher
    from .checks import die, is_running
    from .configuration import (
        Config, HypervisorCfg, _CFG_DEFAULTS, _hypervisor_cfg_text, select_ssh_port,
    )
    from .graphics import select_render_node
    from .qemu_command import build_qemu_argv  # re-exported: do_run + tests use vm.build_qemu_argv
else:  # loaded flat (run by absolute path via the launcher) -- no parent package
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import checks  # noqa: E402  (after the sys.path bootstrap above)
    import configuration_watcher  # noqa: E402
    from checks import die, is_running  # noqa: E402
    from configuration import (  # noqa: E402
        Config, HypervisorCfg, _CFG_DEFAULTS, _hypervisor_cfg_text, select_ssh_port,
    )
    from graphics import select_render_node  # noqa: E402
    from qemu_command import build_qemu_argv  # noqa: E402  re-exported



def _envflag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default) == "1"


# --- virtiofs shared folder --------------------------------------------------
def virtiofsd_argv(cfg: Config, socket_group: "str | None" = None) -> list[str]:
    """The virtiofsd daemon command that exports the host share dir on the VM's
    vhost-user socket, or [] when shared is off. PURE (no spawn) so it can be pinned
    in tests. Depends ONLY on cfg.shared_path -- NOT on ssh -- which is half the
    fix: the share works on every variant because the daemon runs whenever shared
    is set, not as a side effect of the ssh bring-up.

    Run as ROOT via sudo. This is the OTHER half of the fix. virtiofsd creates host
    files on the guest's behalf by setfsuid()/setfsgid()-ing to the guest's
    credentials first; only root may do that, so an UNPRIVILEGED daemon lets the
    guest READ the share but silently EPERMs every create/mkdir -- a regression from
    the old 9p share (9p ran inside QEMU as the user and needed no daemon). Running
    root fixes creation; files land on the host owned by the invoking user.

    --sandbox=none keeps the daemon in the host mount namespace (the default sandbox
    would pivot_root INTO the shared dir); the socket is a per-VM dotfile so two VMs
    never collide. --socket-group hands the root-owned socket to the invoking user's
    primary group so the (non-root) QEMU can still open it (a root-owned socket is
    srwx------ root -- unreachable otherwise); omitted when the group is unknown, and
    the caller then chmods the socket instead."""
    path = cfg.shared_path
    if not path:
        return []
    # Resolve the binary, but fall back to the bare name so this stays a PURE,
    # host-independent builder: on a machine without virtiofsd installed (a CI
    # runner, say) checks.virtiofsd_binary() returns '' and would otherwise emit
    # ["sudo", "", ...] -- an invalid command with an empty argv slot. The bare
    # "virtiofsd" keeps the command well-formed for pinning; whether the daemon
    # is actually present is enforced separately by require_virtiofsd() before
    # any real spawn.
    binary = checks.virtiofsd_binary() or "virtiofsd"
    argv = [
        "sudo",
        binary,
        f"--socket-path={cfg.virtiofs_sock}",
        f"--shared-dir={path}",
        "--sandbox=none",
    ]
    if socket_group:
        argv.append(f"--socket-group={socket_group}")
    return argv


def _primary_group() -> "str | None":
    """The invoking user's primary group NAME, for virtiofsd --socket-group. None if
    it cannot be resolved (a bare uid with no /etc/group entry) -- the caller then
    falls back to chmod-ing the socket after the daemon creates it."""
    try:
        import grp
        return grp.getgrgid(os.getgid()).gr_name
    except (KeyError, OSError):
        return None


def _guest_fstab_line(guest_user: str) -> str:
    """The /etc/fstab line that auto-mounts the share inside the guest, via virtiofs.
    PURE. virtiofs needs no trans=/version= options and no modules-load entry (the
    driver is in-tree in modern kernels), so this is a plain virtiofs entry with
    'nofail' so a VM booted without the share still boots. The source field is the
    mount tag "shared" advertised by the vhost-user-fs device (an opaque host<->guest
    identifier -- it stays lowercase; only the on-disk dir / mountpoint is "Shared")."""
    return f"shared  /home/{guest_user}/Shared  virtiofs  nofail  0 0"


# --- install -----------------------------------------------------------------
def do_install(cfg: Config, iso_arg: str,
               shared: bool = False,
               ssh: bool = False,
               share_host_gpu: bool = False,
               ssh_port: str = "") -> None:
    """Create disk + UEFI NVRAM + shared folder + hypervisor.cfg. Does not boot.
    Run 'hypervisor run <disk> --iso <iso>' to boot the installer afterward.
    The ISO argument is mandatory. (USB passthrough is not an install flag -- it
    is a list of device paths edited in hypervisor.cfg after install.)"""
    checks.require_writable_dir(cfg)
    checks.require_qemu()
    checks.require_ovmf(cfg)
    iso = cfg.resolve_iso(iso_arg)
    if not os.access(iso, os.R_OK):
        die(f"ISO not readable: {iso}")
    checks.require_free_space(cfg)

    # --- hypervisor.cfg: write all defaults + requested toggle overrides -----
    # vals holds COERCED values (bools/ints/lists); the generator renders them.
    vals = dict(_CFG_DEFAULTS)
    vals["shared"] = True if shared else False  # True == share the working dir
    vals["ssh"] = ssh
    # share_host_gpu defaults ON; only a passed --share-host-gpu is redundant,
    # but honour the flag so it never turns the default off.
    if share_host_gpu:
        vals["share_host_gpu"] = True
    if ssh_port:
        vals["ssh_guest_to_host_port_forward"] = int(ssh_port)
    hcfg_path = HypervisorCfg.write(cfg.dir, vals)
    print(f"Config: {hcfg_path}")

    # --- disk: create only when missing; guard FORCE wipes -------------------
    if os.path.isfile(cfg.disk):
        if _envflag("FORCE"):
            used = _du_bytes(cfg.disk)
            if used > 1024 * 1024 * 1024 and not _envflag("YES"):
                die(
                    f"refusing FORCE wipe: {cfg.disk} holds ~{used // 1024 // 1024} MiB. "
                    "Re-run with FORCE=1 YES=1 to confirm."
                )
            _qemu_img_create(cfg)
            print(f"Recreated {cfg.disk_size} disk (wiped): {cfg.disk}")
        else:
            print(f"Disk exists, keeping it: {cfg.disk}  (FORCE=1 to wipe)")
    else:
        _qemu_img_create(cfg)
        print(f"Created {cfg.disk_size} disk: {cfg.disk}")

    # --- UEFI NVRAM: copy only when missing ----------------------------------
    if os.path.isfile(cfg.vars):
        print(f"UEFI NVRAM kept: {cfg.vars}")
    else:
        shutil.copyfile(cfg.vars_tmpl, cfg.vars)
        print(f"UEFI NVRAM ready: {cfg.vars}")

    # --- shared folder (only when enabled) -----------------------------------
    if shared:
        os.makedirs(cfg.shared, exist_ok=True)
        os.chmod(cfg.shared, 0o755)
        print(f"Shared folder ready: {cfg.shared}")

    print()
    print(f"Ready. Boot the installer with:  "
          f"hypervisor run {os.path.basename(cfg.disk)} --iso {os.path.basename(iso)}")


def _qemu_img_create(cfg: Config) -> None:
    try:
        os.remove(cfg.disk)
    except FileNotFoundError:
        pass
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", cfg.disk, cfg.disk_size],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def _du_bytes(path: str) -> int:
    """Real bytes allocated on disk (like `du -B1`), for the FORCE-wipe guard."""
    try:
        return os.stat(path).st_blocks * 512
    except OSError:
        return 0


_EMPTY_DISK_FLOOR = 1024 * 1024  # < 1 MiB allocated == never installed


def _auto_install_iso(cfg: Config, requested: str, disk: str) -> str:
    """Decide which installer ISO to attach as a CD-ROM (or '').

    PURE. An explicit request (--iso / INSTALL_ISO=) always wins and is returned
    verbatim -- used to repair or reinstall an already-populated disk. With no
    request, an EMPTY (never-installed) disk falls back to the directory's single
    *.iso via cfg.find_iso(); a NON-empty disk, or zero-or-many ISOs, returns ''
    so a normal boot never surprise-attaches media.
    """
    if requested:
        return requested
    if _du_bytes(disk) >= _EMPTY_DISK_FLOOR:
        return ""
    return cfg.find_iso()


# --- run ---------------------------------------------------------------------
def do_run(cfg: Config, install_iso: str = "") -> None:
    """Assemble and launch the QEMU VM, then a remote-viewer window against it.
    Closing that window (or Ctrl-C) tears the whole VM down. cfg.disk is the
    already-resolved .qcow2 the caller demanded on the command line."""
    checks.require_writable_dir(cfg)
    checks.require_qemu()
    checks.require_ovmf(cfg)
    checks.require_kvm()
    checks.require_viewer()
    checks.require_not_running(cfg)

    hcfg = cfg.hcfg
    disk = cfg.disk
    if not os.path.isfile(cfg.vars):
        shutil.copyfile(cfg.vars_tmpl, cfg.vars)
    # Only auto-create the DEFAULT working ./Shared dir; a user-named custom path
    # is the user's own responsibility (we never mkdir an arbitrary host path).
    if hcfg.shared is True:
        os.makedirs(cfg.shared, exist_ok=True)
        os.chmod(cfg.shared, 0o755)
    # The shared folder rides virtiofs -> we need the virtiofsd daemon. Gate it here
    # (only when shared is on) so a plain non-shared VM never demands it.
    if cfg.shared_path:
        checks.require_virtiofsd()

    # --- GPU: shared host GPU (3D) vs a generic software-rendered GPU --------
    gpu_args = _gpu_args(cfg)

    # --- installer ISO as a SATA CD-ROM (repair / first-time install) --------
    # Explicit --iso (or INSTALL_ISO=) wins; otherwise an EMPTY (never-installed)
    # disk with exactly one *.iso in the dir auto-attaches it, so a fresh `run`
    # boots the installer instead of hanging at the UEFI shell.
    requested = install_iso or os.environ.get("INSTALL_ISO", "")
    iso = _auto_install_iso(cfg, requested, disk)
    iso_args: list[str] = []
    if iso and os.path.isfile(iso):
        iso_args = [
            "-device", "ich9-ahci,id=sata",
            "-drive", f"if=none,id=cd0,file={iso},media=cdrom,readonly=on",
            "-device", "ide-cd,drive=cd0,bus=sata.0,bootindex=2",
        ]
        if requested:
            print(f"Installer ISO attached: {os.path.basename(iso)}", file=sys.stderr)
        else:
            print(f"Disk is empty -- auto-attaching ISO: {os.path.basename(iso)}",
                  file=sys.stderr)
    elif iso:
        # a requested ISO (INSTALL_ISO=) that does not exist: warn, don't silently
        # boot with no media. (The CLI --iso path already dies via resolve_iso.)
        print(f"WARNING: requested ISO not found, booting without it: {iso}",
              file=sys.stderr)

    port = select_ssh_port(cfg) if hcfg.ssh else None
    _write_viewer_ask_quit(hcfg.ask_before_quitting_hypervisor)

    checks.require_not_running(cfg)
    _rm(cfg.spice_sock)

    qemu = build_qemu_argv(cfg, disk=disk, gpu_args=gpu_args, iso_args=iso_args, port=port)

    if _envflag("DRYRUN"):
        print(" ".join(_shquote(a) for a in qemu))
        return

    _launch(cfg, qemu, port)


def _gpu_args(cfg: Config) -> list[str]:
    """Display/GPU argv. share_host_gpu=on offloads guest GL onto a host DRM
    render node (shared, NOT passthrough -- the host screen keeps working);
    off (or no usable node) gives a generic 2D virtio-vga."""
    if not cfg.share_host_gpu:
        print("share_host_gpu=false -- generic GPU (software rendering)", file=sys.stderr)
        return ["-device", "virtio-vga", "-display", "none"]

    rendernode = select_render_node()
    if not (rendernode and os.path.exists(rendernode)):
        print("share_host_gpu=true but no usable host render node -- "
              "falling back to a generic GPU", file=sys.stderr)
        return ["-device", "virtio-vga", "-display", "none"]

    # xres/yres seed the virtio-gpu EDID with a 1920x1080 preferred mode so the
    # guest comes up at full-HD geometry early (UEFI/console). The authoritative
    # resolution for the running desktop comes from the guest spice-vdagent.
    vga = "virtio-vga-gl,xres=1920,yres=1080"
    if _envflag("VENUS"):
        hostmem = os.environ.get("VENUS_HOSTMEM", "8G")
        vga += f",blob=on,venus=on,hostmem={hostmem}"
        print(f"Vulkan (Venus) enabled: blob=on,venus=on,hostmem={hostmem}",
              file=sys.stderr)
    print(f"Sharing host GPU (3D offload): {rendernode}", file=sys.stderr)
    return ["-device", vga, "-display", f"egl-headless,rendernode={rendernode}"]


def _make_snapshot(cfg: Config) -> "configuration_watcher.Snapshot":
    """The last-known-good snapshot the watcher reverts to: the CURRENT on-disk
    hypervisor.cfg text (so a revert restores the user's exact file, comments and
    all) with the already-validated coerced values from cfg.hcfg.

    Falls back to a freshly-rendered body if the file is unreadable OR keyless
    (empty / all-comments). A keyless baseline must never be adopted: reverting
    to it would restore an empty file (or be refused), bricking the next boot --
    the exact failure this feature exists to prevent."""
    from dataclasses import asdict
    values = asdict(cfg.hcfg)
    try:
        with open(cfg.hypervisor_cfg_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    if not configuration_watcher._has_known_keys(text):
        text = _hypervisor_cfg_text(values)
    return configuration_watcher.Snapshot(values=values, text=text)


def _tty_save() -> "tuple[int, list] | None":
    """Snapshot the controlling terminal's attributes so teardown can restore them.
    Returns (fd, saved_attrs) or None when stdin is not a tty (piped/headless runs
    have no terminal to corrupt or restore). Best-effort: any termios failure -> None."""
    try:
        if not sys.stdin.isatty():
            return None
        import termios
        fd = sys.stdin.fileno()
        return fd, termios.tcgetattr(fd)
    except (OSError, ValueError, ImportError):
        return None


def _tty_restore(saved: "tuple[int, list] | None") -> None:
    """Put the controlling terminal back the way we found it. This is the fix for the
    corrupted-tab bug: remote-viewer links libvte, which puts the shared controlling
    tty into raw/no-echo mode; when it is killed on teardown (SIGKILL from cleanup, or
    the terminal's own SIGINT on Ctrl-C) VTE never restores it, so the shell is left in
    -echo/-icanon -- typing shows nothing and the prompt renders mangled. We restore the
    snapshot on EVERY exit path (called from cleanup, which all paths funnel through).
    Belt-and-braces `stty sane` re-cooks anything the snapshot did not cover (alt-screen,
    bracketed paste). No-op when there was no tty to save."""
    if saved is None:
        return
    fd, attrs = saved
    try:
        import termios
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except Exception:  # never let terminal restore raise on a teardown path
        pass
    # `stty sane` re-cooks anything the snapshot did not cover (alt-screen, bracketed
    # paste). Point it at the SAVED terminal fd (not sys.stdin, which may be a pseudo-file
    # with no fileno on some stdins); best-effort and never allowed to raise on teardown.
    try:
        subprocess.run(["stty", "sane"], stdin=fd,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _launch(cfg: Config, qemu: list[str], port: "int | None") -> None:
    hcfg = cfg.hcfg
    """Boot QEMU, wait for the SPICE socket, launch the viewer; whichever dies
    first tears the other down. Mirrors run.sh's trap-based lifecycle. A
    ConfigWatcher runs alongside, applying live hypervisor.cfg edits and reverting
    invalid ones."""
    qemu_proc: subprocess.Popen | None = None
    viewer_proc: subprocess.Popen | None = None
    virtiofsd_proc: subprocess.Popen | None = None
    watcher: "configuration_watcher.ConfigWatcher | None" = None
    # Snapshot the terminal BEFORE spawning any child, so teardown can undo a child
    # (remote-viewer/VTE) leaving it raw. See _tty_restore.
    saved_tty = _tty_save()

    def cleanup(*_a) -> None:
        if watcher is not None:
            watcher.stop()
        # Order matters only loosely; kill viewer + QEMU so nothing outlives the VM.
        for p in (viewer_proc, qemu_proc):
            if p and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
        subprocess.run(["pkill", "-9", "-x", cfg.proc],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # The virtiofsd daemon runs as ROOT (sudo), and sudo forks -- killing our
        # child sudo does NOT reap the root daemon, and a non-root kill() could not
        # touch it anyway. Reap it by socket path via sudo pkill so no root daemon (and
        # no held-open share dir) outlives the VM, then remove the root-owned socket.
        subprocess.run(
            ["sudo", "pkill", "-9", "-f", f"virtiofsd.*{cfg.virtiofs_sock}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _rm(cfg.spice_sock)
        _rm_sock(cfg.virtiofs_sock)
        # LAST: undo any terminal corruption a child (remote-viewer/VTE) left behind.
        # Runs on every teardown path because they all funnel through cleanup().
        _tty_restore(saved_tty)

    # Ctrl-C / TERM -> cleanup then exit, matching the bash trap.
    def _sig(_signum, _frame):
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    ssh_info = f"  SSH -> localhost:{port}" if port is not None else ""
    print(
        f"Booting VM '{cfg.vm}': {cfg.cpus} vCPU, {cfg.ram} MiB RAM.{ssh_info}",
        file=sys.stderr,
    )

    try:
        # Spawn the virtiofsd daemon FIRST (when shared is on): QEMU's vhost-user
        # chardev connects to its socket at startup, so the socket must already be
        # listening. Depends only on shared -- never on ssh -- so the share appears
        # on every variant. Killed in cleanup() with the rest of the VM.
        virtiofsd_proc = _spawn_virtiofsd(cfg)

        qemu_proc = subprocess.Popen(qemu)

        # wait for the SPICE socket, then launch our own viewer against it
        for _ in range(100):
            if os.path.exists(cfg.spice_sock):
                break
            if qemu_proc.poll() is not None:
                die("QEMU exited before the SPICE socket appeared (see errors above)")
            time.sleep(0.1)

        # Default: a normal, MAXIMIZED window (WM-decorated, on the taskbar).
        # remote-viewer has no --maximize flag, so we open a plain window with a
        # known title and let the window manager maximize it once it maps (see
        # _maximize_window). --auto-resize=always keeps the guest following the
        # window size (needs guest spice-vdagent). fullscreen=true in
        # hypervisor.cfg opts into borderless exclusive fullscreen instead.
        title = f"hypervisor: {cfg.vm}"
        if hcfg.fullscreen:
            view_args = ["--full-screen", "--auto-resize=always"]
        else:
            view_args = ["--auto-resize=always", "--title", title]
        viewer_env = os.environ.copy()
        viewer_display = os.environ.get("HYPERVISOR_VIEWER_DISPLAY")
        if viewer_display:
            viewer_env["DISPLAY"] = viewer_display
        if not hcfg.fullscreen:
            _maximize_window(title, display=viewer_display)
        # stdin=DEVNULL is the source-side half of the tty fix: remote-viewer links
        # libvte, which -- given a controlling tty on stdin -- puts it into raw mode and
        # (when killed on teardown) leaves it -echo/-icanon, mangling the shell. Handing
        # it /dev/null means it has no terminal to corrupt; _tty_restore in cleanup is the
        # belt-and-braces second half for anything that still slips through.
        viewer_proc = subprocess.Popen(
            ["remote-viewer", *view_args, f"spice+unix://{cfg.spice_sock}"],
            env=viewer_env,
            stdin=subprocess.DEVNULL,
        )

        # Watch hypervisor.cfg for live edits: valid ones are applied/logged,
        # invalid ones are reverted to the file we booted with.
        watcher = configuration_watcher.ConfigWatcher(cfg.hypervisor_cfg_path,
                                               _make_snapshot(cfg))
        watcher.start()

        # whichever dies first (close window -> viewer exits; guest powers off ->
        # QEMU exits) drops us into cleanup, which kills the other.
        _wait_any(qemu_proc, viewer_proc)
    finally:
        cleanup()


def _wait_any(a: subprocess.Popen, b: subprocess.Popen) -> None:
    """Block until either process exits (bash `wait -n`)."""
    while True:
        if a.poll() is not None or b.poll() is not None:
            return
        time.sleep(0.2)


def _spawn_virtiofsd(cfg: Config) -> "subprocess.Popen | None":
    """Start the virtiofsd daemon that backs the shared folder, or None when shared
    is off. Removes any stale socket, launches the daemon as root (argv from the pure
    virtiofsd_argv -- see there for why root), then waits for the socket to appear so
    QEMU's vhost-user chardev can connect. Dies if the daemon exits before the socket
    shows up.

    sudo may prompt for a password once here (same as the offline-share path); the
    daemon then runs as root for the life of the VM. When the primary group cannot be
    resolved (so no --socket-group was emitted) the root-owned socket is chmod-ed
    world-accessible via sudo once it appears, so the non-root QEMU can still open it."""
    group = _primary_group()
    argv = virtiofsd_argv(cfg, socket_group=group)
    if not argv:
        return None
    _rm_sock(cfg.virtiofs_sock)
    print(f"Starting virtiofsd (as root) for shared folder: {cfg.shared_path}",
          file=sys.stderr)
    proc = subprocess.Popen(argv)
    for _ in range(100):  # up to ~10s for the socket to appear
        if os.path.exists(cfg.virtiofs_sock):
            if not group:
                # No group handed to virtiofsd -> the root-owned socket is srwx------;
                # open it up so QEMU (non-root) can connect.
                subprocess.run(["sudo", "chmod", "0666", cfg.virtiofs_sock],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return proc
        if proc.poll() is not None:
            die("virtiofsd exited before its socket appeared -- shared folder "
                "cannot be mounted (see errors above)")
        time.sleep(0.1)
    return proc


def _rm_sock(path: str) -> None:
    """Remove a possibly ROOT-owned vhost-user socket. A prior run's virtiofsd ran as
    root, so its leftover socket is root-owned and a plain unlink EPERMs; fall back to
    sudo. Best-effort -- a stale socket only matters if it still exists when virtiofsd
    tries to bind."""
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except PermissionError:
        subprocess.run(["sudo", "rm", "-f", path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _maximize_window(title: str, display: "str | None" = None) -> None:
    """Maximize the remote-viewer window once it maps, in a background thread."""
    if shutil.which("wmctrl") is None:
        return

    env = os.environ.copy()
    if display:
        env["DISPLAY"] = display

    def worker() -> None:
        for _ in range(100):  # up to ~20s for the window to map
            time.sleep(0.2)
            try:
                out = subprocess.run(
                    ["wmctrl", "-l"], capture_output=True, text=True, env=env
                ).stdout
            except FileNotFoundError:
                return
            win_id = None
            for line in out.splitlines():
                parts = line.split(None, 3)
                if len(parts) == 4 and title in parts[3]:
                    win_id = parts[0]
                    break
            if win_id:
                subprocess.run(
                    ["wmctrl", "-i", "-r", win_id, "-b",
                     "add,maximized_vert,maximized_horz"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env,
                )
                return

    import threading

    threading.Thread(target=worker, daemon=True).start()


def _write_viewer_ask_quit(ask_quit: bool) -> None:
    """Set ask-quit=false so remote-viewer doesn't nag on window close, unless
    ask_quit=true in hypervisor.cfg."""
    if ask_quit:
        return
    vv = os.path.expanduser("~/.config/virt-viewer/settings")
    os.makedirs(os.path.dirname(vv), exist_ok=True)
    existing = ""
    if os.path.isfile(vv):
        with open(vv, encoding="utf-8", errors="replace") as fh:
            existing = fh.read()
    if "ask-quit=false" in existing:
        return
    if "[virt-viewer]" in existing:
        new = existing.replace("[virt-viewer]", "[virt-viewer]\nask-quit=false", 1)
    else:
        new = existing + "[virt-viewer]\nask-quit=false\n"
    with open(vv, "w", encoding="utf-8") as fh:
        fh.write(new)


# --- share -------------------------------------------------------------------
def do_share(cfg: Config, arg: str) -> None:
    if arg == "--offline":
        do_share_offline(cfg)
    else:
        do_share_print()


def do_share_print() -> None:
    print(_SHARE_TEXT)


_SHARE_TEXT = """\
Run THESE commands ONCE INSIDE THE GUEST to auto-mount the host ./Shared folder
at ~/Shared on every boot (any modern Linux guest -- the virtiofs driver is
in-tree, so no module or package is needed):

  sudo mkdir -p ~/Shared
  echo "shared  $HOME/Shared  virtiofs  nofail  0 0" | sudo tee -a /etc/fstab
  sudo mount -a

After that it appears at ~/Shared on every boot, owned by your guest user.
(virtiofsd preserves the host uid/gid; when host and guest both use uid 1000,
ownership lines up. An Azzio guest already auto-mounts it via the shipped
home-main-Shared.mount systemd unit -- no need to run the above.)

----------------------------------------------------------------------------
For a SMOOTH, auto-1920x1080 desktop, the GUEST also needs (once):

  # 1. spice-vdagent -- REQUIRED for auto-resolution. Without it the viewer
  #    window maximizes but the guest stays letterboxed at its boot mode.
  sudo pacman -S spice-vdagent
  sudo systemctl enable --now spice-vdagentd

  # 2. virtio/VirGL GPU driver active (host offloads GL to the RTX 4070).
  #    Verify -- must say "virgl", NOT "llvmpipe":
  glxinfo | grep -i virgl

  # 3. Cinnamon/Muffin smoothness (stops juddering on the paravirtual GPU) --
  #    add to /etc/environment, then re-login:
  echo -e 'CLUTTER_VBLANK=none\\nCOGL_DRIVER=gl3' | sudo tee -a /etc/environment"""


def do_share_offline(cfg: Config) -> None:
    """Edit the powered-off guest disk from the host via qemu-nbd (Btrfs @/@home
    only -- Arch/Manjaro Calamares layout). Refuses anything else, since editing
    an unknown guest layout offline risks corrupting it."""
    guest_uid = os.environ.get("GUEST_UID", "1000")
    guest_gid = os.environ.get("GUEST_GID", "1000")
    # The guest's login name inside the VM. Defaults to the Azzio guest account (`main`,
    # the autologin user the ISO provisions), but is overridable for a differently-named
    # guest -- so this is NOT a host-side /home/<user> hard-code (it names the GUEST's home
    # inside its own disk, mirroring the adjacent GUEST_UID/GID overrides).
    guest_user = os.environ.get("GUEST_USER", "main")
    mountpoint = f"/home/{guest_user}/Shared"
    fstab_line = _guest_fstab_line(guest_user)

    disk = cfg.disk
    if not os.path.isfile(disk):
        die(f"no disk to edit: {disk} -- run 'hypervisor install <iso>' first")
    if shutil.which("qemu-nbd") is None:
        die("qemu-nbd missing -- sudo pacman -S qemu-img")
    checks.require_not_running(cfg)

    _sudo(["modprobe", "nbd", "max_part=16"])
    nbd = _free_nbd()
    if not nbd:
        die("no free /dev/nbdN device available")

    mnt = None
    connected = False
    try:
        print(f"Attaching {disk} on {nbd} ...")
        _sudo(["qemu-nbd", "--disconnect", nbd], quiet=True)
        _sudo(["qemu-nbd", f"--connect={nbd}", disk])
        connected = True
        time.sleep(1)
        _sudo(["partprobe", nbd], quiet=True)

        rootpart = f"{nbd}p2"
        if not _is_block(rootpart):
            rootpart = f"{nbd}p1"
        if not _is_block(rootpart):
            die(f"no candidate root partition on {nbd}")
        if _fstype(rootpart) != "btrfs":
            die(
                "guest root is not the Btrfs @/@home layout this editor expects -- "
                "use 'hypervisor share' (mount from inside the guest) instead"
            )

        mnt = subprocess.run(
            ["mktemp", "-d"], check=True, capture_output=True, text=True
        ).stdout.strip()

        # 1. /etc/fstab (root subvolume @). virtiofs needs NO modules-load entry --
        # the virtiofs driver is in-tree in modern kernels, so unlike 9p there is
        # nothing to force-load at boot; the fstab line alone makes it mount.
        _sudo(["mount", "-o", "subvol=@", rootpart, mnt])
        fstab = os.path.join(mnt, "etc/fstab")
        if _grep_mount(fstab, mountpoint):
            print(f"fstab: entry for {mountpoint} already present.")
        else:
            print("fstab: adding shared-folder mount (virtiofs).")
            _sudo_append(
                fstab,
                '\n# host<->guest shared folder (virtiofs, mount tag "shared")\n'
                + fstab_line
                + "\n",
            )
        _sudo(["umount", mnt])

        # 2. create the mountpoint (home subvolume @home)
        _sudo(["mount", "-o", "subvol=@home", rootpart, mnt])
        if os.path.isdir(os.path.join(mnt, guest_user)):
            _sudo(["mkdir", "-p", os.path.join(mnt, guest_user, "Shared")])
            _sudo(["chown", f"{guest_uid}:{guest_gid}",
                   os.path.join(mnt, guest_user, "Shared")])
            print(f"mountpoint: {mountpoint} ready (owner {guest_uid}:{guest_gid}).")
        else:
            die(f"guest /home/{guest_user} not found in @home subvolume")
        _sudo(["umount", mnt])
    finally:
        if mnt:
            _sudo(["umount", "-R", mnt], quiet=True)
            try:
                os.rmdir(mnt)
            except OSError:
                pass
        if connected:
            _sudo(["qemu-nbd", "--disconnect", nbd], quiet=True)

    print()
    print(f"Done. Boot with 'hypervisor run' -- the share appears at {mountpoint} in the guest.")


# --- status / stop -----------------------------------------------------------
def do_status(cfg: Config) -> None:
    running = "RUNNING" if is_running(cfg) else "stopped"
    hcfg = cfg.hcfg
    port = select_ssh_port(cfg) if hcfg.ssh else None
    iso = cfg.find_iso()
    disk_name = os.path.basename(cfg.disk)
    vars_name = os.path.basename(cfg.vars)
    disk = f"{disk_name}  (qcow2)" if os.path.isfile(cfg.disk) else "(none - not installed)"
    uefi = f"{vars_name}  (UEFI NVRAM)" if os.path.isfile(cfg.vars) else "(none)"
    ssh = f"localhost:{port} -> guest :22" if port is not None else "disabled (ssh=false)"
    shared = cfg.shared_path or "(none)"
    usb = " ".join(hcfg.usb) if hcfg.usb else "(none)"
    print(f"VM:        {cfg.vm}   (process: {cfg.proc})")
    print(f"Directory: {cfg.dir}")
    print(f"State:     {running}")
    print(f"Disk:      {disk}")
    print(f"UEFI vars: {uefi}")
    print(f"Shared:    {shared}")
    print(f"ISO:       {iso or '(none in dir)'}")
    print(f"SSH:       {ssh}")
    print(f"Toggles:   share_host_gpu={hcfg.share_host_gpu}  network={hcfg.network}  "
          f"shared={hcfg.shared}  ssh={hcfg.ssh}  usb={usb}  "
          f"fullscreen={hcfg.fullscreen}  "
          f"ask_before_quitting_hypervisor={hcfg.ask_before_quitting_hypervisor}")
    print(f"Hardware:  ram={cfg.ram} cpus={cfg.cpus} "
          f"disk_size={cfg.disk_size} audio={cfg.audio}")


def do_stop(cfg: Config) -> None:
    if is_running(cfg):
        subprocess.run(["pkill", "-TERM", "-x", cfg.proc],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Sent power-off to VM '{cfg.vm}'.")
    else:
        print(f"VM '{cfg.vm}' is not running.")


# --- small shell/OS helpers --------------------------------------------------
def _rm(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _shquote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def _sudo(argv: list[str], quiet: bool = False) -> int:
    kw = {}
    if quiet:
        kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    return subprocess.run(["sudo", *argv], **kw).returncode


def _sudo_append(path: str, text: str) -> None:
    subprocess.run(["sudo", "tee", "-a", path], input=text, text=True,
                   stdout=subprocess.DEVNULL, check=True)


def _sudo_write(path: str, text: str) -> None:
    subprocess.run(["sudo", "tee", path], input=text, text=True,
                   stdout=subprocess.DEVNULL, check=True)


def _grep_mount(fstab: str, mountpoint: str) -> bool:
    """True if an uncommented fstab line already mounts `mountpoint`."""
    try:
        out = subprocess.run(
            ["sudo", "grep", "-qE", rf"^[^#]*[[:space:]]{mountpoint}[[:space:]]", fstab]
        )
        return out.returncode == 0
    except FileNotFoundError:
        return False


def _free_nbd() -> str:
    """Pick a FREE /dev/nbdN (size 0), matching share.sh's loop."""
    import glob as _glob
    for d in sorted(_glob.glob("/dev/nbd*")):
        name = os.path.basename(d)
        try:
            with open(f"/sys/class/block/{name}/size") as fh:
                if fh.read().strip() == "0":
                    return d
        except OSError:
            continue
    return ""


def _is_block(path: str) -> bool:
    try:
        import stat
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError:
        return False


def _fstype(part: str) -> str:
    try:
        return subprocess.run(
            ["lsblk", "-no", "FSTYPE", part], capture_output=True, text=True
        ).stdout.strip()
    except FileNotFoundError:
        return ""
