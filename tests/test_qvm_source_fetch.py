"""qvm_source -- the GitHub FETCH path (clone timeout + offline guard).

These are the regression tests for the build's "idle / frozen / weird errors" bug. The 12-commit
migration replaced the inline `hypervisor` package with a `git clone` of michaelilgiaev/qvm run
mid-build (compiler step 8, _emit_desktop) UNDER the pinned progress bar and BEFORE the build has
resolved mirrors / the offline switch. The original clone used a bare `subprocess.run()` with NO
timeout, so a stalled network hung the whole build forever -- invisible, because the bar paints no
heartbeat for that step. These tests pin the fixes that convert that silent hang into a fast,
explicit failure:

  * EVERY git network call is bounded by _CLONE_TIMEOUT and a timeout raises QvmSourceError
    (never hangs). This is the single most important property of the module.
  * an OFFLINE build (AZZIO_OFFLINE) with no warm cache/qvm and no AZZIO_QVM_DIR fails IMMEDIATELY
    with an actionable message instead of attempting -- and waiting out -- a doomed clone.
  * git is invoked with a low-speed abort + no terminal prompt so a trickle or an auth prompt
    cannot hang either.

The real-network clone is NOT exercised (test_qvm_source.py covers emit against a fake checkout).
We drive the fetch seams with a fake `subprocess` / env, so the suite needs no clone and no server.
"""

from __future__ import annotations

import subprocess

import pytest

import qvm_source


# --- the clone/fetch timeout: a stall becomes a fast QvmSourceError, never a hang -----------

def test_run_git_passes_the_hard_timeout(monkeypatch):
    # Every git call MUST carry timeout=_CLONE_TIMEOUT. Without it a stalled connect blocks the
    # build forever (the original bug). Capture the kwargs subprocess.run is actually called with.
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(qvm_source.subprocess, "run", fake_run)
    qvm_source._run_git(["git", "version"], what="probe")
    assert seen.get("timeout") == qvm_source._CLONE_TIMEOUT, "git call must be timeout-bounded"
    assert seen.get("capture_output") is True
    # The hardened env must be threaded through (so git's own low-speed abort applies).
    assert "env" in seen and seen["env"].get("GIT_HTTP_LOW_SPEED_LIMIT") == "1024"


def test_run_git_converts_timeout_into_qvm_source_error(monkeypatch):
    # A TimeoutExpired from the subprocess must surface as a QvmSourceError (caught by
    # compiler.main and reported) -- NOT propagate as a bare TimeoutExpired, and above all NOT
    # hang. The message must name the repo and the timeout so the operator knows what happened.
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(qvm_source.subprocess, "run", fake_run)
    with pytest.raises(qvm_source.QvmSourceError) as ei:
        qvm_source._run_git(["git", "clone", "x"], what="git clone of qvm")
    msg = str(ei.value)
    assert "timed out" in msg
    assert str(qvm_source._CLONE_TIMEOUT) in msg
    assert qvm_source.QVM_REPO_URL in msg


def test_clone_timeout_is_bounded_and_overridable():
    # The default must be a real, finite bound (a hang is the bug) and stay modest so a dead
    # network fails in minutes, not never. It is read from AZZIO_QVM_CLONE_TIMEOUT at import.
    assert isinstance(qvm_source._CLONE_TIMEOUT, int)
    assert 0 < qvm_source._CLONE_TIMEOUT <= 600


def test_clone_surfaces_timeout_without_hanging(monkeypatch, tmp_path):
    # End-to-end on the clone path: if the underlying git clone stalls, _clone must raise
    # QvmSourceError (fast) rather than block. Fake run raises TimeoutExpired like a real stall.
    monkeypatch.setattr(qvm_source, "_have_git", lambda: True)

    def fake_run(cmd, **kw):
        # rm -rf (cleanup) has no timeout kwarg; let it pass. The git clone has one -> stall it.
        if "timeout" in kw:
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(qvm_source.subprocess, "run", fake_run)
    with pytest.raises(qvm_source.QvmSourceError):
        qvm_source._clone(tmp_path / "qvm")


# --- git env hardening: low-speed abort + no interactive prompt -----------------------------

