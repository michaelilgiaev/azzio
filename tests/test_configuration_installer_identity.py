"""installer_identity -- the CLI/SSH installer's account / hostname / timezone collection.

The scripted installer must reach parity with the Calamares Users + Location pages: it now
PROMPTS (or reads AZ_INSTALL_* env for an unattended SSH install) for hostname, full name,
username, a user password, a root password, and a timezone, then applies them in the chroot
(create/rename the user, set both passwords, write the hostname, point /etc/localtime at the
chosen zone). These pin the load-bearing bits of those three bash fragments:

  * env pre-seed hooks exist for every field AND an interactive fallback (`read`) is kept, so
    a plain `azzio-install --cli` over SSH still works step by step.
  * passwords are read hidden (`read -s`), confirmed, and never written to a world-readable
    file; the chroot shreds the plaintext files after chpasswd.
  * the chroot applies user + root passwords via chpasswd, renames the copied `main` account
    to the chosen login (preserving uid/gid), writes the hostname, and overrides the timezone.
  * everything is a pure string producer (no network/subprocess/fs), and the collected bash
    is syntactically valid (`bash -n`).
"""

from __future__ import annotations

import subprocess

import installer_identity as idy


def _bash_ok(fragment: str) -> None:
    # A syntax check of the fragment in a minimal harness (it references shell vars/functions
    # defined by the surrounding installer, so we only assert it PARSES, not that it runs).
    res = subprocess.run(["bash", "-n"], input=fragment, text=True, capture_output=True)
    assert res.returncode == 0, res.stderr


# --- collect: env pre-seed for every field, interactive fallback kept --------

def test_collect_has_env_preseed_and_interactive_fallback_for_every_field():
    s = idy.identity_collect_sh()
    # Each field: an AZ_INSTALL_* pre-seed AND a `read` fallback for interactive use.
    assert 'if [ -n "$AZ_INSTALL_HOSTNAME" ]' in s and 'read -rp "Hostname' in s
    assert 'if [ -n "$AZ_INSTALL_FULLNAME" ]' in s and 'read -rp "Your full name' in s
    assert 'if [ -n "$AZ_INSTALL_USERNAME" ]' in s and 'read -rp "Username' in s
    assert 'if [ -n "$AZ_INSTALL_PASSWORD" ]' in s and 'read -rsp "Password for' in s
    assert 'if [ -n "$AZ_INSTALL_ROOT_PASSWORD" ]' in s
    assert 'if [ -n "$AZ_INSTALL_TIMEZONE" ]' in s and 'read -rp "Timezone' in s


def test_collect_reads_passwords_hidden_and_confirms():
    s = idy.identity_collect_sh()
    # Hidden entry (`read -s`) and a confirmation/repeat step for both user and root.
    assert "read -rsp" in s
    assert "Repeat password" in s
    assert "Passwords did not match" in s


def test_collect_skips_fullname_prompt_under_star_password():
    # `azzio-install --auto` leaves the full name blank (spec: full_name=NULL, skip). An empty
    # AZ_INSTALL_FULLNAME cannot survive run_cli()'s `${VAR:+...}` sudo forwarding, so the collect
    # step must instead skip the full-name prompt under the auto/unattended marker
    # (AZ_INSTALL_STAR_PASSWORD) -- otherwise --auto blocks on "Your full name (optional):".
    s = idy.identity_collect_sh()
    # The full-name block gates on the star/auto marker and leaves az_fullname empty there.
    assert 'elif [ -n "$AZ_INSTALL_STAR_PASSWORD" ]; then' in s
    assert "Full name: (skipped)" in s
    # The interactive `read` fallback for a plain `--cli` run is still present.
    assert 'read -rp "Your full name' in s


