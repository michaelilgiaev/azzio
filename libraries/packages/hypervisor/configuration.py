"""configuration.py - CWD-derived VM identity, paths, and configuration.

Everything about a VM is derived from the CURRENT WORKING DIRECTORY:

    cd ~/Hypervisors/azzio && hypervisor install some.iso

Config object is built once (Config.from_cwd()) and threaded through every
subcommand. VM / PROC / DISK are always derived from the directory and are NOT
overridable. Everything else is read from hypervisor.cfg (priority: env > cfg > default).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

# Flat app: the sibling modules must resolve to the SAME module object however this file
# is loaded. When loaded AS part of the packages.hypervisor package (the test suite, which
# imports `from packages.hypervisor import ...`), __package__ is set -> use package-relative
# imports so there is exactly ONE checks/configuration_schema module (and therefore one
# HypervisorError class the tests can catch). When loaded FLAT by absolute path (the launcher
# execs command_line_interface.py, which has no parent package), fall back to a sys.path
# bootstrap + bare sibling imports. Mirrors the flat layout of packages/backup + passwords, but made
# dual-mode because this app raises a custom exception the tests assert on across the
# import boundary (backup/passwords use return codes, so they never needed this).
if __package__:
    from . import configuration_schema
    from .checks import die
else:  # loaded flat (run by absolute path via the launcher) -- no parent package
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import configuration_schema  # noqa: E402  (after the sys.path bootstrap above)
    from checks import die  # noqa: E402

CODE = "/usr/share/edk2/x64/OVMF_CODE.4m.fd"
VARS_TMPL = "/usr/share/edk2/x64/OVMF_VARS.4m.fd"

DEFAULT_SSH_FORWARD_PORT = 49155

_HYPERVISOR_CFG_NAME = "hypervisor.cfg"

# Defaults as already-COERCED Python values (the same types coerce_all yields):
# bools are bool, ram/cpus/port are int, shared/usb use their union types.
_CFG_DEFAULTS: dict = {
    "share_host_gpu":                 True,
    "network":                        "user",
    "shared":                         False,
    "ssh":                            False,
    "ssh_guest_to_host_port_forward": DEFAULT_SSH_FORWARD_PORT,
    "usb":                            [],
    "fullscreen":                     False,
    "ask_before_quitting_hypervisor": False,
    "ram":                            16384,
    "cpus":                           16,
    "disk_size":                      "200G",
    "audio":                          "on",
}

# One SHORT, high-level comment per key. Keep it minimal (the user asked: "add a
# little bit of comments ... high level explanations, and dont over do it").
# These reference how to inspect the host on an Arch-based system where relevant.
_CFG_COMMENTS: dict = {
    "share_host_gpu":
        "guest renders on the host GPU (shared, not passthrough); false = software",
    "network":
        "user (NAT) | none | a host interface to bridge (list them: ip -br addr)",
    "shared":
        "false | empty = share this dir | an absolute host path to share (virtiofs)",
    "ssh":
        "forward the guest's SSH port to the host",
    "ssh_guest_to_host_port_forward":
        "host port that maps to guest :22",
    "usb":
        "empty | absolute device path(s) to pass through (find them: lsusb, lsblk -o NAME,TRAN,MOUNTPOINT)",
    "fullscreen":
        "borderless exclusive fullscreen instead of a maximized window",
    "ask_before_quitting_hypervisor":
        "prompt before the viewer window closes the VM",
    "ram":
        "guest RAM in MiB (16384 = 16 GiB); check the host with: free -h",
    "cpus":
        "vCPU count pinned as cores; do not exceed the host: nproc",
    "disk_size":
        "qcow2 virtual disk size, e.g. 200G (only used when the disk is created)",
    "audio":
        "on | off; on = PipeWire on the host (confirm with: pactl info)",
}


def _render_value(key: str, val) -> str:
    """Render a coerced Python value back to its hypervisor.cfg string form."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, list):
        return " ".join(val)
    return str(val)


