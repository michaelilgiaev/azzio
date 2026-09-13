"""LibreOffice configuration modification -- skip the first-run / introduction popups.

LibreOffice (the office suite Azzio ships as `libreoffice-fresh`) greets a fresh profile
with a series of popups that Azzio wants gone from the very first launch, exactly the way
packages/vlc suppresses VLC's first-run privacy dialog:

  * The "Welcome to LibreOffice!" first-launch dialog ("You are running LibreOffice for the
    first time" -- the tabbed Welcome / User Interface / Appearance personalize window that
    LibreOffice 25.8+/26.x shows once per version). THIS is the one the e2e test caught still
    appearing: it is NOT governed by FirstRun / the first-start wizard flags below -- it is
    version-gated on `ooSetupLastVersion` (shown when that stored version differs from the
    running one, which a fresh profile, where it is absent, always trips).
  * "Tip of the Day"        -- a modal shown on EVERY start until unticked.
  * The first-start wizard  -- LibreOffice's initial registration/online-setup run.
  * "What's New" / release-notes infobar on a new version.
  * The automatic online-update check popup.

HOW (a preseeded registrymodifications.xcu, the supported analog of the partial vlcrc).
LibreOffice persists user preference changes to a single XML file in the user profile,
~/.config/libreoffice/4/user/registrymodifications.xcu, as a list of <item oor:path=...>
entries -- each overriding ONE configuration node's default. We ship that file with only
the handful of nodes that turn the popups off; every other LibreOffice setting keeps its
default (LibreOffice merges these over the shipped registry, same as VLC merging vlcrc over
its defaults). LibreOffice REWRITES this file itself whenever the user changes a setting, so
our shipped copy is just the initial seed.

The node paths/keys below are the real oor config identifiers:
  * org.openoffice.Setup/Product/ooSetupLastVersion = <ver> -- pre-declare the shipped
    LibreOffice version as already-seen, so the version-gated "Welcome to LibreOffice!"
    first-launch dialog does NOT open. This is THE fix for the welcome popup the e2e test
    caught (verified against a real Azzio guest: LibreOffice 26.2.5 writes
    ooSetupLastVersion="26.2" after that dialog, so seeding it makes a first run look like
    the version was already seen). Uses the major.minor form LibreOffice itself writes.
  * org.openoffice.Setup/Office/ooSetupInstCompleted = true  -- mark install setup done
  * org.openoffice.Setup/Office/FirstStartWizardCompleted = true -- skip the first-start run
  * org.openoffice.Office.Common/Misc/FirstRun = false       -- do not run the first-run job
  * org.openoffice.Office.Common/Misc/ShowTipOfTheDay = false -- no Tip of the Day modal
  * org.openoffice.Office.Common/Misc/ShowWhatsNew = false    -- no What's New screen
  * org.openoffice.Office.Jobs .../UpdateCheck/AutoCheckEnabled = false -- no update popup

WHERE IT GOES. ~/.config/libreoffice/4/user/registrymodifications.xcu for the live user
(compiler.py chowns it 1000:998 and mirrors it into /etc/skel so a Calamares-created user
inherits the same quiet first run). The "4" is LibreOffice's user-profile-version dir (the
current, long-stable profile layout the libreoffice-fresh build uses).

Pure standard library (returns a string). compiler.py iterates emit_plan() exactly like
packages/vlc -- one HOME file, owner="home".
"""

from __future__ import annotations

# The live user's home (matches openbox.HOME / the airootfs /home/main tree).
HOME = "/home/main"

# WHERE LIBREOFFICE READS ITS USER REGISTRY: ~/.config/libreoffice/4/user/
# registrymodifications.xcu (XDG_CONFIG_HOME defaults to ~/.config, which the OpenBox
# session sets). Single source of truth for the path; emit_plan() ships the file here and
# compiler.py mirrors it into /etc/skel.
REGMOD_PATH = f"{HOME}/.config/libreoffice/4/user/registrymodifications.xcu"

