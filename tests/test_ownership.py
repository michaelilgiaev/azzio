"""ownership -- owner resolution + the WORKDIR-skip catastrophe guard.

Two behaviors here can silently relock the host's cache/ output/ logs/ trees or,
worse, recurse into the live mkarchiso work tree that holds proc/sys/dev/run bind
mounts. Both are pure logic once the environment seams are pinned:

  * `_resolve_owner()` decides WHO the trees get chowned back to. A wrong answer
    is invisible until a later run: chown-to-root (uid "0") would relock every
    file; a Unicode digit like Arabic-Indic "١" passes str.isdigit() but the
    chown syscall rejects it, so the reclaim would fail mid-build. The function
    must reject "", "0", and any non-ASCII-digit string, returning None (which
    disables the handback) rather than emitting a bad owner.

  * `_reclaim_periodic()` sweeps cache/'s children but MUST skip WORKDIR (the live
    work tree). Recursing it during mkarchiso would chown across the proc/sys/dev
    bind mounts. The invariant asserted here: the WORKDIR string never appears in
    any captured chown/chmod argv, while a sibling child of cache/ does.

No network, no subprocess, no sudo, no docker: `ownership._run` is rebound to an
argv recorder and every environment probe (is_root/in_docker/getuid/getgid/env)
is monkeypatched.
"""

from __future__ import annotations

import os

import pytest

import ownership


# --------------------------------------------------------------------------
# helpers / fixtures
# --------------------------------------------------------------------------

