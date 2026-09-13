"""The FN media controls -- `azzio volume` / `azzio brightness` / `azzio machine`, the
centered cyan OSD bar, and the OpenBox FN keybinds that drive them.

The PROMPT: add volume + brightness FN controls to the `azzio` command line interface. Resolve the machine's FN
mapping (a PC's FN+F2/F3 are volume; a laptop's are brightness), make BRIGHTNESS a laptop-only
option (a PC has no backlight), add a "Machine Type" surface that displays PC/Laptop and lets
the user HARD-SWITCH it, and show a centered cyan (the Azzio logo cyan) on-screen bar at 100%
with 7.5% steps when volume/brightness change -- reusing the speech-to-text indicator's UI.

These tests pin, against the BUNDLED command line interface (the /usr/local/bin/azzio artifact) + the standalone
OSD script + the OpenBox wiring:

  * machine-type detection (backlight, then DMI chassis) and the persistent hard override;
  * the 7.5% step + 0..100 clamp for volume and brightness;
  * brightness is gated to laptops (honouring the override), volume is not;
  * the OSD is centered, draws a cyan bar + a volume/brightness icon, self-dismisses;
  * the OpenBox rc.xml binds the XF86 audio/brightness keysyms to the subcommands, and the OSD
    script ships (executable, pinned) next to the terminal user interface binary.

The command line interface is exercised via its bundle (packages.azzio.bundle.bundle_source) executed in one
namespace -- exactly the artifact the compiler ships -- so the tests drive the real functions.
"""

from __future__ import annotations

import json
import subprocess
import types

import pytest

from packages.azzio.bundle import bundle_source, MODULE_ORDER
from packages import openbox as desktop
import paths
import profile as profiledef


def _command_line_interface():
    """Exec the bundled azzio command line interface in a fresh module namespace (as shipped)."""
    mod = types.ModuleType("azzio_cli_media_test")
    exec(compile(bundle_source(), "azzio_command_line_interface", "exec"), mod.__dict__)
    return mod


# --- the modules are bundled in a working order -----------------------------

def test_machine_and_media_are_bundled_before_the_cli():
    """machine.py + media.py must be in the bundle, and BEFORE command_line_interface.py (whose main()
    dispatches to them). machine.py must precede media.py (media's brightness gate calls
    machine.is_laptop() by bare name)."""
    for mod in ("machine.py", "media.py"):
        assert mod in MODULE_ORDER, f"{mod} not bundled"
        assert MODULE_ORDER.index(mod) < MODULE_ORDER.index("command_line_interface.py")
    assert MODULE_ORDER.index("machine.py") < MODULE_ORDER.index("media.py")


def test_dispatch_branches_exist_in_main():
    """`azzio volume|brightness|machine ...` must be real top-level dispatch branches, and the
    top-level usage must advertise them."""
    src = desktop.azzio_command_line_interface()
    assert 'cmd == "volume"' in src and "return cmd_volume(argv[1:])" in src
    assert 'cmd == "brightness"' in src and "return cmd_brightness(argv[1:])" in src
    assert 'cmd == "machine"' in src and "return cmd_machine(argv[1:])" in src
    # advertised in usage()
    assert "volume <up|down|mute|get>" in src
    assert "brightness <up|down|get>" in src
    assert "machine [--pc|--laptop|--auto]" in src


# --- machine-type detection + the hard switch -------------------------------

def test_machine_default_is_pc_with_no_signals(monkeypatch):
    """With no backlight and no DMI chassis, autodetection falls back to PC (a machine with no
    backlight has no brightness to control)."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_has_backlight", lambda: False)
    monkeypatch.setattr(cli, "_chassis_is_laptop", lambda: None)
    monkeypatch.setattr(cli, "_read_override", lambda: None)
    assert cli._detect_machine_type() == "PC"
    assert cli.machine_type() == "PC"
    assert cli.is_laptop() is False


def test_machine_backlight_means_laptop(monkeypatch):
    """A real backlight under /sys/class/backlight => Laptop (it can control its own screen),
    and it wins over a (contradicting) desktop chassis code."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_has_backlight", lambda: True)
    monkeypatch.setattr(cli, "_chassis_is_laptop", lambda: False)   # DMI says desktop...
    monkeypatch.setattr(cli, "_read_override", lambda: None)
    assert cli._detect_machine_type() == "Laptop"                  # ...backlight still wins
    assert cli.is_laptop() is True