def _render_defaults() -> dict:
    """A fresh copy of the coerced defaults (helper for tests / the generator)."""
    return dict(_CFG_DEFAULTS)


def effective_defaults() -> dict:
    """The base defaults a fresh `hypervisor install` starts from: the built-in
    _CFG_DEFAULTS with the user's global overrides (defaults.cfg) layered on top. Coerced
    values, in schema order. This is what `hypervisor --configure --status` reports and what
    the bare-`azzio` TUI summarises -- deliberately EXCLUDES any per-directory hypervisor.cfg
    and env (those are per-VM, not defaults)."""
    vals = dict(_CFG_DEFAULTS)
    _apply_user_defaults(vals)
    return vals


def render_defaults_text(vals: dict) -> str:
    """Render effective defaults as plain `key = value` lines (schema order), for the
    `--configure --status` report. Unlike _hypervisor_cfg_text this carries NO comments --
    it is a status dump, not a generated cfg file."""
    return "".join(f"{key} = {_render_value(key, vals[key])}\n"
                   for key in configuration_schema.KEYS)


def _hypervisor_cfg_text(vals: dict) -> str:
    """Generate hypervisor.cfg text: one '# comment' line then 'key = value' per
    setting, in schema order. `vals` holds COERCED values (as from _CFG_DEFAULTS
    or a HypervisorCfg)."""
    lines: list[str] = []
    for key in configuration_schema.KEYS:
        comment = _CFG_COMMENTS.get(key)
        if comment:
            lines.append(f"# {comment}")
        lines.append(f"{key} = {_render_value(key, vals[key])}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


@dataclass
class HypervisorCfg:
    share_host_gpu: bool
    network: str
    shared: "bool | str"
    ssh: bool
    ssh_guest_to_host_port_forward: int
    usb: list
    fullscreen: bool
    ask_before_quitting_hypervisor: bool
    disk_size: str
    ram: int
    cpus: int
    audio: str

    @classmethod
    def from_dir(cls, directory: str) -> "HypervisorCfg":
        # Layering (lowest priority first): built-in defaults -> the user's global
        # default overrides (~/.config/azzio-hypervisor/defaults.cfg) -> this directory's
        # own hypervisor.cfg -> env. So a global default changes what NEW installs and
        # unset keys resolve to, while a directory's own cfg still wins for that VM.
        vals = dict(_CFG_DEFAULTS)
        _apply_user_defaults(vals)
        path = os.path.join(directory, _HYPERVISOR_CFG_NAME)
        if os.path.isfile(path):
            raw = _migrate_legacy_keys(_parse_conf(path))
            coerced, errors = configuration_schema.coerce_all(raw)
            if errors:
                die(f"{_HYPERVISOR_CFG_NAME}: " + "; ".join(errors))
            vals.update(coerced)
        _apply_env_overrides(vals)
        return cls(**vals)

    @classmethod
    def write(cls, directory: str, vals: dict) -> str:
        path = os.path.join(directory, _HYPERVISOR_CFG_NAME)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_hypervisor_cfg_text(vals))
        return path


# Env overrides mirror the cfg keys (uppercased, with a couple of legacy names).
# Each raw env string goes through the SAME coercer as the file, so an override
# is validated exactly like a file value.
_ENV_OVERRIDES = {
    "NETWORK":        "network",
    "DISK_SIZE":      "disk_size",
    "RAM":            "ram",
    "CPUS":           "cpus",
    "AUDIO":          "audio",
    "SSHPORT":        "ssh_guest_to_host_port_forward",
    "SHARED":         "shared",
    "USB":            "usb",
    "SHARE_HOST_GPU": "share_host_gpu",
    "SSH":            "ssh",
    "FULLSCREEN":     "fullscreen",
    "ASK_QUIT":       "ask_before_quitting_hypervisor",
}

# Legacy 1/0 env flags for booleans: keep the old ergonomics (SHARE_HOST_GPU=1).
_ENV_BOOL_ONEZERO = {"SHARE_HOST_GPU", "SSH", "FULLSCREEN", "ASK_QUIT"}


