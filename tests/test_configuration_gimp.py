"""packages.gimp -- skip GIMP's first-run / introduction dialogs (config-only).

Why these tests matter: compiler._emit_apps blindly writes emit_plan() to
~/.config/GIMP/3.2/gimprc (+ /etc/skel). The suppression depends on the exact gimprc keys
GIMP reads and the correct versioned config dir (3.2, not 3.0); a drift silently brings a
dialog back. Just as important: GIMP must LOAD NORMALLY -- the preload/warm-start machinery
was reverted, so these tests also lock in that the modification is CONFIG-ONLY (one gimprc,
no .desktop Exec override, no wrapper/helper, no preload).
"""

from __future__ import annotations

from packages import gimp


def test_emit_plan_is_single_home_gimprc():
    plan = gimp.emit_plan()
    assert len(plan) == 1
    entry = plan[0]
    assert entry["builder"] is gimp.gimprc
    assert entry["dest"] == gimp.GIMPRC_PATH
    assert entry["dest"] == "/home/main/.config/GIMP/3.2/gimprc"
    assert entry["mode"] == 0o644
    assert entry["owner"] == "home"


def test_gimprc_disables_welcome_dialog():
    # (show-welcome-dialog no) turns off the "show each time" Welcome dialog.
    out = gimp.gimprc()
    assert "(show-welcome-dialog no)" in out


def test_gimprc_seeds_config_version_to_suppress_version_welcome_window():
    # The version-gated "Welcome to GIMP <ver>" window is shown whenever gimprc's
    # config-version differs from GIMP's own version (a fresh profile, absent key, always
    # differs). Seeding config-version to the shipped GIMP version makes a first run look
    # like the version was already seen, so that window never opens. This is the fix for the
    # "GIMP welcome popup was never removed" regression. Pin the version to the shipped gimp.
    out = gimp.gimprc()
    assert gimp.GIMP_CONFIG_VERSION == "3.2.4"
    assert f'(config-version "{gimp.GIMP_CONFIG_VERSION}")' in out


def test_gimprc_disables_tips_dialog():
    # (show-tips no) stops the Tip of the Day dialog.
    out = gimp.gimprc()
    assert "(show-tips no)" in out


def test_gimprc_uses_the_real_versioned_config_dir():
    # GIMP 3.2 reads ~/.config/GIMP/3.2/gimprc; a 3.0 dir is ignored. Pin the version.
    assert gimp.GIMP_VERSION_DIR == "3.2"
    assert "/.config/GIMP/3.2/gimprc" in gimp.GIMPRC_PATH


def test_modification_is_config_only_no_preload_no_desktop_override():
    # GIMP loads normally: the modification ships ONLY the gimprc. It must NOT reintroduce
    # any preload/warm-start machinery, a .desktop Exec override, or a window helper (all of
    # which were deliberately removed). The whole module is one HOME config file, so this is
    # checked on the ARTIFACTS the plan emits (not on prose): exactly one gimprc, and none of
    # the machinery paths a preload build would ship.
    plan = gimp.emit_plan()
    dests = [e["dest"] for e in plan]
    # exactly one artifact, the gimprc
    assert dests == [gimp.GIMPRC_PATH]
    # no .desktop launcher override, no autostart entry, no wrapper/helper binary anywhere.
    for d in dests:
        assert not d.endswith(".desktop"), d
        assert "autostart" not in d, d
        assert "/bin/" not in d, d
    # The gimprc itself must not shell out to anything (it is pure GIMP config, not a script).
    body = gimp.gimprc()
    assert "--no-splash" not in body
    assert "azzio-gimp" not in body