def test_machine_chassis_laptop_codes(monkeypatch):
    """When there is no backlight, the DMI chassis code decides: 9/10 (laptop/notebook) =>
    Laptop, 3 (desktop) => PC."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_has_backlight", lambda: False)
    monkeypatch.setattr(cli, "_read_override", lambda: None)
    for code in ("8", "9", "10", "11", "14", "30", "31", "32"):
        assert code in cli._LAPTOP_CHASSIS_CODES
    # a laptop code -> Laptop
    monkeypatch.setattr(cli, "_chassis_is_laptop", lambda: True)
    assert cli._detect_machine_type() == "Laptop"
    # a desktop code -> PC
    monkeypatch.setattr(cli, "_chassis_is_laptop", lambda: False)
    assert cli._detect_machine_type() == "PC"


def test_machine_hard_override_persists_and_wins(tmp_path, monkeypatch):
    """`azzio machine --pc/--laptop` writes ~/.config/azzio/machine-type and that override
    WINS over autodetection; `--auto` clears it. Point the pointer file at a temp home."""
    cli = _command_line_interface()
    state = tmp_path / ".config" / "azzio" / "machine-type"
    monkeypatch.setattr(cli, "_machine_state_file", lambda: str(state))
    # autodetect would say PC (no backlight, no/という chassis) -- stub it so the test is host-free
    monkeypatch.setattr(cli, "_detect_machine_type", lambda: "PC")

    # force Laptop -> file written, effective type flips, brightness gate opens
    assert cli.cmd_machine(["--laptop"]) == 0
    assert state.read_text().strip() == "Laptop"
    assert cli.machine_type() == "Laptop"
    assert cli.is_laptop() is True

    # force PC
    assert cli.cmd_machine(["--pc"]) == 0
    assert state.read_text().strip() == "PC"
    assert cli.machine_type() == "PC"

    # --auto clears the override -> back to autodetection (PC)
    assert cli.cmd_machine(["--auto"]) == 0
    assert not state.exists()
    assert cli.machine_type() == "PC"


def test_machine_status_and_unknown_option(tmp_path, monkeypatch, capsys):
    """Bare `azzio machine` prints the recognised type; an unknown option is rc 2."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_machine_state_file", lambda: str(tmp_path / "mt"))
    monkeypatch.setattr(cli, "_detect_machine_type", lambda: "Laptop")
    assert cli.cmd_machine([]) == 0
    out = capsys.readouterr().out
    assert "Machine type: Laptop" in out
    assert "autodetected" in out
    assert cli.cmd_machine(["--bogus"]) == 2
    assert "unknown option" in capsys.readouterr().err


# --- the 7.5% step + 0..100 clamp -------------------------------------------

def test_step_is_seven_and_a_half_percent():
    """The spec: each increase/decrease is 7.5%, over a 0..100 range."""
    cli = _command_line_interface()
    assert cli.MEDIA_STEP_PERCENT == 7.5
    assert cli.MEDIA_MIN_PERCENT == 0.0
    assert cli.MEDIA_MAX_PERCENT == 100.0
    # 100 -> 92.5 -> 85 stepping down; and back up
    assert cli._step_percent(100.0, -1) == 92.5
    assert cli._step_percent(92.5, -1) == 85.0
    assert cli._step_percent(85.0, +1) == 92.5


def test_step_clamps_at_the_edges():
    """A press can never push past 100 or below 0."""
    cli = _command_line_interface()
    assert cli._step_percent(97.0, +1) == 100.0     # 97 + 7.5 clamps to 100
    assert cli._step_percent(100.0, +1) == 100.0
    assert cli._step_percent(3.0, -1) == 0.0        # 3 - 7.5 clamps to 0
    assert cli._step_percent(0.0, -1) == 0.0


# --- volume: steps, mutes, shows the OSD ------------------------------------

