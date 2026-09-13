"""packages.passwords.live_keyboard_line -- the LIVE keyboard/Caps-Lock line at the `passwords`
master-password prompts (step six, item 2).

`passwords` printed keyboard.keyboard_status_line() ("Keyboard: us   Caps Lock: off") ONCE before
each hidden getpass -- so it was STATIC, stale the instant the user toggles Caps Lock or switches
layout WHILE the prompt waits (exactly the bug `backup`/`unpack` had). Step six gives `passwords`
the SAME live line: live_keyboard_line.py (copied verbatim from packages/backup) repaints that one
row in place while the getpass blocks. These tests mirror the backup ones and pin:

  * OFF a tty (or when the cursor row can't be read) it degrades to a SINGLE static print with NO
    ANSI escape leaked (the pre-existing behaviour, safe on a pipe/redirect);
  * ON a (faked) tty it spins a daemon repaint thread that rewrites a FIXED row in place with the
    ANSI save/move/clear/restore sequence, runs the prompt callback, and STOPS + joins the thread;
  * the DSR-reply read TIMES OUT on a silent terminal (never hangs -- the handoff's hard rule);
  * EVERY getpass in passwords.py (create/confirm master, the re-encrypt pair, the unlock loop) AND
    in encrypt_passwords_text_tile.py routes through the live wrapper, not a bare getpass;
  * live_keyboard_line.py ships with the passwords package (flat-dir module discovery).

The module is BYTE-IDENTICAL to packages/backup/live_keyboard_line.py (a verbatim copy, the same
convention keyboard.py uses); the terminal is faked so this runs anywhere, including CI. The
passwords curses plaintext-store UI is SEPARATE and unaffected -- only the getpass prompts change.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import paths
from packages.passwords import live_keyboard_line as L

# The app modules (passwords.py / encrypt_passwords_text_tile.py) import each other by BARE
# top-level name because the launcher runs them with the passwords dir on sys.path. Mirror that
# here so importing them (to check their getpass routing) resolves those bare imports.
_PASSWORDS_DIR = Path(__file__).resolve().parents[1] / "libraries" / "packages" / "passwords"
if str(_PASSWORDS_DIR) not in sys.path:
    sys.path.insert(0, str(_PASSWORDS_DIR))


# --- the module is a verbatim copy of the backup one ------------------------

def test_passwords_live_keyboard_line_is_verbatim_copy_of_backup():
    """The passwords module is a byte-for-byte copy of packages/backup/live_keyboard_line.py (the
    handoff: copy it verbatim; both dirs' keyboard.py are already identical). This pins that they
    do not drift -- a fix to one must be mirrored to the other."""
    a = (paths.BACKUP_DIR / "live_keyboard_line.py").read_bytes()
    b = (paths.PASSWORDS_DIR / "live_keyboard_line.py").read_bytes()
    assert a == b, "passwords/live_keyboard_line.py must be a verbatim copy of the backup one"


# --- OFF-TTY: static fallback, no ANSI --------------------------------------

def test_off_tty_prints_static_line_once_and_no_ansi(monkeypatch):
    """When no cursor row can be read (off tty), the wrapper prints the status line exactly ONCE,
    runs the callback, and emits NO escape sequence (garbage on a pipe)."""
    monkeypatch.setattr(L, "_get_cursor_row", lambda out: None)
    buf = io.StringIO()
    calls = {"n": 0}

    def cb():
        calls["n"] += 1
        return "secret"

    result = L.prompt_with_live_keyboard_line(
        cb, lambda: "Keyboard: us   Caps Lock: off", out_stream=buf)

    assert result == "secret"
    assert calls["n"] == 1
    assert buf.getvalue() == "Keyboard: us   Caps Lock: off\n"
    assert "\033" not in buf.getvalue()


def test_off_tty_when_devtty_unopenable(monkeypatch):
    """Even if a row IS read, an unopenable /dev/tty degrades to the static path (still runs the
    callback, no thread, no escape sequence beyond the initial print)."""
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
    """A stand-in for the writable /dev/tty; records writes and no-ops close() so the wrapper can
    flush/close it while the test still reads the buffer."""

    def close(self):
        pass


def test_live_path_repaints_row_and_stops_thread(monkeypatch):
    """On a tty: a daemon thread repaints a FIXED row with the ANSI save/move/clear/restore
    sequence WHILE the prompt blocks, and is stopped + joined once it returns."""
    import time
    monkeypatch.setattr(L, "_get_cursor_row", lambda out: 8)   # cursor row 8 -> status on row 7
    fake_tty = _FakeTty()
    monkeypatch.setattr("builtins.open", lambda *a, **k: fake_tty)
    monkeypatch.setattr(L, "REFRESH_INTERVAL_SECS", 0.01)

    def cb():
        import threading
        live = [t for t in threading.enumerate() if t.is_alive() and t.daemon]
        assert live, "a daemon repaint thread must be alive while the prompt blocks"
        time.sleep(0.05)
        return "pw"

    out = io.StringIO()
    result = L.prompt_with_live_keyboard_line(
        cb, lambda: "Keyboard: us   Caps Lock: ON", out_stream=out)

    assert result == "pw"
    written = fake_tty.getvalue()
    assert "\033[s" in written and "\033[7;1H" in written
    assert "\033[2K" in written and "\033[u" in written
    assert "Keyboard: us   Caps Lock: ON" in written
    assert written.count("\033[7;1H") >= 2, written
    time.sleep(0.05)
    settled = fake_tty.getvalue()
    time.sleep(0.05)
    assert fake_tty.getvalue() == settled, "repaint thread kept running after the prompt returned"


def test_get_cursor_row_is_none_off_tty():
    """_get_cursor_row returns None when stdout is not a real terminal (a StringIO)."""
    assert L._get_cursor_row(io.StringIO()) is None


def test_get_cursor_row_does_not_hang_on_a_silent_terminal():
    """HANG GUARD (handoff: a prompt must NEVER block with no input). On a REAL tty whose peer
    never answers the DSR (\\033[6n) the reply read must TIME OUT and return None (static
    fallback), not block forever. Run it in a child whose stdin+stdout are a pty slave while the
    parent holds the master and never replies; the child must finish quickly."""
    import pty
    import subprocess

    child = (
        "import sys\n"
        f"sys.path.insert(0, {os.path.dirname(L.__file__)!r})\n"
        "import live_keyboard_line as L\n"
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
        os.close(slave)
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


# --- passwords.py routes EVERY getpass through the live wrapper --------------

def test_passwords_prompts_route_through_the_live_wrapper(monkeypatch):
    """Every master-password getpass in passwords.py -- the create+confirm pair (_prompt_new_master),
    the re-encrypt pair (_recover_stale_plaintext), and the unlock loop (main) -- goes through the
    live wrapper. We stub the wrapper to record each prompt (without blocking on input) and drive
    the three sites, proving none uses a bare getpass. The status source is keyboard_status_line."""
    import passwords as pwmod

    prompts = []

    def fake_wrapper(prompt_cb, status_cb):
        # The prompt_cb is `lambda: getpass.getpass(<PROMPT>)`; we don't call it (no blocking).
        prompts.append(status_cb())
        return "pw"

    monkeypatch.setattr(pwmod.live_keyboard_line,
                        "prompt_with_live_keyboard_line", fake_wrapper)

    # 1) create master (two prompts: create + confirm), both "pw" -> matches -> returns "pw".
    prompts.clear()
    assert pwmod._prompt_new_master() == "pw"
    assert len(prompts) == 2
    assert all(p.startswith("Keyboard:") for p in prompts)

    # 2) the unlock loop in main(): it calls _prompt_with_keyboard_line('Master password: ') then
    #    decrypts. Drive _prompt_with_keyboard_line directly (the loop's single prompt site).
    prompts.clear()
    assert pwmod._prompt_with_keyboard_line("Master password: ") == "pw"
    assert len(prompts) == 1


def test_passwords_recover_reencrypt_prompts_route_through_wrapper(tmp_path, monkeypatch):
    """The re-encrypt recovery path (_recover_stale_plaintext) also routes its master-password
    prompts through the live wrapper. Drive it with a stubbed wrapper + a fake round-trip verify so
    it re-encrypts and reports 'resolved' without any real gpg or blocking input."""
    import passwords as pwmod

    # A leftover plaintext to recover.
    plain = tmp_path / "passwords.txt"
    plain.write_text("site\tsecret\n")
    enc = tmp_path / "passwords.txt.gpg"

    prompts = []

    def fake_wrapper(prompt_cb, status_cb):
        prompts.append(status_cb())
        return "master"

    monkeypatch.setattr(pwmod.live_keyboard_line,
                        "prompt_with_live_keyboard_line", fake_wrapper)
    # Accept the recovery, then stub the crypto so no real gpg runs and the round-trip "verifies".
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    monkeypatch.setattr(pwmod, "_encrypt_text", lambda text, e, pw: None)
    monkeypatch.setattr(pwmod, "_verify_store", lambda e, text, pw: True)

    assert pwmod._recover_stale_plaintext(str(enc), str(plain)) == "resolved"
    # Two prompts (master + confirm) both routed through the wrapper.
    assert len(prompts) == 2
    assert all(p.startswith("Keyboard:") for p in prompts)


def test_encrypt_importer_prompts_route_through_the_live_wrapper(monkeypatch):
    """The optional bulk importer (encrypt_passwords_text_tile.py) routes its master-password
    create+confirm getpass pair through the live wrapper too."""
    import encrypt_passwords_text_tile as enc

    prompts = []

    def fake_wrapper(prompt_cb, status_cb):
        prompts.append(status_cb())
        return "pw"

    monkeypatch.setattr(enc.live_keyboard_line,
                        "prompt_with_live_keyboard_line", fake_wrapper)
    assert enc._prompt_with_keyboard_line("Master password: ") == "pw"
    assert len(prompts) == 1
    assert prompts[0].startswith("Keyboard:")


# --- ships with the package (module discovery) ------------------------------

def test_live_keyboard_line_module_ships():
    """live_keyboard_line.py is a real passwords source, so packaging discovery ships it flat to
    LIB_DIR (packaging.py discovers every .py -- no packaging edit needed)."""
    from packages.passwords import packaging as pw
    shipped = {e["dest"] for e in pw.emit_plan()}
    assert f"{pw.LIB_DIR}/live_keyboard_line.py" in shipped
    src = (paths.PASSWORDS_DIR / "live_keyboard_line.py").read_text(encoding="utf-8")
    assert "def prompt_with_live_keyboard_line(" in src


def test_passwords_py_and_encrypt_no_longer_static_print_before_getpass():
    """REGRESSION: the OLD static `print(keyboard_status_line()); getpass.getpass(...)` pattern is
    GONE from both files -- every getpass now goes through _prompt_with_keyboard_line. Pin that no
    bare `getpass.getpass(` remains OUTSIDE the adapter, and the adapter exists in both files."""
    for name in ("passwords.py", "encrypt_passwords_text_tile.py"):
        src = (paths.PASSWORDS_DIR / name).read_text(encoding="utf-8")
        assert "def _prompt_with_keyboard_line(" in src, f"{name} lost its live-line adapter"
        assert "prompt_with_live_keyboard_line(" in src
        # exactly ONE getpass.getpass call remains per file -- the one inside the adapter lambda.
        assert src.count("getpass.getpass(") == 1, (
            f"{name} still has a raw getpass outside the live-line adapter")