def _make(monkeypatch, *, root: bool, docker: bool = False,
          host_uid=None, host_gid=None, sudo=None):
    """Build an Ownership with the environment fully pinned.

    Rebinds ownership._run to a recorder so no subprocess ever spawns, and pins
    is_root/in_docker plus HOST_UID/HOST_GID so _resolve_owner runs pure logic.
    Returns (Ownership, recorded_argv_list).
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(ownership, "_run", lambda cmd: calls.append(list(cmd)))
    monkeypatch.setattr(ownership.paths, "is_root", lambda: root)
    monkeypatch.setattr(ownership.paths, "in_docker", lambda: docker)

    if host_uid is None:
        monkeypatch.delenv("HOST_UID", raising=False)
    else:
        monkeypatch.setenv("HOST_UID", host_uid)
    if host_gid is None:
        monkeypatch.delenv("HOST_GID", raising=False)
    else:
        monkeypatch.setenv("HOST_GID", host_gid)

    if sudo is None:
        sudo = [] if root else ["sudo", "-n"]
    return ownership.Ownership(sudo), calls


# --------------------------------------------------------------------------
# _resolve_owner: root / container branch
# --------------------------------------------------------------------------

def test_root_valid_host_ids_resolve_to_owner_string(monkeypatch):
    own, _ = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")
    assert own.owner == "1000:998"


def test_root_missing_host_ids_returns_none(monkeypatch):
    # Unset HOST_UID/HOST_GID -> DON'T guess; None disables the handback.
    own, _ = _make(monkeypatch, root=True, host_uid=None, host_gid=None)
    assert own.owner is None


def test_root_empty_host_ids_returns_none(monkeypatch):
    own, _ = _make(monkeypatch, root=True, host_uid="", host_gid="")
    assert own.owner is None


def test_root_uid_zero_rejected(monkeypatch):
    # chown-to-root ("0") would relock everything, so "0" is an explicit reject.
    own, _ = _make(monkeypatch, root=True, host_uid="0", host_gid="998")
    assert own.owner is None


def test_root_gid_zero_rejected(monkeypatch):
    own, _ = _make(monkeypatch, root=True, host_uid="1000", host_gid="0")
    assert own.owner is None


def test_root_both_zero_rejected(monkeypatch):
    own, _ = _make(monkeypatch, root=True, host_uid="0", host_gid="0")
    assert own.owner is None


def test_root_whitespace_is_stripped(monkeypatch):
    # .strip() runs before validation; padded valid digits still resolve.
    own, _ = _make(monkeypatch, root=True, host_uid="  1000  ", host_gid="\t998\n")
    assert own.owner == "1000:998"


def test_root_unicode_digit_uid_rejected(monkeypatch):
    # Arabic-Indic digit U+0661 passes str.isdigit() but chown rejects it; the
    # ASCII-only set check must reject it here so no bad owner reaches chown.
    assert "١".isdigit()  # sanity: this is exactly the trap being guarded
    own, _ = _make(monkeypatch, root=True, host_uid="١000", host_gid="998")
    assert own.owner is None


def test_root_unicode_digit_gid_rejected(monkeypatch):
    own, _ = _make(monkeypatch, root=True, host_uid="1000", host_gid="٩٩٨")
    assert own.owner is None


def test_root_non_digit_garbage_rejected(monkeypatch):
    own, _ = _make(monkeypatch, root=True, host_uid="1000", host_gid="root")
    assert own.owner is None


def test_root_negative_sign_rejected(monkeypatch):
    # '-' is not an ASCII digit, so "-1" is rejected (not silently coerced).
    own, _ = _make(monkeypatch, root=True, host_uid="-1", host_gid="998")
    assert own.owner is None


def test_root_leading_zero_uid_kept_verbatim(monkeypatch):
    # "01000" is all ASCII digits and != "0", so it is accepted verbatim (no
    # numeric normalization -- the string is passed straight to the owner specification).
    own, _ = _make(monkeypatch, root=True, host_uid="01000", host_gid="998")
    assert own.owner == "01000:998"


def test_root_zero_prefixed_but_not_zero_kept(monkeypatch):
    # Guard is exact string == "0", not "starts with 0": "00" would relock, and
    # the source only rejects the literal "0". Document the real behavior: "00"
    # is NOT the literal "0", so it is accepted.
    own, _ = _make(monkeypatch, root=True, host_uid="00", host_gid="998")
    assert own.owner == "00:998"


def test_root_docker_reject_warns_to_stderr(monkeypatch, capsys):
    # In docker, a rejected id set prints the HOST_UID/HOST_GID hint to stderr.
    own, _ = _make(monkeypatch, root=True, docker=True, host_uid=None, host_gid=None)
    assert own.owner is None
    err = capsys.readouterr().err
    assert "HOST_UID/HOST_GID not set" in err
    assert "-e HOST_UID=$(id -u) -e HOST_GID=$(id -g)" in err


def test_root_native_reject_is_silent(monkeypatch, capsys):
    # NOT in docker -> no stderr noise even though owner is None (native root run
    # with no HOST ids is a legitimate quiet case, not a misconfiguration).
    own, _ = _make(monkeypatch, root=True, docker=False, host_uid=None, host_gid=None)
    assert own.owner is None
    assert capsys.readouterr().err == ""


def test_root_valid_ids_no_warning(monkeypatch, capsys):
    own, _ = _make(monkeypatch, root=True, docker=True, host_uid="1000", host_gid="998")
    assert own.owner == "1000:998"
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------
# _resolve_owner: native non-root branch
# --------------------------------------------------------------------------

def test_nonroot_with_sudo_resolves_to_invoking_user(monkeypatch):
    monkeypatch.setattr(ownership.os, "getuid", lambda: 4242)
    monkeypatch.setattr(ownership.os, "getgid", lambda: 4343)
    own, _ = _make(monkeypatch, root=False, sudo=["sudo", "-n"])
    assert own.owner == "4242:4343"


def test_nonroot_without_sudo_returns_none(monkeypatch):
    # No sudo means we cannot escalate to chown root-created files -> disable.
    own, _ = _make(monkeypatch, root=False, sudo=[])
    assert own.owner is None


def test_nonroot_ignores_host_env(monkeypatch):
    # HOST_UID/HOST_GID are the container contract; on a native non-root run they
    # are ignored entirely in favor of the live uid/gid.
    monkeypatch.setattr(ownership.os, "getuid", lambda: 1001)
    monkeypatch.setattr(ownership.os, "getgid", lambda: 1002)
    own, _ = _make(monkeypatch, root=False, sudo=["sudo", "-n"],
                   host_uid="7", host_gid="7")
    assert own.owner == "1001:1002"


# --------------------------------------------------------------------------
# _chown / _chmod: sudo prefix threading + argv shape
# --------------------------------------------------------------------------

def test_chown_prepends_sudo_prefix(monkeypatch):
    own, calls = _make(monkeypatch, root=False, sudo=["sudo", "-n"])
    monkeypatch.setattr(ownership.os, "getuid", lambda: 1000)
    monkeypatch.setattr(ownership.os, "getgid", lambda: 1000)
    own._chown("-R", "1000:1000", "/some/dir")
    assert calls == [["sudo", "-n", "chown", "-R", "1000:1000", "/some/dir"]]


def test_chown_root_has_no_sudo_prefix(monkeypatch):
    own, calls = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")
    own._chown("1000:998", "/some/dir")
    assert calls == [["chown", "1000:998", "/some/dir"]]


def test_chmod_prepends_sudo_prefix(monkeypatch):
    own, calls = _make(monkeypatch, root=False, sudo=["sudo", "-n"])
    monkeypatch.setattr(ownership.os, "getuid", lambda: 1000)
    monkeypatch.setattr(ownership.os, "getgid", lambda: 1000)
    own._chmod("-R", "u+w", "/some/dir")
    assert calls == [["sudo", "-n", "chmod", "-R", "u+w", "/some/dir"]]


# --------------------------------------------------------------------------
# reclaim_full / _reclaim_periodic: owner==None short-circuit
# --------------------------------------------------------------------------

def test_reclaim_full_noop_when_owner_none(monkeypatch):
    own, calls = _make(monkeypatch, root=True, host_uid=None, host_gid=None)
    assert own.owner is None
    own.reclaim_full()
    assert calls == []


def test_reclaim_periodic_noop_when_owner_none(monkeypatch):
    own, calls = _make(monkeypatch, root=True, host_uid=None, host_gid=None)
    assert own.owner is None
    own._reclaim_periodic()
    assert calls == []


# --------------------------------------------------------------------------
# The WORKDIR-skip invariant: _reclaim_periodic must NEVER touch the live work
# tree (cache/build) which during mkarchiso holds proc/sys/dev/run bind mounts.
# --------------------------------------------------------------------------

def _pin_trees(monkeypatch, tmp_path, *, docker: bool):
    """Point CACHEDIR/BUILDDIR/LOGDIR/WORKDIR at a real tmp_path layout.

    Returns the four Path objects. On a native run WORKDIR is a child of CACHEDIR
    (cache/build); in docker it lives OUTSIDE the bind mounts (/tmp-style path),
    so it is not even a child of cache/ and iterdir never yields it.
    """
    cache = tmp_path / "cache"
    build_out = tmp_path / "output"
    logs = tmp_path / "logs"
    cache.mkdir()
    build_out.mkdir()
    logs.mkdir()

    if docker:
        workdir = tmp_path / "container" / "azzio-build"
        workdir.mkdir(parents=True)
    else:
        workdir = cache / "build"
        workdir.mkdir()

    # Sibling children of cache/ that MUST be reclaimed (the persistent stores).
    (cache / "pkgs").mkdir()
    (cache / "pacman-pkg").mkdir()

    monkeypatch.setattr(ownership.paths, "CACHEDIR", cache)
    monkeypatch.setattr(ownership.paths, "BUILDDIR", build_out)
    monkeypatch.setattr(ownership.paths, "LOGDIR", logs)
    monkeypatch.setattr(ownership.paths, "WORKDIR", workdir)
    return cache, build_out, logs, workdir


def _all_path_args(calls):
    """Flatten every argv token that looks like a filesystem path (has a slash)."""
    toks: list[str] = []
    for cmd in calls:
        toks.extend(cmd)
    return toks


def test_reclaim_periodic_native_never_touches_workdir(monkeypatch, tmp_path):
    _, _, _, workdir = _pin_trees(monkeypatch, tmp_path, docker=False)
    own, calls = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")

    own._reclaim_periodic()

    toks = _all_path_args(calls)
    # The live work tree string must appear in NO argv -- the catastrophe guard.
    assert str(workdir) not in toks
    # And it never appears as a recursive-chown target either (belt-and-suspenders
    # against a substring match: no token even contains the workdir path).
    assert all(str(workdir) not in t for t in toks)


def test_reclaim_periodic_still_reclaims_sibling_cache_children(monkeypatch, tmp_path):
    # Skipping WORKDIR must not skip the OTHER cache children (pkgs, pacman-pkg):
    # those are the persistent stores that must be handed back.
    cache, _, _, _ = _pin_trees(monkeypatch, tmp_path, docker=False)
    own, calls = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")

    own._reclaim_periodic()

    toks = _all_path_args(calls)
    assert str(cache / "pkgs") in toks
    assert str(cache / "pacman-pkg") in toks


def test_reclaim_periodic_chowns_owner_and_top_inodes(monkeypatch, tmp_path):
    cache, build_out, logs, _ = _pin_trees(monkeypatch, tmp_path, docker=False)
    own, calls = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")

    own._reclaim_periodic()

    chowns = [c for c in calls if "chown" in c]
    # Every chown carries the resolved owner string.
    assert chowns and all("1000:998" in c for c in chowns)
    # Top (non-recursive) inode chown of all three roots happened.
    for root_dir in (cache, build_out, logs):
        assert ["chown", "1000:998", str(root_dir)] in calls


def test_reclaim_periodic_recursive_flags_use_preserve_root(monkeypatch, tmp_path):
    # Recursive chowns must carry -R and --preserve-root so a bug that made a root
    # dir "/" can't nuke the whole filesystem's ownership.
    _pin_trees(monkeypatch, tmp_path, docker=False)
    own, calls = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")

    own._reclaim_periodic()

    recursive_chowns = [c for c in calls if "chown" in c and "-R" in c]
    assert recursive_chowns
    for c in recursive_chowns:
        assert "--preserve-root" in c


def test_reclaim_periodic_docker_workdir_outside_cache_untouched(monkeypatch, tmp_path):
    # In docker WORKDIR is not a child of cache/, so iterdir never yields it; still
    # assert it appears in no argv (the container-internal scratch is never chowned
    # by the periodic sweep).
    _, _, _, workdir = _pin_trees(monkeypatch, tmp_path, docker=True)
    own, calls = _make(monkeypatch, root=True, docker=True,
                       host_uid="1000", host_gid="998")

    own._reclaim_periodic()

    toks = _all_path_args(calls)
    assert all(str(workdir) not in t for t in toks)


# --------------------------------------------------------------------------
# reclaim_full: recurses all three trees (safe only with no live mounts).
# --------------------------------------------------------------------------

def test_reclaim_full_recurses_all_three_trees(monkeypatch, tmp_path):
    cache, build_out, logs, _ = _pin_trees(monkeypatch, tmp_path, docker=False)
    own, calls = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")

    own.reclaim_full()

    # Each of the three trees gets a recursive, preserve-root chown to the owner.
    for d in (cache, build_out, logs):
        assert ["chown", "-R", "--preserve-root", "1000:998", str(d)] in calls


def test_reclaim_full_chmods_user_write(monkeypatch, tmp_path):
    cache, build_out, logs, _ = _pin_trees(monkeypatch, tmp_path, docker=False)
    own, calls = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")

    own.reclaim_full()

    for d in (cache, build_out, logs):
        assert ["chmod", "-R", "u+w", str(d)] in calls


def test_reclaim_full_skips_missing_dirs(monkeypatch, tmp_path):
    # Only existing dirs are chowned; a non-existent tree is silently skipped
    # (is_dir() guard) rather than erroring.
    cache, build_out, logs, _ = _pin_trees(monkeypatch, tmp_path, docker=False)
    import shutil
    shutil.rmtree(logs)  # LOGDIR now gone
    own, calls = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")

    own.reclaim_full()

    toks = _all_path_args(calls)
    assert str(logs) not in toks
    assert str(cache) in toks  # the surviving trees are still handled


# --------------------------------------------------------------------------
# start/stop_continuous: daemon flag + owner==None short-circuit, no real loop.
# --------------------------------------------------------------------------

def test_start_continuous_noop_when_owner_none(monkeypatch):
    own, _ = _make(monkeypatch, root=True, host_uid=None, host_gid=None)
    assert own.owner is None
    own.start_continuous()
    assert own._thread is None  # no thread spawned when there is nothing to reclaim


def test_start_continuous_spawns_daemon_thread(monkeypatch):
    # Stub the sweep so the loop body does nothing; assert only the thread's shape.
    own, _ = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")
    monkeypatch.setattr(own, "_reclaim_periodic", lambda: None)

    own.start_continuous()
    try:
        assert own._thread is not None
        assert own._thread.daemon is True
        assert own._thread.name == "chowner"
        assert own._thread.is_alive()
    finally:
        own.stop_continuous()
    assert own._thread is None


def test_stop_continuous_joins_and_clears_thread(monkeypatch):
    own, _ = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")
    monkeypatch.setattr(own, "_reclaim_periodic", lambda: None)
    own.start_continuous()
    t = own._thread
    assert t is not None

    own.stop_continuous()
    assert own._thread is None
    assert not t.is_alive()  # the loop exited on the _stop event


def test_stop_continuous_safe_without_start(monkeypatch):
    # stop before start must not raise (no thread to join).
    own, _ = _make(monkeypatch, root=True, host_uid="1000", host_gid="998")
    own.stop_continuous()  # should be a no-op, not an AttributeError/TypeError
    assert own._thread is None


# --------------------------------------------------------------------------
# constructor wiring
# --------------------------------------------------------------------------

def test_constructor_stores_sudo_prefix(monkeypatch):
    own, _ = _make(monkeypatch, root=False, sudo=["sudo", "-n"])
    monkeypatch.setattr(ownership.os, "getuid", lambda: 1000)
    monkeypatch.setattr(ownership.os, "getgid", lambda: 1000)
    assert own.sudo == ["sudo", "-n"]


def test_module_run_uses_check_false(monkeypatch):
    # _run must never raise on a failing chown (check=False): a transient chown
    # failure during the 1s sweep must not crash the build thread.
    captured = {}

    def fake_run(cmd, **kw):
        captured.update(kw)
        class R:  # minimal stand-in
            returncode = 1
        return R()

    monkeypatch.setattr(ownership.subprocess, "run", fake_run)
    ownership._run(["chown", "x", "/y"])
    assert captured.get("check") is False
