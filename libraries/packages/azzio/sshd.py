#!/usr/bin/env python3
"""azzio guest command line interface -- `--sshd-hypervisor` (wire the guest sshd up for the hypervisor).

Installs the host's public key from ~/Shared/authorized_keys (staged there by the
hypervisor) into the target user's ~/.ssh/authorized_keys, then enables and starts sshd.
Safe to run more than once. Named --sshd-hypervisor because it wires the guest sshd up for
the hypervisor's forwarded host->guest SSH port. See common.py for how modules are bundled.
"""

from __future__ import annotations

# BUNDLE_START

# The ssh-variant hardening drop-in (written to /etc/ssh/sshd_config.d/ by the bring-up).
# `PermitEmptyPasswords no` is OpenSSH's default; pinning it explicitly makes the ssh
# variant's posture intentional and drift-proof (data/PROMPT.md DECISION 2: never allow a
# blank-password login). A drop-in leaves the stock sshd_config untouched.
SSHD_HARDENING_CONF = (
    "# Azzio ssh-variant hardening -- written by `azzio --sshd-hypervisor`.\n"
    "# Refuse empty-password logins (the shipped default, pinned so it cannot drift).\n"
    "PermitEmptyPasswords no\n"
)

# Root-login policy lives in its OWN drop-in, SEPARATE from the always-on hardening
# above, because it is TOGGLEABLE at runtime (`azzio network ssh root on|off`, surfaced
# in the TUI). data/PROMPT.md: root ssh login is OFF by default -- only the end user's own
# account (resolved dynamically via SUDO_USER) may log in. `PermitRootLogin no` denies root
# for ALL auth methods (key and password), so even the hypervisor-staged host key cannot
# reach root. Keeping this in a separate file lets the toggle rewrite JUST this file
# (no <-> yes) without ever touching 10-azzio-hardening.conf's empty-password pin.
#
# The `00-` prefix is LOAD-BEARING. sshd resolves each keyword FIRST-match-wins across
# /etc/ssh/sshd_config and every sshd_config.d/*.conf in LEXICAL order (man sshd_config:
# "for each keyword, the first obtained value will be used"). A `00-` file is read before
# every other drop-in (10-azzio-hardening, the systemd 20-*, a stray file, 99-archlinux),
# so OUR PermitRootLogin is authoritative and nothing later can silently override it. The
# earlier `20-` name was NOT authoritative: any lower/other drop-in that set
# `PermitRootLogin yes` won first-match while the status read only our file and wrongly
# reported "denied" -- the exact bug this fixes.
SSHD_ROOT_LOGIN_DROPIN = "/etc/ssh/sshd_config.d/00-azzio-root-login.conf"

# The OLD (pre-fix) drop-in name. A box provisioned by an earlier build carries the deny
# policy here; the toggle removes it on every on/off so the old and new files never coexist
# (two PermitRootLogin directives in the dir is confusing, and a human reading it could not
# tell which wins). `00-` sorts before `20-`, so first-match already prefers the new file --
# the removal is for tidiness and to eliminate the stale, now-dead directive.
SSHD_ROOT_LOGIN_DROPIN_LEGACY = "/etc/ssh/sshd_config.d/20-azzio-root-login.conf"

SSHD_ROOT_LOGIN_OFF = (
    "# Azzio root-login policy -- default DENY (`azzio network ssh root off`).\n"
    "# Only the end user's own account may log in over ssh; root is refused for all\n"
    "# auth methods (key and password). Flip with `azzio network ssh root on`.\n"
    "PermitRootLogin no\n"
)

SSHD_ROOT_LOGIN_ON = (
    "# Azzio root-login policy -- OPT-IN ALLOW (`azzio network ssh root on`).\n"
    "# INSECURE: this lets root log in over ssh. Turn it back off with\n"
    "# `azzio network ssh root off` (the default) as soon as you are done.\n"
    "PermitRootLogin yes\n"
)


def _root_login_dropin_path() -> str:
    """Path of the toggleable root-login drop-in. A function (not just the constant) so
    tests can point the status read at a temp file without a real /etc write."""
    return SSHD_ROOT_LOGIN_DROPIN


