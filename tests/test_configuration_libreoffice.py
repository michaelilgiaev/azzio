"""packages.libreoffice -- skip LibreOffice's first-run / introduction popups.

Why these tests matter: compiler._emit_apps blindly writes emit_plan() to
~/.config/libreoffice/4/user/registrymodifications.xcu (+ /etc/skel). The suppression
depends on the exact oor config node paths / property names LibreOffice reads; a drift in
a path or key silently brings a popup back. The file must also be well-formed XML or
LibreOffice ignores it wholesale.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from packages import libreoffice as lo


def test_emit_plan_is_single_home_registrymodifications():
    plan = lo.emit_plan()
    assert len(plan) == 1
    entry = plan[0]
    assert entry["builder"] is lo.registrymodifications_xcu
    assert entry["dest"] == lo.REGMOD_PATH
    assert entry["dest"] == "/home/main/.config/libreoffice/4/user/registrymodifications.xcu"
    assert entry["mode"] == 0o644
    assert entry["owner"] == "home"


def test_registrymodifications_is_wellformed_xml():
    # LibreOffice ignores the whole file if it is not well-formed; parse it.
    ET.fromstring(lo.registrymodifications_xcu())


def test_registrymodifications_suppresses_tip_of_the_day():
    # ShowTipOfTheDay=false is THE switch that stops the Tip of the Day modal.
    out = lo.registrymodifications_xcu()
    assert 'oor:name="ShowTipOfTheDay"' in out
    assert "<value>false</value>" in out


def test_registrymodifications_skips_first_run_wizard():
    # The first-start wizard + first-run job must both be marked done/off so the initial
    # registration/setup run never appears.
    out = lo.registrymodifications_xcu()
    assert 'oor:name="FirstStartWizardCompleted"' in out
    assert 'oor:name="ooSetupInstCompleted"' in out
    assert 'oor:name="FirstRun"' in out


def test_registrymodifications_disables_whatsnew_and_update_check():
    # The What's New screen and the automatic online-update popup are both off.
    out = lo.registrymodifications_xcu()
    assert 'oor:name="ShowWhatsNew"' in out
    assert 'oor:name="AutoCheckEnabled"' in out


def test_registrymodifications_suppresses_welcome_first_launch_dialog():
    # THE fix for the popup the e2e caught: the "Welcome to LibreOffice!" first-launch dialog
    # is version-gated on ooSetupLastVersion (shown when the stored version differs from the
    # running one -- a fresh profile always differs). Seeding it to the shipped major.minor
    # (verified: LibreOffice 26.2.5 writes "26.2") makes a first run look already-seen so the
    # window never opens. It must sit under the real /org.openoffice.Setup/Product path.
    out = lo.registrymodifications_xcu()
    assert lo.LIBREOFFICE_LAST_VERSION == "26.2"
    assert 'oor:path="/org.openoffice.Setup/Product"' in out
    assert 'oor:name="ooSetupLastVersion"' in out
    assert f"<value>{lo.LIBREOFFICE_LAST_VERSION}</value>" in out


def test_registrymodifications_uses_real_oor_paths():
    # The nodes must sit under LibreOffice's real oor config paths or the items are ignored.
    out = lo.registrymodifications_xcu()
    assert 'oor:path="/org.openoffice.Setup/Office"' in out
    assert 'oor:path="/org.openoffice.Setup/Product"' in out
    assert 'oor:path="/org.openoffice.Office.Common/Misc"' in out
    # It is an oor:items document with the openoffice registry namespace.
    assert 'xmlns:oor="http://openoffice.org/2001/registry"' in out
    assert out.lstrip().startswith("<?xml")