def test_volume_up_steps_and_shows_osd(monkeypatch, capsys):
    """`azzio volume up` reads the current level, writes current+7.5 (clamped), and pops the
    OSD. Backends + OSD are stubbed so the test is host-free."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_volume_read", lambda: (50.0, False))
    written = {}
    monkeypatch.setattr(cli, "_volume_write", lambda p: (written.__setitem__("p", p), True)[1])
    osd = {}
    monkeypatch.setattr(cli, "_show_osd", lambda kind, pct, muted=False: osd.update(kind=kind, pct=pct))
    assert cli.cmd_volume(["up"]) == 0
    assert written["p"] == 57.5
    assert osd == {"kind": "volume", "pct": 57.5}
    assert "Volume: 58%" in capsys.readouterr().out   # rounded for display


def test_volume_down_and_mute(monkeypatch):
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_volume_read", lambda: (30.0, False))
    seen = {}
    monkeypatch.setattr(cli, "_volume_write", lambda p: (seen.__setitem__("p", p), True)[1])
    monkeypatch.setattr(cli, "_show_osd", lambda *a, **k: None)
    assert cli.cmd_volume(["down"]) == 0
    assert seen["p"] == 22.5
    # mute toggles via the backend and shows the OSD with the muted flag
    toggled = {}
    monkeypatch.setattr(cli, "_volume_toggle_mute", lambda: (toggled.__setitem__("t", True), True)[1])
    monkeypatch.setattr(cli, "_volume_read", lambda: (22.5, True))
    shown = {}
    monkeypatch.setattr(cli, "_show_osd", lambda kind, pct, muted=False: shown.update(muted=muted))
    assert cli.cmd_volume(["mute"]) == 0
    assert toggled.get("t") is True
    assert shown.get("muted") is True


def test_volume_no_backend_is_error(monkeypatch, capsys):
    """With neither wpctl nor amixer, a volume change reports an error (rc 1), not a crash."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_volume_read", lambda: (0.0, False))
    monkeypatch.setattr(cli, "_volume_write", lambda p: False)
    monkeypatch.setattr(cli, "_show_osd", lambda *a, **k: None)
    assert cli.cmd_volume(["up"]) == 1
    assert "no audio backend" in capsys.readouterr().err


def test_volume_get_prints_percent(monkeypatch, capsys):
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_volume_read", lambda: (42.0, False))
    assert cli.cmd_volume(["get"]) == 0
    assert capsys.readouterr().out.strip() == "42"


# --- brightness: LAPTOP ONLY, steps, shows the OSD --------------------------

def test_brightness_is_laptop_only(monkeypatch, capsys):
    """On a PC (is_laptop False), `azzio brightness up/down` does NOTHING and says so (rc 1) --
    brightness is a laptop-only control. The backlight setter is never called."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "is_laptop", lambda: False)
    called = {"write": False}
    monkeypatch.setattr(cli, "_brightness_write", lambda p: called.__setitem__("write", True) or True)
    assert cli.cmd_brightness(["up"]) == 1
    assert called["write"] is False
    err = capsys.readouterr().err
    assert "laptop-only" in err


def test_brightness_up_on_laptop_steps_and_shows_osd(monkeypatch, capsys):
    """On a laptop, `azzio brightness up` steps 7.5% off the current backlight reading and pops
    the OSD."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "is_laptop", lambda: True)
    monkeypatch.setattr(cli, "_brightness_read", lambda: 40.0)
    written = {}
    monkeypatch.setattr(cli, "_brightness_write", lambda p: (written.__setitem__("p", p), True)[1])
    osd = {}
    monkeypatch.setattr(cli, "_show_osd", lambda kind, pct, muted=False: osd.update(kind=kind, pct=pct))
    assert cli.cmd_brightness(["up"]) == 0
    assert written["p"] == 47.5
    assert osd == {"kind": "brightness", "pct": 47.5}
    assert "Brightness: 48%" in capsys.readouterr().out