def test_git_env_sets_low_speed_abort_and_no_prompt(monkeypatch):
    # GIT_HTTP_LOW_SPEED_* makes git abort a mid-transfer CRAWL (bytes trickling, so the process
    # is not "stuck" and the subprocess timeout would not fire). GIT_TERMINAL_PROMPT=0 stops a
    # private/auth remote from blocking on an unanswerable credential prompt under the PTY.
    monkeypatch.delenv("GIT_HTTP_LOW_SPEED_LIMIT", raising=False)
    monkeypatch.delenv("GIT_HTTP_LOW_SPEED_TIME", raising=False)
    env = qvm_source._git_env()
    assert env["GIT_HTTP_LOW_SPEED_LIMIT"] == "1024"
    assert env["GIT_HTTP_LOW_SPEED_TIME"] == "30"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_env_low_speed_matches_curl_download_threshold():
    # The qvm clone and the package download defend against the SAME slow origin, so their
    # give-up-on-a-trickle threshold must agree (1 KB/s). Guard that the git env mirrors the
    # curl --speed-limit/--speed-time the package downloader uses (pacman._DOWNLOAD_XFERCOMMAND).
    import pacman
    env = qvm_source._git_env()
    xfer = pacman._DOWNLOAD_XFERCOMMAND
    assert env["GIT_HTTP_LOW_SPEED_LIMIT"] == "1024" and "--speed-limit 1024" in xfer
    assert env["GIT_HTTP_LOW_SPEED_TIME"] == "30" and "--speed-time 30" in xfer


# --- offline guard: an offline build never attempts (nor waits on) a doomed clone -----------

def test_offline_arg_without_checkout_fails_fast_and_never_clones(monkeypatch, tmp_path):
    # THE real-build path: compiler threads offline=True (from cache_is_complete()) into
    # emit_plan()/ensure_checkout(). With no warm cache/qvm and no AZZIO_QVM_DIR, that must
    # raise IMMEDIATELY with an actionable message and NOT call git at all -- NOT fall through
    # to a clone that burns the whole timeout on a connect that cannot succeed offline.
    monkeypatch.delenv("AZZIO_OFFLINE", raising=False)   # prove the ARGUMENT drives it, not env
    monkeypatch.delenv("AZZIO_QVM_DIR", raising=False)
    monkeypatch.setattr(qvm_source, "QVM_CHECKOUT", tmp_path / "cache" / "qvm")

    called = {"clone": False}
    monkeypatch.setattr(qvm_source, "_clone",
                        lambda *a, **k: called.__setitem__("clone", True))

    with pytest.raises(qvm_source.QvmSourceError) as ei:
        qvm_source.ensure_checkout(offline=True)
    assert "offline" in str(ei.value).lower()
    assert called["clone"] is False, "offline build must NOT attempt a clone"


def test_emit_plan_forwards_offline_to_fail_fast(monkeypatch, tmp_path):
    # The seam the fix hangs on: emit_plan(offline) must forward the flag so the whole desktop
    # emit fails fast offline. (compiler.py calls qvm_source.emit_plan(offline).)
    monkeypatch.delenv("AZZIO_OFFLINE", raising=False)
    monkeypatch.delenv("AZZIO_QVM_DIR", raising=False)
    monkeypatch.setattr(qvm_source, "QVM_CHECKOUT", tmp_path / "cache" / "qvm")
    monkeypatch.setattr(qvm_source, "_clone",
                        lambda *a, **k: pytest.fail("offline emit_plan must NOT clone"))
    with pytest.raises(qvm_source.QvmSourceError):
        qvm_source.emit_plan(offline=True)


def test_offline_env_fallback_without_checkout_fails_fast(monkeypatch, tmp_path):
    # The manual-override fallback: with no offline ARGUMENT (None), AZZIO_OFFLINE=1 still
    # forces the fail-fast. (Lets an operator force it by hand even outside the compiler flow.)
    monkeypatch.setenv("AZZIO_OFFLINE", "1")
    monkeypatch.delenv("AZZIO_QVM_DIR", raising=False)
    monkeypatch.setattr(qvm_source, "QVM_CHECKOUT", tmp_path / "cache" / "qvm")
    called = {"clone": False}
    monkeypatch.setattr(qvm_source, "_clone",
                        lambda *a, **k: called.__setitem__("clone", True))
    with pytest.raises(qvm_source.QvmSourceError):
        qvm_source.ensure_checkout()   # no arg -> env fallback
    assert called["clone"] is False