# The LibreOffice version (major.minor) the ISO ships -- the `libreoffice-fresh` package
# pinned in the offline cache is 26.2.x. This is what LibreOffice stores in
# ooSetupLastVersion, and it is THE gate for the "Welcome to LibreOffice!" first-launch
# dialog: LibreOffice shows that window whenever the stored ooSetupLastVersion differs from
# the running version (a fresh profile, where the key is absent, always differs), then
# writes ooSetupLastVersion = <running major.minor> for next time. Verified against a real
# Azzio guest: LibreOffice 26.2.5 wrote ooSetupLastVersion="26.2" after that dialog. So
# SEEDING it to the shipped major.minor makes a first run look like the version was already
# seen and the welcome window never opens. Uses the major.minor form LibreOffice itself
# writes (NOT the full 26.2.5). Bump alongside the shipped libreoffice-fresh package.
LIBREOFFICE_LAST_VERSION = "26.2"


def registrymodifications_xcu() -> str:
    """Return the seed registrymodifications.xcu that disables LibreOffice's first-run and
    introduction popups (see the module docstring).

    Only the popup-suppressing nodes are written; LibreOffice merges them over its shipped
    registry defaults, so every other setting stays stock. Each <item> overrides one config
    node; the oor:path / prop name / value must match LibreOffice's schema or the item is
    ignored. Values are typed (xsi:type) as LibreOffice writes them: booleans as xsd:boolean."""
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!-- Azzio LibreOffice overrides: skip the first-run and introduction popups (the
     "Welcome to LibreOffice!" first-launch dialog, Tip of the Day, first-start wizard,
     What's New, online update check). Generated by packages/libreoffice (edit the
     Python, not this file). LibreOffice reads these nodes over its shipped registry
     defaults; every other setting is left exactly as LibreOffice ships it, and LibreOffice
     rewrites this file when a preference changes. -->
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <!-- Pre-declare the shipped LibreOffice version as already-seen so the version-gated
      "Welcome to LibreOffice!" first-launch dialog never opens (the popup the e2e caught). -->
 <item oor:path="/org.openoffice.Setup/Product">
  <prop oor:name="ooSetupLastVersion" oor:op="fuse">
   <value>{LIBREOFFICE_LAST_VERSION}</value>
  </prop>
 </item>
 <item oor:path="/org.openoffice.Setup/Office">
  <prop oor:name="ooSetupInstCompleted" oor:op="fuse">
   <value>true</value>
  </prop>
 </item>
 <item oor:path="/org.openoffice.Setup/Office">
  <prop oor:name="FirstStartWizardCompleted" oor:op="fuse">
   <value>true</value>
  </prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Misc">
  <prop oor:name="FirstRun" oor:op="fuse">
   <value>false</value>
  </prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Misc">
  <prop oor:name="ShowTipOfTheDay" oor:op="fuse">
   <value>false</value>
  </prop>
 </item>
 <item oor:path="/org.openoffice.Office.Common/Misc">
  <prop oor:name="ShowWhatsNew" oor:op="fuse">
   <value>false</value>
  </prop>
 </item>
 <item oor:path="/org.openoffice.Office.Jobs/Jobs/org.openoffice.Office.Jobs:Job['UpdateCheck']/Arguments">
  <prop oor:name="AutoCheckEnabled" oor:op="fuse">
   <value>false</value>
  </prop>
 </item>
</oor:items>
"""


# --- Emit plan --------------------------------------------------------------
# Declarative map (builder -> dest -> mode -> owner), the same shape compiler.py iterates
# for packages/vlc and packages/openbox. One HOME file (owner="home"): compiler.py chowns it
# 1000:998 with the rest of /home/main AND mirrors it into /etc/skel so a Calamares-created
# user inherits the same quiet first run. Mode 0644 (plain config data).
_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan for the LibreOffice override: a single HOME file at
    ~/.config/libreoffice/4/user/registrymodifications.xcu.

    Shape matches vlc.emit_plan() (builder/dest/mode/owner) so compiler.py can emit it with
    the same loop (and skel-mirror the home file). Returns a FRESH dict so a caller cannot
    mutate module state."""
    return [
        {
            "builder": registrymodifications_xcu,
            "dest": REGMOD_PATH,
            "mode": _CONF,
            "owner": "home",
        },
    ]