def test_brightness_get_is_na_on_a_pc(monkeypatch, capsys):
    """`azzio brightness get` always exits 0; on a PC (no backlight) it prints n/a rather than
    erroring, so a caller can query without tripping the laptop gate."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli, "_brightness_read", lambda: None)
    assert cli.cmd_brightness(["get"]) == 0
    assert capsys.readouterr().out.strip() == "n/a"


def test_brightness_step_never_blanks_the_panel(monkeypatch):
    """The backlight write keeps a raw value of at least 1 so a "down" step never drives the
    panel to pure black (unrecoverable without the keys). Drive _brightness_write against a fake
    sysfs device and assert the written raw is >= 1 even at 0%."""
    cli = _command_line_interface()
    writes = []

    class _FakeWritable:
        """A tiny file stand-in that records everything written to it, and survives the
        `with open(...) as fh` close (a StringIO would raise once closed)."""
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, s):
            writes.append(s)

        def read(self):
            return "100"

    monkeypatch.setattr(cli, "_backlight_device", lambda: "/sys/class/backlight/fake")

    real_open = open

    def fake_open(path, *a, **k):
        if path.endswith("max_brightness") or path.endswith("brightness"):
            return _FakeWritable()
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(cli.os, "access", lambda p, m: True)   # pretend we can write directly
    assert cli._brightness_write(0.0) is True
    assert writes, "nothing was written to the backlight"
    assert int(writes[-1]) >= 1                                # never a raw 0 (black panel)


# --- `set <N>`: a PRECISE level (the follow-up spec) ------------------------

def test_parse_percent_accepts_number_percent_and_clamps():
    """`set` parses an int/float, optional trailing '%', and clamps to 0..100; junk -> None."""
    cli = _command_line_interface()
    assert cli._parse_percent("65") == 65.0
    assert cli._parse_percent("65%") == 65.0
    assert cli._parse_percent(" 12.5 ") == 12.5
    assert cli._parse_percent("120") == 100.0     # clamp high
    assert cli._parse_percent("-5") == 0.0        # clamp low
    assert cli._parse_percent("nope") is None


def test_volume_set_writes_precise_level_and_shows_osd(monkeypatch, capsys):
    """The follow-up spec: `azzio volume` should let me select PRECISELY how much. `volume set N`
    writes exactly N (clamped) and pops the OSD; a missing/bad N is rc 2."""
    cli = _command_line_interface()
    written = {}
    monkeypatch.setattr(cli, "_volume_write", lambda p: (written.__setitem__("p", p), True)[1])
    osd = {}
    monkeypatch.setattr(cli, "_show_osd", lambda kind, pct, muted=False: osd.update(kind=kind, pct=pct))
    assert cli.cmd_volume(["set", "65"]) == 0
    assert written["p"] == 65.0
    assert osd == {"kind": "volume", "pct": 65.0}
    assert "Volume: 65%" in capsys.readouterr().out
    # a missing percent is a usage error (rc 2)
    assert cli.cmd_volume(["set"]) == 2
    assert "set needs a percent" in capsys.readouterr().err


def test_brightness_set_is_laptop_only(monkeypatch, capsys):
    """`brightness set N` is gated to laptops (like up/down): a PC refuses (rc 1, no write); a
    laptop writes exactly N."""
    cli = _command_line_interface()
    written = {"p": None}
    monkeypatch.setattr(cli, "_brightness_write", lambda p: (written.__setitem__("p", p), True)[1])
    monkeypatch.setattr(cli, "_show_osd", lambda *a, **k: None)
    # PC -> refused, nothing written
    monkeypatch.setattr(cli, "is_laptop", lambda: False)
    assert cli.cmd_brightness(["set", "40"]) == 1
    assert written["p"] is None
    assert "laptop-only" in capsys.readouterr().err
    # laptop -> writes exactly 40
    monkeypatch.setattr(cli, "is_laptop", lambda: True)
    assert cli.cmd_brightness(["set", "40"]) == 0
    assert written["p"] == 40.0


# --- the one-time defaults seed (50% volume / 100% brightness) --------------

def test_media_init_seeds_50_and_100_once(tmp_path, monkeypatch):
    """The follow-up spec: the pre/post-install default is 50% volume and 100% brightness, applied
    ONCE; after the user configures anything it persists. `media-init` seeds 50/100 (brightness
    only on a laptop), drops a marker, and on a SECOND run does nothing (so a later user level is
    never clobbered)."""
    cli = _command_line_interface()
    assert cli.MEDIA_DEFAULT_VOLUME == 50.0
    assert cli.MEDIA_DEFAULT_BRIGHTNESS == 100.0
    marker = tmp_path / ".config" / "azzio" / "media-seeded"
    monkeypatch.setattr(cli, "_media_seed_file", lambda: str(marker))
    vols, brs = [], []
    monkeypatch.setattr(cli, "_volume_write", lambda p: (vols.append(p), True)[1])
    monkeypatch.setattr(cli, "_brightness_write", lambda p: (brs.append(p), True)[1])
    monkeypatch.setattr(cli, "is_laptop", lambda: True)      # laptop: brightness seeded too
    # first run: seeds 50/100 and writes the marker
    assert cli.cmd_media_init([]) == 0
    assert vols == [50.0] and brs == [100.0]
    assert marker.exists()
    # second run: marker present -> NOTHING re-applied (user's later choice is respected)
    assert cli.cmd_media_init([]) == 0
    assert vols == [50.0] and brs == [100.0]      # unchanged


def test_media_init_skips_brightness_on_a_pc(tmp_path, monkeypatch):
    """On a PC (no backlight) media-init seeds the volume but NOT brightness (a PC has none)."""
    cli = _command_line_interface()
    marker = tmp_path / "media-seeded"
    monkeypatch.setattr(cli, "_media_seed_file", lambda: str(marker))
    vols, brs = [], []
    monkeypatch.setattr(cli, "_volume_write", lambda p: (vols.append(p), True)[1])
    monkeypatch.setattr(cli, "_brightness_write", lambda p: (brs.append(p), True)[1])
    monkeypatch.setattr(cli, "is_laptop", lambda: False)
    assert cli.cmd_media_init([]) == 0
    assert vols == [50.0]
    assert brs == []          # no backlight on a PC -> brightness left alone


def test_media_init_is_wired_into_dispatch_and_autostart():
    """`azzio media-init` is a real dispatch branch, and the OpenBox autostart runs it once at
    login (both live and installed sessions) so a fresh machine boots at the defaults."""
    src = desktop.azzio_command_line_interface()
    assert 'cmd == "media-init"' in src and "return cmd_media_init(argv[1:])" in src
    for autostart in (desktop.openbox_autostart(), desktop.openbox_autostart_installed()):
        assert "media-init" in autostart


def test_autostart_shortens_the_fn_hold_autorepeat():
    """The follow-up spec: holding an FN key has too long a delay before it kicks into fast drag;
    make it wait a little less. The autostart shortens the X autorepeat delay via `xset r rate`."""
    for autostart in (desktop.openbox_autostart(), desktop.openbox_autostart_installed()):
        assert "xset r rate" in autostart
    # xset must be shipped for the (guarded) line to actually take effect on the guest
    pkgs = (paths.PACKAGES_FILE).read_text(encoding="utf-8")
    assert "xorg-xset" in pkgs


# --- the OSD is a C/Xlib program: bottom-middle, cyan, iconed, no-flicker, draggable ---------
# The OSD was REWRITTEN from the tkinter osd_indicator.py to a compiled Xlib program
# (on_screen_display.c), per the follow-up spec: "written in C, not Python", "it should not
# flicker", "bottom middle", "stay a tiny bit longer / fade", "hover with mouse to drag ... add
# some sort of highlighter". These tests pin those contracts against the C SOURCE
# (on_screen_display.c) -- the artifact the ISO compiles.

def _osd_src() -> str:
    return (paths.AZZIO_COMMAND_LINE_INTERFACE_DIR / "on_screen_display.c").read_text(encoding="utf-8")


def test_osd_is_written_in_c_not_python():
    """The spec: this should be written in C, not Python. The OSD is on_screen_display.c (an Xlib
    program), and the old tkinter osd_indicator.py is gone."""
    d = paths.AZZIO_COMMAND_LINE_INTERFACE_DIR
    assert (d / "on_screen_display.c").exists(), "on_screen_display.c (the C OSD) must exist"
    assert not (d / "osd_indicator.py").exists(), "the old tkinter OSD must be removed"
    src = _osd_src()
    assert "#include <X11/Xlib.h>" in src            # a real Xlib program
    assert "int main(void)" in src


def test_osd_uses_the_logo_cyan_for_the_bar():
    """The spec: use the Azzio logo colour, cyan, for the bars. The OSD accent is #06B8FD."""
    src = _osd_src()
    assert "0x06B8FD" in src
    assert "COL_ACCENT" in src and "0x06B8FD" in src


