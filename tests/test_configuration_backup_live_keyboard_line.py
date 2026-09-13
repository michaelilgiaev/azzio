"""packages.backup.live_keyboard_line -- the LIVE keyboard/Caps-Lock line at the passphrase
prompt (step five, item 2).

The `backup` and `unpack` commands print "Keyboard: us   Caps Lock: off" before every getpass
so a wrong layout / stuck Caps Lock is visible before the hidden passphrase is typed. Step five
requires that line to be LIVE -- it must repaint itself while the prompt blocks so a Caps Lock
toggle or a layout switch shows up immediately (mirroring data/backup.py's update_layout_line
daemon thread). These tests pin:

  * OFF a tty (or when the cursor row can't be read) it degrades to a SINGLE static print with
    NO ANSI escape leaked (the pre-existing behaviour, safe on a pipe/redirect);
  * ON a (faked) tty it starts a daemon repaint thread that rewrites a FIXED row in place with
    the ANSI save/move/clear/restore sequence, runs the prompt callback, and STOPS + joins the
    thread around the prompt (so the line is live only while waiting);
  * archive.prompt_passphrase drives the prompt THROUGH this live wrapper (both the create and
    confirm prompts, and the single unpack prompt), so both commands get the live line.

Stdlib only; the terminal is faked (no real /dev/tty needed) so it runs anywhere, including CI.
"""

from __future__ import annotations

import io
import os
import sys
import time

import paths
from packages.backup import live_keyboard_line as L


# --- OFF-TTY: static fallback, no ANSI --------------------------------------

def test_off_tty_prints_static_line_once_and_no_ansi(monkeypatch):
    """When no cursor row can be read (off tty), the wrapper prints the status line exactly
    ONCE, runs the callback, and emits NO escape sequence (garbage on a pipe)."""
    monkeypatch.setattr(L, "_get_cursor_row", lambda out: None)
    buf = io.StringIO()
    calls = {"n": 0}

    def cb():
        calls["n"] += 1
        return "secret"

    result = L.prompt_with_live_keyboard_line(
        cb, lambda: "Keyboard: us   Caps Lock: off", out_stream=buf)

    assert result == "secret"          # callback's return is passed straight back
    assert calls["n"] == 1             # prompt ran exactly once
    assert buf.getvalue() == "Keyboard: us   Caps Lock: off\n"
    assert "\033" not in buf.getvalue()  # no ANSI leaked into a non-terminal


def test_off_tty_when_devtty_unopenable(monkeypatch):
    """Even if a row IS read, an unopenable /dev/tty degrades to the static path (still runs
    the callback, no thread, no escape sequence beyond the initial print)."""
    monkeypatch.setattr(L, "_get_cursor_row", lambda out: 5)

    def _no_tty(*a, **k):
        raise OSError("no controlling tty")

    monkeypatch.setattr("builtins.open", _no_tty)
    buf = io.StringIO()
    result = L.prompt_with_live_keyboard_line(
        lambda: "typed", lambda: "Keyboard: us", out_stream=buf)
    assert result == "typed"
    assert buf.getvalue() == "Keyboard: us\n"


# --- ON A (FAKED) TTY: live repaint thread ----------------------------------

class _FakeTty(io.StringIO):
    """A stand-in for the writable /dev/tty. Records everything written so a test can assert
    the ANSI repaint sequence was emitted; close() is a no-op so the wrapper can flush/close
    it freely."""

    def close(self):  # keep the buffer readable after the wrapper closes it
        pass


def test_live_path_repaints_row_and_stops_thread(monkeypatch):
    """On a tty: a daemon thread repaints a FIXED row with the ANSI save/move/clear/restore
    sequence WHILE the prompt blocks, and is stopped + joined once it returns."""
    # Pretend the cursor is on row 8 -> the status line sits on row 7 (row-1).
    monkeypatch.setattr(L, "_get_cursor_row", lambda out: 8)
    fake_tty = _FakeTty()
    monkeypatch.setattr("builtins.open", lambda *a, **k: fake_tty)
    # Speed the repaint up so a couple land during the short blocking callback.
    monkeypatch.setattr(L, "REFRESH_INTERVAL_SECS", 0.01)

    seen_thread = {}

    def cb():
        # While we are "typing", the repaint thread should be alive and painting.
        import threading
        live = [t for t in threading.enumerate() if t.is_alive() and t.daemon]
        seen_thread["any_daemon_alive"] = bool(live)
        time.sleep(0.05)   # let several repaints happen
        return "pw"

    out = io.StringIO()
    result = L.prompt_with_live_keyboard_line(
        cb, lambda: "Keyboard: us   Caps Lock: ON", out_stream=out)

    assert result == "pw"
    written = fake_tty.getvalue()
    # The exact in-place rewrite sequence for row 7: save, move to (7;1), clear line, text,
    # restore. (\033[s ... \033[7;1H \033[2K <status> \033[u)
    assert "\033[s" in written and "\033[7;1H" in written
    assert "\033[2K" in written and "\033[u" in written
    assert "Keyboard: us   Caps Lock: ON" in written
    # At least two repaints landed while blocking (the loop actually ran, not just the final).
    assert written.count("\033[7;1H") >= 2, written
    # And the wrapper stopped the thread: no lingering repaint after return (give it a beat).
    time.sleep(0.05)
    settled = fake_tty.getvalue()
    time.sleep(0.05)
    assert fake_tty.getvalue() == settled, "repaint thread kept running after the prompt returned"