def test_collect_fullname_skipped_noninteractively_under_star(tmp_path):
    # Behavioural: with AZ_INSTALL_STAR_PASSWORD set and AZ_INSTALL_FULLNAME UNSET (exactly how
    # --auto arrives after run_cli drops the empty fullname), the collect step must run to
    # completion WITHOUT reading the full name from stdin. Driven with stdin closed it must exit
    # 0 and leave az_fullname empty. A regression that re-prompts here would hang/fail on the
    # empty stdin, catching the reported "--auto prompts: Your full name (optional):" bug.
    import subprocess, os
    s = idy.identity_collect_sh()
    zdir = tmp_path / "zoneinfo" / "Asia"
    zdir.mkdir(parents=True)
    (zdir / "Jerusalem").write_text("")
    s = s.replace("/usr/share/zoneinfo", str(tmp_path / "zoneinfo"))
    driver = tmp_path / "collect.sh"
    driver.write_text("#!/bin/bash\nexport LIGHT_BLUE='' RESET=''\n" + s
                      + '\necho "FULL=[$az_fullname] USER=$az_username"\n')
    env = dict(os.environ,
               AZ_INSTALL_STAR_PASSWORD="1", AZ_INSTALL_USERNAME="main",
               AZ_INSTALL_HOSTNAME="azzio", AZ_INSTALL_TIMEZONE="Asia/Jerusalem")
    env.pop("AZ_INSTALL_FULLNAME", None)  # UNSET, as --auto's dropped empty value arrives.
    r = subprocess.run(["bash", str(driver)], capture_output=True, text=True,
                       env=env, stdin=subprocess.DEVNULL, timeout=30)
    assert r.returncode == 0, f"collect blocked/failed on fullname under star mode: {r.stderr}"
    assert "FULL=[] USER=main" in r.stdout, r.stdout
    assert "Your full name" not in r.stdout


def test_collect_skips_password_prompts_under_star_password():
    # STAR-PASSWORD convention (`--auto` sets AZ_INSTALL_STAR_PASSWORD): the password prompts
    # must be SKIPPED entirely (they would block an unattended run) and a marker exported so
    # the write/chroot steps apply a literal '*'. Assert the collect step gates the whole
    # password section on the env var and sets az_star_password, exporting it downstream.
    s = idy.identity_collect_sh()
    assert 'if [ -n "$AZ_INSTALL_STAR_PASSWORD" ]; then' in s
    assert "az_star_password=1" in s
    # az_star_password is exported alongside the other collected answers.
    assert "az_star_password" in s.split("export ", 1)[1]


def test_collect_star_password_flow_is_noninteractive(tmp_path):
    # Behavioural: with AZ_INSTALL_STAR_PASSWORD (and the other fields pre-seeded, as `--auto`
    # provides) the collect step must run to completion WITHOUT reading a password from stdin.
    # Drive it with stdin closed; it must exit 0 and set az_star_password=1. A regression that
    # re-introduced a password `read` here would hang/fail (empty stdin -> non-zero), catching
    # the "unattended install blocks on a hidden password prompt" bug.
    import subprocess, os
    s = idy.identity_collect_sh()
    # Point the zoneinfo validation at a stub tree so Asia/Jerusalem validates in the sandbox.
    zdir = tmp_path / "zoneinfo" / "Asia"
    zdir.mkdir(parents=True)
    (zdir / "Jerusalem").write_text("")
    s = s.replace("/usr/share/zoneinfo", str(tmp_path / "zoneinfo"))
    driver = tmp_path / "collect.sh"
    driver.write_text("#!/bin/bash\nexport LIGHT_BLUE='' RESET=''\n" + s
                      + '\necho "STAR=$az_star_password USER=$az_username TZ=$az_timezone"\n')
    env = dict(os.environ,
               AZ_INSTALL_STAR_PASSWORD="1", AZ_INSTALL_USERNAME="main",
               AZ_INSTALL_HOSTNAME="azzio", AZ_INSTALL_TIMEZONE="Asia/Jerusalem",
               AZ_INSTALL_FULLNAME="")
    r = subprocess.run(["bash", str(driver)], capture_output=True, text=True,
                       env=env, stdin=subprocess.DEVNULL, timeout=30)
    assert r.returncode == 0, f"collect blocked/failed under star mode: {r.stderr}"
    assert "STAR=1 USER=main TZ=Asia/Jerusalem" in r.stdout, r.stdout
    # It must NOT have prompted for a password.
    assert "Password for" not in r.stdout


def test_collect_validates_username_and_timezone():
    s = idy.identity_collect_sh()
    # Username matches a POSIX-ish pattern; timezone must exist in the zoneinfo DB.
    assert "grep -Eq '^[a-z_][a-z0-9_-]*$'" in s
    assert 'if [ -f "/usr/share/zoneinfo/$az_timezone" ]' in s
    # A bad PRE-SEEDED value aborts rather than looping forever on a non-interactive stdin.
    assert "Aborting (pre-seeded timezone is invalid)." in s
    assert "Aborting (pre-seeded username is invalid)." in s