def test_osd_is_bottom_middle_on_the_primary_monitor():
    """The follow-up spec: position it BOTTOM MIDDLE (Manjaro Cinnamon style), not centered. The
    OSD resolves the PRIMARY monitor (RandR) and places the chip horizontally centered, resting
    above the bottom edge by a margin."""
    src = _osd_src()
    assert "XRRGetMonitors" in src                    # primary-monitor geometry via RandR
    assert "place_bottom_middle" in src
    # horizontally centered, and offset up from the bottom by MARGIN_BOTTOM (not vertically centered)
    assert "(o->mon_w - WIN_W) / 2" in src
    assert "mon_h - WIN_H - MARGIN_BOTTOM" in src
    assert "MARGIN_BOTTOM" in src


def test_osd_is_single_instance_so_it_never_flickers():
    """The follow-up spec: it should NOT flicker on increase/decrease/mute. The fix is a SINGLE
    resident window: the process binds a per-user abstract socket; a later invocation fails to
    bind, connects, and FORWARDS its line to the running window (which repaints in place) instead
    of mapping a second window."""
    src = _osd_src()
    assert "azzio-osd" in src                        # the single-instance socket name base
    assert "AF_UNIX" in src and "bind(" in src        # tries to bind the control socket
    assert "EADDRINUSE" in src                        # already-running detection
    assert "connect(" in src                          # forward to the resident instance
    # double-buffered (draw into a pixmap, blit once) -> tear/flicker free
    assert "XCreatePixmap" in src and "XCopyArea" in src