def test_get_cursor_row_is_none_off_tty():
    """_get_cursor_row returns None when stdout is not a real terminal (a StringIO), so the
    wrapper degrades. (Direct unit of the tty gate.)"""
    assert L._get_cursor_row(io.StringIO()) is None


def test_get_cursor_row_does_not_hang_on_a_silent_terminal(tmp_path):
    """HANG GUARD (handoff: a prompt must NEVER block with no input). On a REAL tty whose peer
    never answers the DSR (\\033[6n) -- e.g. a tmux/screen pane, or a pty with no responder --
    the reply read must TIME OUT and return None (static fallback), not block forever.

    We run _get_cursor_row in a child whose stdin AND stdout are a pty SLAVE (so both isatty()
    -> the read path is actually entered), while the parent holds the master and NEVER writes a
    reply. The child must finish quickly (return None); a regression to the old unbounded
    read(1) would hang until the subprocess timeout fires and raise TimeoutExpired here."""
    import pty
    import subprocess

    child = (
        "import sys\n"
        f"sys.path.insert(0, {os.path.dirname(L.__file__)!r})\n"
        "import live_keyboard_line as L\n"
        # Both stdin and stdout are the pty slave here -> _isatty passes for both.
        "row = L._get_cursor_row(sys.stdout)\n"
        "sys.stderr.write('ROW=' + repr(row))\n"
        "sys.stderr.flush()\n"
    )
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", child],
            stdin=slave, stdout=slave, stderr=subprocess.PIPE, text=True,
        )
        os.close(slave)  # the child owns the slave now
        # The parent deliberately does NOT answer the DSR. The child must still exit promptly
        # thanks to the select() timeout; give it a generous ceiling so a slow CI box is fine,
        # but far below "hangs forever". A regression raises TimeoutExpired below.
        try:
            _out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AssertionError(
                "_get_cursor_row hung on a silent terminal (no DSR-reply timeout)")
        assert "ROW=None" in err, f"expected a None fallback, got: {err!r}"
    finally:
        try:
            os.close(master)
        except OSError:
            pass


# --- archive.prompt_passphrase drives BOTH prompts through the live wrapper ---

def test_archive_prompt_passphrase_uses_live_wrapper(monkeypatch):
    """Both the CREATE and CONFIRM getpass prompts (confirm=True) go through the live wrapper,
    so the live line is shown at each. We stub the wrapper to record its prompts and return a
    fixed passphrase, proving prompt_passphrase routes through it (not a bare getpass)."""
    from packages.backup import archive

    prompts = []

    def fake_wrapper(prompt_cb, status_cb, out_stream=None):
        # Record which prompt string getpass would have used, without blocking on input.
        prompts.append(status_cb())  # status source is wired in
        return "matching"

    monkeypatch.setattr(archive.live_keyboard_line,
                        "prompt_with_live_keyboard_line", fake_wrapper)
    # confirm=True -> create + confirm, both matching -> returns the passphrase.
    result = archive.prompt_passphrase(confirm=True)
    assert result == "matching"
    # Two prompts routed through the live wrapper (create + confirm).
    assert len(prompts) == 2
    # The status source is keyboard.keyboard_status_line (starts with "Keyboard:").
    assert all(p.startswith("Keyboard:") for p in prompts)


def test_archive_unpack_single_prompt_uses_live_wrapper(monkeypatch):
    """The single unpack prompt (confirm=False) also routes through the live wrapper (one
    prompt, no confirm)."""
    from packages.backup import archive

    count = {"n": 0}

    def fake_wrapper(prompt_cb, status_cb, out_stream=None):
        count["n"] += 1
        return "onepass"

    monkeypatch.setattr(archive.live_keyboard_line,
                        "prompt_with_live_keyboard_line", fake_wrapper)
    result = archive.prompt_passphrase(confirm=False)
    assert result == "onepass"
    assert count["n"] == 1   # exactly one prompt for the decrypt path


# --- ships with the package (module discovery) ------------------------------

def test_live_keyboard_line_module_ships(monkeypatch):
    """live_keyboard_line.py is a real backup source, so packaging discovery ships it flat to
    LIB_DIR (no packaging edit needed)."""
    from packages.backup import packaging as bk
    shipped = {e["dest"] for e in bk.emit_plan()}
    assert f"{bk.LIB_DIR}/live_keyboard_line.py" in shipped
    src = (paths.BACKUP_DIR / "live_keyboard_line.py").read_text(encoding="utf-8")
    assert "def prompt_with_live_keyboard_line(" in src