def test_offline_guard_does_not_block_a_warm_checkout(monkeypatch, tmp_path):
    # The offline guard must only fire when there is NOTHING to use. A warm cache/qvm is the
    # supported offline path, so an offline build with a populated checkout returns it, no clone.
    monkeypatch.setenv("AZZIO_OFFLINE", "1")
    monkeypatch.delenv("AZZIO_QVM_DIR", raising=False)
    checkout = tmp_path / "cache" / "qvm"
    libs = checkout / "libraries"
    libs.mkdir(parents=True)
    (libs / "command_line_interface.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    monkeypatch.setattr(qvm_source, "QVM_CHECKOUT", checkout)
    monkeypatch.setattr(qvm_source, "_clone",
                        lambda *a, **k: pytest.fail("warm checkout must not trigger a clone"))

    assert qvm_source.ensure_checkout() == libs


def test_offline_guard_honours_azzio_qvm_dir_override(monkeypatch, tmp_path):
    # AZZIO_QVM_DIR is the air-gapped seam: even offline, an explicit override checkout is used
    # verbatim and the guard never fires (it is checked before the offline branch).
    monkeypatch.setenv("AZZIO_OFFLINE", "1")
    override = tmp_path / "elsewhere" / "qvm"
    libs = override / "libraries"
    libs.mkdir(parents=True)
    (libs / "command_line_interface.py").write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("AZZIO_QVM_DIR", str(override))
    monkeypatch.setattr(qvm_source, "_clone",
                        lambda *a, **k: pytest.fail("override must not trigger a clone"))

    assert qvm_source.ensure_checkout() == libs


def test_is_offline_argument_is_authoritative(monkeypatch):
    # The build-wide offline bool compiler threads in (offline = cache_is_complete()) is
    # AUTHORITATIVE: an explicit arg wins over the env, in BOTH directions. This is what makes
    # the real build flow reachable (nothing sets AZZIO_OFFLINE in-process before the qvm step).
    monkeypatch.setenv("AZZIO_OFFLINE", "1")       # env says offline...
    assert qvm_source._is_offline(False) is False  # ...but the arg wins (online)
    monkeypatch.delenv("AZZIO_OFFLINE", raising=False)
    assert qvm_source._is_offline(True) is True    # arg wins the other way too


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("", False), ("0", False), ("false", False), ("no", False),
])
def test_is_offline_env_fallback_truthiness(monkeypatch, val, expected):
    # When the arg is None (the no-arg emit_plan() contract), fall back to AZZIO_OFFLINE, read
    # like the C side's AZZIO_FORCE_LIVE (1/true/yes/on); unset/false means "online" so a
    # normal build is never mis-gated.
    monkeypatch.setenv("AZZIO_OFFLINE", val)
    assert qvm_source._is_offline() is expected
    assert qvm_source._is_offline(None) is expected


def test_is_offline_unset_is_online(monkeypatch):
    monkeypatch.delenv("AZZIO_OFFLINE", raising=False)
    assert qvm_source._is_offline() is False


# --- online path still works: a reachable clone is unchanged (fake git) ----------------------

def test_cold_cache_online_clones_once(monkeypatch, tmp_path):
    # With no warm checkout, online, _clone is called exactly once and its result is returned.
    # Fake _clone materialises the entry file so ensure_checkout's post-clone validation passes.
    monkeypatch.delenv("AZZIO_OFFLINE", raising=False)
    monkeypatch.delenv("AZZIO_QVM_DIR", raising=False)
    checkout = tmp_path / "cache" / "qvm"
    monkeypatch.setattr(qvm_source, "QVM_CHECKOUT", checkout)

    calls = []

    def fake_clone(dest):
        calls.append(dest)
        libs = qvm_source._checkout_libraries(dest)
        libs.mkdir(parents=True, exist_ok=True)
        (libs / "command_line_interface.py").write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(qvm_source, "_clone", fake_clone)
    libs = qvm_source.ensure_checkout()
    assert calls == [checkout]
    assert libs == qvm_source._checkout_libraries(checkout)
