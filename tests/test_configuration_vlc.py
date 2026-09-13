"""packages.vlc -- suppress VLC's first-run "metadata network access" dialog.

Why these tests matter: compiler._emit_apps blindly writes emit_plan() to
~/.config/vlc/vlcrc (+ /etc/skel). The two prefs are what actually kill the dialog, and
their SECTION headers must match VLC's own (`[qt]`, `[core]`) or VLC ignores the keys. A
drift in a key name/value or a section header silently brings the dialog back.
"""

from __future__ import annotations

from packages import vlc


def test_emit_plan_is_single_home_vlcrc():
    plan = vlc.emit_plan()
    assert len(plan) == 1
    entry = plan[0]
    assert entry["builder"] is vlc.vlcrc
    assert entry["dest"] == vlc.VLCRC_PATH
    assert entry["dest"] == "/home/main/.config/vlc/vlcrc"
    assert entry["mode"] == 0o644
    assert entry["owner"] == "home"


def test_vlcrc_disables_the_privacy_dialog():
    # qt-privacy-ask=0 is THE switch that stops the first-run dialog from appearing.
    out = vlc.vlcrc()
    assert "qt-privacy-ask=0" in out


def test_vlcrc_disables_metadata_network_access():
    # The actual "No" the dialog would set; pinned so network metadata fetching is off.
    out = vlc.vlcrc()
    assert "metadata-network-access=0" in out


def test_vlcrc_uses_vlc_section_headers():
    # The keys are ignored unless they sit under VLC's real section names. qt-privacy-ask
    # lives in [qt]; metadata-network-access lives in [core].
    out = vlc.vlcrc()
    assert "[qt]" in out
    assert "[core]" in out
    # qt-privacy-ask must appear AFTER [qt] and BEFORE [core]; metadata-network-access
    # must appear after [core]. (A key under the wrong section is silently dropped.)
    qt_idx = out.index("[qt]")
    core_idx = out.index("[core]")
    assert qt_idx < out.index("qt-privacy-ask=0") < core_idx
    assert core_idx < out.index("metadata-network-access=0")


def test_vlcrc_is_nonempty_and_ini_shaped():
    out = vlc.vlcrc()
    assert isinstance(out, str) and out.strip()
    # INI comments start with '#'; the banner line must be a comment, not a stray key.
    assert out.lstrip().startswith("#")


def test_vlcrc_follows_system_theme_dark_by_default():
    # VLC follows the system theme via qt-palette-mode (2 = dark, 1 = light). Azzio
    # defaults dark; the light build flips it to 1. The line lives under [qt] (before [core]).
    dark = vlc.vlcrc()
    light = vlc.vlcrc(dark=False)
    assert "qt-palette-mode=2" in dark
    assert "qt-palette-mode=1" in light
    assert vlc.VLC_PALETTE_DARK == 2 and vlc.VLC_PALETTE_LIGHT == 1
    # under [qt], before [core]
    assert dark.index("[qt]") < dark.index("qt-palette-mode=2") < dark.index("[core]")
    # the privacy suppression is unaffected by the theme in either mode
    assert "qt-privacy-ask=0" in dark and "qt-privacy-ask=0" in light