def test_osd_draws_simple_volume_and_brightness_icons():
    """The spec: create simple icons for volume and brightness. They are DRAWN with X primitives
    (a speaker for volume, a sun for brightness) -- simple, no image files."""
    src = _osd_src()
    assert "draw_speaker" in src         # the volume icon
    assert "draw_sun" in src             # the brightness icon
    # it chooses the icon by kind
    assert 'strcmp(o->kind, "brightness")' in src
    # a muted state is representable (the speaker shows an x)
    assert "o->muted" in src


def test_osd_scale_is_full_range_holds_longer_and_fades():
    """The spec: a full-range 0..100 bar that stays a TINY BIT LONGER, then FADES away. The bar
    fills to percent/100; the window holds, then fades via _NET_WM_WINDOW_OPACITY, then closes."""
    src = _osd_src()
    assert "o->percent / 100.0" in src               # a 0..100 percent fill
    assert "HOLD_MS" in src and "FADE_MS" in src     # a hold-then-fade dismissal
    assert "_NET_WM_WINDOW_OPACITY" in src           # the fade (compositor opacity, like Tk -alpha)
    assert "override_redirect = True" in src          # borderless/unmanaged
    assert "_NET_WM_STATE_ABOVE" in src              # kept on top


def test_osd_supports_mouse_hover_drag_with_a_highlight():
    """The follow-up spec: allow the user to hover with the mouse and DRAG to increase/decrease,
    with a HIGHLIGHTER indicating the drag is live. The OSD selects pointer events, drags map x
    to a percent and run `azzio <kind> set <pct>`, and a highlight ring is drawn on hover."""
    src = _osd_src()
    assert "ButtonPressMask" in src and "PointerMotionMask" in src and "EnterWindowMask" in src
    assert "x_to_percent" in src                     # pointer x -> 0..100
    assert "spawn_set" in src and '"set"' in src     # a drag sets a precise level via the CLI
    assert "o->hover" in src or "o->dragging" in src  # the highlight is gated on hover/drag
    assert "COL_HILITE" in src                       # the drag highlight colour


def test_osd_never_steals_focus():
    """The OSD must never steal focus (override-redirect, notification window type, no focus
    calls) so typing is never interrupted -- the same constraint the tkinter indicator had."""
    src = _osd_src()
    assert "override_redirect = True" in src
    assert "_NET_WM_WINDOW_TYPE_NOTIFICATION" in src
    # no explicit focus grab
    assert "XSetInputFocus" not in src


# --- the launcher (media.py) wires to the shipped OSD, non-blocking ----------

def test_media_launches_the_shipped_osd_detached():
    """media.py launches the OSD DETACHED (start_new_session) with one JSON line, so the FN key
    returns instantly; and it targets the same path the ISO ships the OSD to."""
    cli = _command_line_interface()
    assert cli.OSD_INDICATOR_BIN == desktop.AZZIO_OSD_SYSTEM_PATH == "/usr/local/lib/azzio/azzio-osd"
    src = bundle_source()
    # the OSD is launched via Popen, detached, fed a JSON payload
    assert "start_new_session=True" in src
    assert '"kind": kind' in src and '"percent"' in src and '"muted"' in src


def test_media_osd_is_a_noop_without_display(monkeypatch):
    """No DISPLAY (or a missing OSD binary) must make _show_osd a clean no-op -- the FN key still
    changes the volume/brightness, it just shows no bar. It must NOT spawn a process."""
    cli = _command_line_interface()
    monkeypatch.setattr(cli.os.environ, "get", lambda *a, **k: None)   # DISPLAY unset
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn the OSD without a display"))
    cli._show_osd("volume", 50.0)   # returns cleanly