def _sshd_effective_permitrootlogin() -> str | None:
    """The EFFECTIVE `PermitRootLogin` value sshd would use RIGHT NOW, or None if it cannot
    be determined. Asks sshd itself via `sshd -T` (the extended test mode that prints the
    fully-resolved config, honouring first-match-wins across sshd_config + every drop-in),
    so the answer reflects reality -- not a naive read of one file that a foreign directive
    might override. `sshd -T` needs root (it reads host keys), so it runs under the same
    non-interactive sudo the rest of the CLI uses; a missing credential or absent sshd
    yields None and the caller falls back to the world-readable drop-in read. The value is
    lower-cased (e.g. "yes", "no", "prohibit-password", "forced-commands-only")."""
    r = subprocess.run([*_sudo_prefix(), "sshd", "-T"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return None
    value = None
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == "permitrootlogin":
            value = parts[1].lower()   # sshd -T prints one effective line; keep the last
    return value


def _root_login_enabled_from_file() -> bool:
    """Fallback read used when the effective (`sshd -T`) query is unavailable: True if OUR
    drop-in ALLOWS root. Pure read (no root): the sshd_config.d files are world-readable.
    Absent file -> disabled (the shipped default). The last `PermitRootLogin` line in the
    file wins WITHIN that single file, but note sshd itself is first-match ACROSS files --
    which is why the drop-in is named `00-` (it wins that cross-file first-match) and why
    the effective query above is preferred whenever it is available."""
    path = _root_login_dropin_path()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    enabled = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[0].lower() == "permitrootlogin":
            # Anything other than an explicit "no"/"prohibit-password"/"forced-commands-only"
            # opens root to interactive login; we only ever write "yes"/"no", but be robust.
            enabled = parts[1].lower() == "yes"
    return enabled


def sshd_root_login_is_enabled() -> bool:
    """True if root ssh login is currently ALLOWED. Prefers sshd's EFFECTIVE policy
    (`sshd -T`) so the answer can never lie -- it accounts for every config file and sshd's
    first-match-wins resolution, catching the case where a foreign directive permits root
    while our own drop-in says no. Only an explicit effective "yes" counts as enabled;
    "no"/"prohibit-password"/"forced-commands-only" all deny an interactive root shell.
    When the effective query is unavailable (no cached sudo, sshd absent), falls back to a
    world-readable read of our drop-in so status still reflects the intended policy."""
    effective = _sshd_effective_permitrootlogin()
    if effective is not None:
        return effective == "yes"
    return _root_login_enabled_from_file()


def _sshd_reload_if_running() -> None:
    """Reload sshd so a root-login toggle takes effect WITHOUT dropping live sessions
    (reload, never restart). Best-effort: if sshd is not running there is nothing to
    reload and the on-disk drop-in will simply be read when it next starts."""
    if sshd_is_active():
        _sudo("systemctl", "reload", "sshd", check=False)


def _remove_legacy_root_login_dropin() -> None:
    """Remove a stale pre-fix `20-azzio-root-login.conf` if present, so the old and new
    drop-ins never coexist (see SSHD_ROOT_LOGIN_DROPIN_LEGACY). Best-effort: `rm -f` is a
    no-op on a fresh install that never had the old file."""
    _sudo("rm", "-f", SSHD_ROOT_LOGIN_DROPIN_LEGACY, check=False)


def sshd_root_login_enable() -> int:
    """Turn root ssh login ON (opt-in, INSECURE): write the `PermitRootLogin yes` drop-in
    (the authoritative first-sorting 00- file), drop any stale legacy 20- file, and reload
    sshd. Returns 0. Prints a security reminder because this widens exposure."""
    _sudo_write(SSHD_ROOT_LOGIN_DROPIN, SSHD_ROOT_LOGIN_ON)
    _remove_legacy_root_login_dropin()
    _sshd_reload_if_running()
    print("root ssh login ENABLED (insecure) -- run `azzio network ssh root off` to "
          "disable it again when done.")
    return 0


def sshd_root_login_disable() -> int:
    """Turn root ssh login OFF (the default): write the `PermitRootLogin no` drop-in (the
    authoritative first-sorting 00- file), drop any stale legacy 20- file, and reload sshd.
    Then VERIFY it actually took: query sshd's EFFECTIVE policy and, if root is somehow
    STILL permitted (a foreign, earlier-matching config forcing `yes`), warn loudly and
    return non-zero instead of silently claiming success -- so 'disable didn't work' is
    surfaced (in the TUI, which shows command output) rather than hidden. Returns 0 when
    root ends up denied. Safe to run repeatedly."""
    _sudo_write(SSHD_ROOT_LOGIN_DROPIN, SSHD_ROOT_LOGIN_OFF)
    _remove_legacy_root_login_dropin()
    _sshd_reload_if_running()
    # Verify against the EFFECTIVE policy DIRECTLY (`sshd -T`), NOT sshd_root_login_is_enabled
    # -- the latter would fall back to re-reading the deny file we just wrote and always
    # "confirm" success (circular). Only a POSITIVE effective "yes" proves an override; a
    # None (sshd -T unavailable: no sudo / no sshd) means we cannot confirm, so we do not
    # falsely assert a verified state.
    effective = _sshd_effective_permitrootlogin()
    if effective == "yes":
        _err("azzio: wrote PermitRootLogin no, but sshd STILL permits root login -- another "
             "sshd_config directive is overriding it. Run `sudo sshd -T | grep -i "
             "permitrootlogin` and check /etc/ssh/sshd_config and /etc/ssh/sshd_config.d/ "
             "for a `PermitRootLogin yes` that takes effect first.")
        return 1
    print("root ssh login disabled -- only your own account may log in over ssh.")
    return 0


def sshd_root_login_status() -> int:
    """Print whether root ssh login is currently enabled or disabled (a plain read of the
    drop-in). Returns 0 when enabled, 1 when disabled, so a probe can branch on the code."""
    enabled = sshd_root_login_is_enabled()
    print(f"root ssh login: {'enabled' if enabled else 'disabled'}")
    return 0 if enabled else 1


def _install_hypervisor_pubkey(target_user: str, target_home: str) -> None:
    """BEST-EFFORT: if the virtiofs `shared` folder carries a host pubkey, install it into
    the TARGET user's ~/.ssh/authorized_keys so the hypervisor can log in by KEY.

    This is the HYPERVISOR nicety and is deliberately NON-FATAL: under QEMU/virtiofs the
    host stages ~/Shared/authorized_keys and we install it; on BARE-METAL (an installed ssh
    variant) there is no virtiofs share, so we simply skip it and rely on PASSWORD auth (the
    operator's --ssh password, baked into /etc/shadow). Enabling sshd itself must NOT
    depend on this -- otherwise the installed ssh desktop would never start sshd. Any
    failure here is reported and swallowed; the caller proceeds to bring sshd up regardless.

    A root-owned authorized_keys trips sshd StrictModes, so the dir + key are chowned to
    the target user (install -o/-g target_user)."""
    # The GUEST mountpoint is ~/Shared; "shared" (the mount source below) is the opaque
    # virtiofs TAG the host exports under, which stays lowercase.
    shared = os.path.join(target_home, "Shared")
    key = os.path.join(shared, "authorized_keys")
    if not _is_mountpoint(shared):
        # Try to mount the virtiofs share; if it is not there (bare metal, or the
        # home-main-Shared.mount unit already covers it), give up quietly. This is a
        # FALLBACK: normally the systemd .mount unit has already mounted ~/Shared, but
        # mounting here too makes the pubkey install work even before that unit runs.
        os.makedirs(shared, exist_ok=True)
        rc = _sudo("mount", "-t", "virtiofs", "shared", shared, check=False)
        if rc != 0:
            print("azzio --sshd-hypervisor: no virtiofs shared folder; skipping "
                  "host-key install (password login still works).")
            return
    if not os.path.isfile(key):
        print(f"azzio --sshd-hypervisor: {key} not found; skipping host-key install "
              "(password login still works).")
        return
    ssh_dir = os.path.join(target_home, ".ssh")
    rc = _sudo("install", "-d", "-m", "700", "-o", target_user, "-g", target_user,
               ssh_dir, check=False)
    if rc != 0:
        _err("azzio --sshd-hypervisor: could not create ~/.ssh; skipping host-key "
             "install (password login still works).")
        return
    rc = _sudo("install", "-m", "600", "-o", target_user, "-g", target_user,
               key, os.path.join(ssh_dir, "authorized_keys"), check=False)
    if rc != 0:
        _err("azzio --sshd-hypervisor: could not install the host pubkey; skipping "
             "(password login still works).")
        return
    print(f"Installed pubkey -> {target_home}/.ssh/authorized_keys")


def sshd_hypervisor() -> int:
    """Bring the ssh server up: (best-effort) install the hypervisor host pubkey, generate
    host keys, OPEN port 22/tcp, then enable+start sshd. Resolves the REAL login user via
    SUDO_USER (the documented invocation is `sudo azzio --sshd-hypervisor`) and refuses a
    bare-root target.

    Works in BOTH environments: under the hypervisor it also installs the host pubkey from
    the virtiofs share (key login); on bare metal (an installed ssh desktop) it skips the pubkey
    step and relies on PASSWORD login (the --ssh password in /etc/shadow). Only the pubkey
    step is optional -- host-key generation, the firewall open, and enabling sshd are
    FAIL-FAST (a failure bails with that step's code and does NOT print the success line, so
    a dead sshd never reports "enabled and started")."""
    target_user = os.environ.get("SUDO_USER") or _current_user()
    if target_user == "root":
        _err("azzio --sshd-hypervisor: run as a normal user via sudo (got root); "
             "cannot stage a login key for root")
        return 1
    try:
        import pwd
        target_home = pwd.getpwnam(target_user).pw_dir
    except KeyError:
        target_home = ""
    if not target_home:
        _err(f"azzio --sshd-hypervisor: could not resolve home for user {target_user}")
        return 1
    # Host pubkey install is BEST-EFFORT (hypervisor only); never blocks bringing sshd up.
    _install_hypervisor_pubkey(target_user, target_home)
    # From here the steps are FAIL-FAST: a failure bails with its exit code and does NOT
    # print the success line (so a failed sshd never reports "enabled and started").
    rc = _sudo("ssh-keygen", "-A", check=False)
    if rc != 0:
        return rc
    # setup-pkgs.sh sets 'ufw default deny incoming', so open :22 BEFORE starting
    # sshd (so the forwarded host->guest port is reachable the moment it listens).
    # 22/tcp explicitly (ssh is tcp): the user's firewall spec is "port 22 configured to
    # allow tcp", and an explicit proto is unambiguous where the `ssh` service alias could
    # in principle also add udp on some ufw app profiles.
    rc = _sudo("ufw", "allow", "22/tcp", check=False)
    if rc != 0:
        return rc
    # HARDEN the ssh variant (data/PROMPT.md DECISION 2): drop a config snippet that
    # refuses empty-password logins, so even if some account ever ended up with a blank
    # shadow field, sshd would reject it. `PermitEmptyPasswords no` is already OpenSSH's
    # default, but writing it explicitly makes the posture intentional and drift-proof (a
    # future default change cannot silently weaken us). A sshd_config.d drop-in is the
    # non-destructive way to set it (leaves the stock sshd_config untouched).
    _sudo_write("/etc/ssh/sshd_config.d/10-azzio-hardening.conf", SSHD_HARDENING_CONF)
    # Deny root ssh login by DEFAULT (data/PROMPT.md): only the end user's own account
    # (target_user, resolved above via SUDO_USER) may log in. Written as its own drop-in so
    # the `azzio network ssh root on|off` toggle can flip just this file later. Done before
    # `systemctl enable` so the shipped/enabled sshd starts with root already denied.
    _sudo_write(SSHD_ROOT_LOGIN_DROPIN, SSHD_ROOT_LOGIN_OFF)
    rc = _sudo("systemctl", "enable", "--now", "sshd", check=False)
    if rc != 0:
        return rc
    print(f"sshd enabled and started -- ssh in as {target_user}.")
    return 0


def sshd_stop() -> int:
    """Stop AND disable sshd, then CLOSE port 22 in the firewall -- the inverse of the
    `--sshd-hypervisor` bring-up. Used by `azzio network ssh stop` and the TUI's SSH
    Server screen so a user can turn ssh back off from one place.

    Disabling the service (not just stopping it) is deliberate: `stop` alone would let
    sshd come back at the next boot. We also `ufw delete allow 22/tcp` so the open port
    does not outlive the running service (an open :22 with nothing listening is still an
    advertised attack surface). Each step is best-effort (check=False) and the worst rc is
    returned, so a missing ufw rule does not mask a failed systemctl."""
    rc = _sudo("systemctl", "disable", "--now", "sshd", check=False)
    # Remove the allow rule so the port is not left open with no service behind it. A
    # non-existent rule makes ufw exit non-zero, which is fine here (nothing to close).
    _sudo("ufw", "delete", "allow", "22/tcp", check=False)
    if rc != 0:
        _err("azzio: could not stop sshd (systemctl disable --now sshd failed).")
        return rc
    print("sshd stopped and disabled; port 22 closed in the firewall.")
    return 0


def sshd_is_active() -> bool:
    """True if the sshd service is currently active. Pure read (no root)."""
    return subprocess.run(["systemctl", "is-active", "--quiet", "sshd"],
                          check=False).returncode == 0


def sshd_status() -> int:
    """Print whether sshd is active and whether port 22 is allowed through the firewall --
    a one-shot read for `azzio network ssh status` and the TUI status line. Returns 0 if
    sshd is active, 1 otherwise, so a caller/probe can branch on the exit code."""
    active = sshd_is_active()
    print(f"sshd: {'active' if active else 'inactive'}")
    # Report the firewall posture for :22 too (read-only via a non-interactive sudo; if
    # that needs a password we simply omit the line rather than prompt from a status read).
    r = subprocess.run(["sudo", "-n", "ufw", "status"],
                       capture_output=True, text=True, check=False)
    if r.returncode == 0:
        allowed = any("22" in ln and "ALLOW" in ln.upper() for ln in r.stdout.splitlines())
        print(f"firewall: port 22 {'allowed' if allowed else 'not allowed'}")
    return 0 if active else 1