def test_collect_rejects_reserved_and_existing_usernames():
    # An adversary case: the username regex alone accepts pre-existing system accounts (root,
    # bin, nobody, ...). The chroot only RENAMES `main` to the chosen name when that name is
    # FREE, so a colliding name silently skips the rename yet still re-points tty1 autologin at
    # that account -> no startx bootstrap -> bare tty1 (the reported bug). The collect step must
    # reject `root` outright and any OTHER already-existing account, while still allowing `main`
    # (the live user we rename from).
    s = idy.identity_collect_sh()
    # root is always rejected.
    assert '[ "$az_username" = "root" ]' in s
    assert "not allowed" in s and "root" in s
    # Any other pre-existing account is rejected (id lookup), but `main` is explicitly allowed.
    assert '[ "$az_username" != "main" ] && id "$az_username"' in s
    assert "already exists as a system account" in s
    # Pre-seeded collisions abort (don't loop forever on non-interactive stdin).
    assert "Aborting (pre-seeded username is reserved)." in s
    assert "Aborting (pre-seeded username collides with a system account)." in s


def test_collect_defaults_match_live_identity():
    s = idy.identity_collect_sh()
    assert 'az_hostname="${az_hostname:-azzio}"' in s
    assert 'az_username="${az_username:-main}"' in s
    assert 'az_timezone="${az_timezone:-Asia/Jerusalem}"' in s


def test_collect_is_valid_bash():
    _bash_ok(idy.identity_collect_sh())


# --- write: persist answers, passwords 0600 ---------------------------------

def test_write_persists_fields_and_secures_passwords():
    s = idy.identity_write_sh()
    for f in ("hostname", "username", "fullname", "timezone"):
        assert f"/mnt/etc/install_info/{f}" in s
    # In the NORMAL (non-star) path, passwords are written under a restrictive umask
    # (0600-ish), not a plain marker file.
    assert "umask 077" in s
    assert "/mnt/etc/install_info/password" in s
    assert "/mnt/etc/install_info/root_password" in s


def test_write_persists_only_a_marker_under_star_password():
    # Under the STAR-PASSWORD convention (`--auto`) NO plaintext password is persisted: the
    # write step branches on $az_star_password and drops just a `star_password` marker so the
    # chroot writes a literal '*' for user + root. The plaintext files (and their umask 077
    # subshells) live only in the else branch, so a star install never lands a secret on disk.
    s = idy.identity_write_sh()
    assert 'if [ -n "$az_star_password" ]; then' in s
    assert "/mnt/etc/install_info/star_password" in s


def test_write_is_valid_bash():
    _bash_ok(idy.identity_write_sh())


# --- chroot: apply user + root passwords, hostname, timezone -----------------

def test_chroot_sets_both_passwords_via_chpasswd_and_shreds():
    # The NORMAL (non-star) path: collected user + root passwords applied via chpasswd, then
    # the plaintext files shredded. This is the else branch of the star-password conditional.
    s = idy.identity_chroot_sh()
    assert "chpasswd" in s
    # Root password is applied too (parity with Calamares setRootPassword).
    assert "printf 'root:%s'" in s
    # Plaintext password files are removed after use.
    assert "rm -f /etc/install_info/password /etc/install_info/root_password" in s


def test_chroot_writes_star_password_for_user_and_root_when_requested():
    # STAR-PASSWORD convention (`--auto`): when the star_password marker is present the chroot
    # writes a literal '*' into the shadow field for BOTH the chosen login and root via
    # `usermod -p '*'` (the Ubuntu/casper standard: an invalid hash -> no password login, but
    # the account is NOT locked, so tty1 autologin + NOPASSWD sudo keep the box usable). It
    # must NOT run chpasswd in this branch, and it consumes (removes) the marker.
    s = idy.identity_chroot_sh()
    assert "if [ -f /etc/install_info/star_password ]; then" in s
    assert "usermod -p '*' \"$az_login\"" in s
    assert "usermod -p '*' root" in s
    assert "rm -f /etc/install_info/star_password" in s