# --- the OpenBox FN keybinds + the OSD shipping ------------------------------

def test_openbox_binds_the_fn_media_keysyms():
    """The FN keys are wired by BINDING the XF86 media keysyms (not a fixed FN+F2/F3, which
    differs per machine) to the `azzio` subcommands."""
    rc = desktop.openbox_rc_xml()
    assert "XF86AudioRaiseVolume" in rc and "/usr/local/bin/azzio volume up" in rc
    assert "XF86AudioLowerVolume" in rc and "/usr/local/bin/azzio volume down" in rc
    assert "XF86AudioMute" in rc and "/usr/local/bin/azzio volume mute" in rc
    assert "XF86MonBrightnessUp" in rc and "/usr/local/bin/azzio brightness up" in rc
    assert "XF86MonBrightnessDown" in rc and "/usr/local/bin/azzio brightness down" in rc


def test_openbox_documents_the_per_machine_fn_mapping():
    """The rc.xml comment must explain WHY we bind keysyms: the physical FN+F2/F3 mapping differs
    between a PC (volume) and a laptop (brightness), so binding the keysyms makes each 'just
    work' -- and brightness self-gates on a PC."""
    rc = desktop.openbox_rc_xml()
    assert "keysym" in rc.lower()
    assert "brightness" in rc.lower() and "volume" in rc.lower()


def test_osd_ships_executable_and_pinned():
    """The OSD ships (root-owned, executable) next to the terminal user interface binary. It is a
    COMPILED C binary now, so it is built + installed by terminal_user_interface_build.build_osd()
    (invoked from compiler.py), NOT emitted as a text PLAN entry -- and it is pinned 0755 in the
    ISO file_permissions (archiso would otherwise ship it 0644 and the launcher's X_OK guard would
    silently skip the bar)."""
    from packages.azzio import terminal_user_interface_build as tb
    # the build wiring targets the same lib-dir path media.py + openbox refer to
    assert tb.OSD_BIN_SYSTEM_PATH == desktop.AZZIO_OSD_SYSTEM_PATH == "/usr/local/lib/azzio/azzio-osd"
    assert hasattr(tb, "build_osd")
    # it is NO LONGER a text-emitted PLAN entry (that was the tkinter script era)
    assert all(e["dest"] != "/usr/local/lib/azzio/azzio-osd" for e in desktop.emit_plan())
    assert not hasattr(desktop, "azzio_osd"), "the text OSD emitter must be gone"
    # pinned executable in the ISO
    perms = profiledef.FILE_PERMISSIONS
    assert perms["/usr/local/lib/azzio/azzio-osd"] == "0:0:755"
    # the X client libs the OSD links are declared as build-host deps
    assert {"libx11", "libxrandr", "libxft"} <= set(tb.TERMINAL_USER_INTERFACE_BUILD_DEPS)


def test_osd_is_not_bundled_into_the_fast_cli():
    """The OSD is a SEPARATE compiled program (on_screen_display.c); it must NOT be bundled into
    the `azzio` script (the bundle is Python modules only), and the fast command line interface
    path must not import tkinter (nothing does anymore -- the OSD is C)."""
    assert "on_screen_display.c" not in MODULE_ORDER
    assert "osd_indicator.py" not in MODULE_ORDER
    assert "import tkinter" not in bundle_source()


def test_osd_never_lingers_and_hard_backstop_bounds_lifetime():
    """The OSD must self-terminate even when stdin closes with NO usable message (the launcher
    died before writing, or wrote only blanks/garbage). This is a SOURCE-CONTRACT test only -- it
    reads on_screen_display.c and never compiles or RUNS it, so the suite stays pure (no X server touched, no
    window ever mapped -- `tests.sh` never fires the UI).

    Contract: an absolute MAX_LIFE_MS backstop bounds EVERY path (checked at the top of the tick
    loop against a monotonic birth time), and reaching it returns 1 => the window closes."""
    src = _osd_src()
    assert "MAX_LIFE_MS" in src
    assert "now - o->born_ms > MAX_LIFE_MS" in src   # the hard lifetime backstop (in tick())
    assert "return 1" in src                          # ... and hitting it tears the window down
    # the poll loop always wakes on a short timeout so the fade/hold/backstop advance even with
    # no X or socket traffic (it can never block forever waiting for input)
    assert "poll(pfds, 2, timeout)" in src