def _apply_env_overrides(vals: dict) -> None:
    for env, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env, "")
        if not raw:
            continue
        if env in _ENV_BOOL_ONEZERO:
            vals[key] = raw == "1"
            continue
        ok, val, err = configuration_schema.coerce_one(key, raw)
        if not ok:
            die(f"{env}: {err} (got '{raw}')")
        vals[key] = val


def _apply_user_defaults(vals: dict) -> None:
    """Layer the user's global default overrides (defaults.cfg) over the built-in defaults.

    Imported LAZILY to avoid an import cycle (configuration_defaults imports this module).
    Each override is re-coerced through the schema; a value that fails is SILENTLY skipped
    (the file is optional and set_key already validated on write -- degrading a stray bad
    line to "use the built-in" must never fail a VM launch, unlike a directory's own
    hypervisor.cfg which dies loudly)."""
    if __package__:
        from . import configuration_defaults
    else:  # loaded flat by absolute path via the launcher -- no parent package
        import configuration_defaults  # noqa: E402
    for key, raw in configuration_defaults.load().items():
        ok, val, _err = configuration_schema.coerce_one(key, raw)
        if ok and val is not None:
            vals[key] = val


def _slugify(base: str) -> str:
    """Sanitize a directory name to a [a-z0-9-] slug (matches common.sh)."""
    s = base.lower()
    s = re.sub(r"[^a-z0-9-]", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "vm"


@dataclass
class Config:
    dir: str
    vm: str
    proc: str
    disk: str
    vars: str
    shared: str
    spice_sock: str
    hypervisor_cfg_path: str
    hcfg: HypervisorCfg
    code: str
    vars_tmpl: str

    # convenience aliases into hcfg (read-only after construction). ram/cpus are
    # ints in the cfg; QEMU's -m/-smp take strings, so render them as str here.
    @property
    def disk_size(self) -> str: return self.hcfg.disk_size
    @property
    def ram(self) -> str: return str(self.hcfg.ram)
    @property
    def cpus(self) -> str: return str(self.hcfg.cpus)
    @property
    def audio(self) -> str: return self.hcfg.audio
    @property
    def share_host_gpu(self) -> bool: return self.hcfg.share_host_gpu

    @property
    def shared_path(self) -> str:
        """The host directory to share, honouring hcfg.shared:
        True -> the default ./Shared dir; a str -> that path; False -> '' (off)."""
        s = self.hcfg.shared
        if s is True:
            return self.shared
        if isinstance(s, str) and s:
            return s
        return ""

    @property
    def virtiofs_sock(self) -> str:
        """The vhost-user UNIX socket the virtiofsd daemon listens on and QEMU
        connects to for the shared folder. Lives beside .spice.sock in the VM dir
        (a dotfile so it does not clutter the share); one per VM dir, so two VMs
        never collide. Derived, not a stored field -- like the spice socket."""
        return os.path.join(self.dir, ".virtiofs.sock")

    @classmethod
    def from_cwd(cls) -> "Config":
        d = os.getcwd()
        base = os.path.basename(d)
        vm = _slugify(base)
        proc = f"{vm}-vm"[:15]

        hcfg = HypervisorCfg.from_dir(d)  # schema in from_dir already validated it

        return cls(
            dir=d,
            vm=vm,
            proc=proc,
            disk=os.path.join(d, f"{vm}.qcow2"),
            vars=os.path.join(d, "OVMF_VARS.4m.fd"),
            shared=os.path.join(d, "Shared"),
            spice_sock=os.path.join(d, ".spice.sock"),
            hypervisor_cfg_path=os.path.join(d, _HYPERVISOR_CFG_NAME),
            hcfg=hcfg,
            code=CODE,
            vars_tmpl=VARS_TMPL,
        )

    # --- ISO argument: REQUIRED, must be a .iso that exists ------------------
    def resolve_iso(self, arg: str) -> str:
        """A .iso file is mandatory. Accepts a path or a bare filename in CWD.

        Returns the resolved path or raises HypervisorError. Unlike the old
        behaviour, this never auto-discovers: the caller must name the file.
        """
        if not arg:
            die("an ISO is required -- e.g. 'hypervisor install azzio.iso'")
        if not arg.endswith(".iso"):
            die(f"expected a .iso file, got: {arg}")
        if "/" in arg:
            if not os.path.isfile(arg):
                die(f"ISO not found: {arg}")
            return arg
        in_dir = os.path.join(self.dir, arg)
        if os.path.isfile(in_dir):
            return in_dir
        if os.path.isfile(arg):
            return arg
        die(f"ISO not found in {self.dir}: {arg}")

    def find_iso(self) -> str:
        """Best-effort single *.iso in the dir for status display; '' if not exactly one."""
        matches = _glob_sorted(self.dir, ".iso")
        return matches[0] if len(matches) == 1 else ""

    # --- disk argument: REQUIRED, must be a .qcow2 that exists ---------------
    def resolve_run_disk(self, arg: str) -> str:
        """A .qcow2 file is mandatory. Accepts a path or a bare filename in CWD."""
        if not arg:
            die("a disk is required -- e.g. 'hypervisor run azzio.qcow2'")
        if not arg.endswith(".qcow2"):
            die(f"expected a .qcow2 file, got: {arg}")
        if "/" in arg:
            if not os.path.isfile(arg):
                die(f"disk not found: {arg}")
            return arg
        in_dir = os.path.join(self.dir, arg)
        if os.path.isfile(in_dir):
            return in_dir
        if os.path.isfile(arg):
            return arg
        die(f"disk not found in {self.dir}: {arg}")


def parse_conf_text(text: str) -> dict[str, str]:
    """Parse a KEY=VALUE hypervisor.cfg BODY, stripping '#' comments. The ONE
    canonical parser -- the live-reload watcher uses this too, so its validation
    can never disagree with the loader about what a line is. Splits on '\\n' only
    (NOT str.splitlines(), which also breaks on \\x0b \\x0c \\x85 \\u2028 ... and
    would let a body pass the watcher yet mis-parse in the loader)."""
    out: dict[str, str] = {}
    for raw in text.split("\n"):
        line = raw.split("#", 1)[0]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_conf(path: str) -> dict[str, str]:
    """Parse a KEY=VALUE hypervisor.cfg file (matches common.sh)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_conf_text(fh.read())


# Pre-redesign key names, mapped to their current names so an old hypervisor.cfg
# keeps working. The current name wins if BOTH are present.
_LEGACY_KEY_ALIASES = {"sshd": "ssh"}


def _migrate_legacy_keys(raw: dict) -> dict:
    out = dict(raw)
    for old, new in _LEGACY_KEY_ALIASES.items():
        if old in out:
            old_val = out.pop(old)
            out.setdefault(new, old_val)   # current name wins if both present
    return out


def _glob_sorted(directory: str, suffix: str) -> list[str]:
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(suffix))
    except OSError:
        return []
    paths = [os.path.join(directory, n) for n in names]
    return [p for p in paths if os.path.isfile(p)]   # skip a dir named like *.iso


def select_ssh_port(cfg: Config) -> int:
    """The forwarded host port for guest :22. Defaults to 49155 (or whatever
    ssh_guest_to_host_port_forward / SSHPORT is set to); bumps past a port
    already in use so two VMs never collide. Never climbs past the max valid TCP
    port (65535) -- if everything up to there is busy it dies cleanly rather than
    letting the bump reach 65536 and crash socket.bind (OverflowError)."""
    port = cfg.hcfg.ssh_guest_to_host_port_forward or DEFAULT_SSH_FORWARD_PORT
    while port <= configuration_schema._MAX_PORT:
        if not _port_in_use(port):
            return port
        port += 1
    die(f"no free host port available at or above "
        f"{cfg.hcfg.ssh_guest_to_host_port_forward} (up to {configuration_schema._MAX_PORT})")


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True
        except OverflowError:
            return True   # out of the 0..65535 range -> not a usable port