def test_chroot_star_and_chpasswd_are_mutually_exclusive():
    # The star branch and the chpasswd branch are the two arms of ONE if/else: a star install
    # must never also chpasswd (that would set a real password), and a normal install must
    # never usermod -p '*'. Assert the chpasswd calls sit in the `else` after the star `if`.
    s = idy.identity_chroot_sh()
    star_idx = s.index("if [ -f /etc/install_info/star_password ]; then")
    else_idx = s.index("else", star_idx)
    chpasswd_idx = s.index("chpasswd", star_idx)
    assert star_idx < else_idx < chpasswd_idx, "chpasswd must be in the else arm of the star check"


def test_chroot_renames_live_user_preserving_identity():
    s = idy.identity_chroot_sh()
    # Rename the copied-in `main` to the chosen login (keeps uid/gid + /home), not recreate.
    assert "usermod -l" in s
    assert "groupmod -n" in s
    # And move its home to match the new name.
    assert 'mv /home/main "/home/$az_username"' in s


def test_chroot_writes_hostname_and_overrides_timezone():
    s = idy.identity_chroot_sh()
    assert 'echo "$az_hostname" > /etc/hostname' in s
    assert 'ln -sf "/usr/share/zoneinfo/$az_timezone" /etc/localtime' in s
    assert "hwclock --systohc" in s


def test_chroot_reissues_sudo_grant_for_the_chosen_user():
    # THE parity bug an adversary found: the only sudo grant on the target is the copied
    # /etc/sudoers.d/00-main (`main ALL=(ALL) NOPASSWD: ALL`). Renaming the account to e.g.
    # "alice" would leave that rule pointing at a user that no longer exists -> the installed
    # user has NO sudo. So after a rename the chroot MUST re-point the grant at the chosen
    # login (Calamares gives the created user wheel/sudo; this is the CLI equivalent).
    s = idy.identity_chroot_sh()
    # A sudoers drop-in is (re)written for the chosen login with NOPASSWD, replacing 00-main.
    assert "/etc/sudoers.d/00-main" in s
    assert "NOPASSWD: ALL" in s
    # It is keyed on the chosen login ($az_login), not the literal "main" -- written via
    # printf '%s ...' "$az_login" so a login name is never interpolated as a format string.
    assert 'ALL=(ALL) NOPASSWD: ALL\\n' in s and '"$az_login"' in s
    # Validated with visudo -c before install (a bad sudoers file locks everyone out) and 0440.
    assert "visudo -c" in s
    assert "0440" in s or "chmod 440" in s


def test_chroot_repoints_first_boot_unit_after_home_move():
    # The first-boot oneshot's ExecStart is hardcoded to /home/main/.config/first-boot/...
    # (installer.first_boot_service). After `mv /home/main /home/$user` that path dangles and
    # the enabled unit fails at first boot. The chroot must rewrite the unit's ExecStart (and
    # the conf path it reads) to the renamed home so first-boot still runs.
    s = idy.identity_chroot_sh()
    assert "first-boot-setup.service" in s
    assert "/home/main/.config/first-boot" in s          # the old path being rewritten
    # The rewrite targets the new home path (keyed on the resolved login).
    assert '/home/$az_login/.config/first-boot' in s


def test_chroot_repoints_getty_autologin_after_rename():
    # THE adversary-found boot-blocker: the scripted installer copies the live getty@tty1
    # autologin drop-in onto the target, and it hardcodes `--autologin main`. After a rename to
    # e.g. "alice", `main` no longer exists, so agetty's `login -f main` fails and tty1 respawns
    # forever -- the installed system never autologins and drops to a bare login prompt (the
    # "greeted with tty1" bug). The chroot MUST rewrite the drop-in to autologin the chosen user.
    s = idy.identity_chroot_sh()
    # The drop-in path is targeted...
    assert "getty@tty1.service.d/autologin.conf" in s
    # ...and the hardcoded `--autologin main` is rewritten to the resolved login.
    assert "--autologin main" in s                 # the stale token being replaced
    assert '--autologin $az_login' in s            # keyed on the chosen login
    # It is inside the rename branch (only rewritten when the login actually differs from main).
    assert 'if [ "$az_login" != "main" ]; then' in s


def test_chroot_is_valid_bash():
    _bash_ok(idy.identity_chroot_sh())
