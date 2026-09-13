"""Calamares branding config builders: product identity + the QML slideshow.

  - branding.desc  product strings, window sizing, the near-black + blue theme,
                   and the window icon (productIcon -> a real PNG in the component dir)
  - show.qml       a minimal single-slide slideshow (no marketing copy)

branding.desc names the window icon by PRODUCT_ICON_FILE (imported from
config_constants). Re-exported by the `calamares` facade as
`calamares.branding_desc` / `calamares.branding_show_qml`.
"""

from __future__ import annotations

from .config_constants import PRODUCT_ICON_FILE


# --- 7. branding/azzio/branding.desc --------------------------------------
def branding_desc() -> str:
    """Product identity + a single-slide QML slideshow placeholder + colors."""
    return """\
# Branding for the Azzio Linux installer.
---
componentName: azzio

# Interval used when the slideshow QML advances (ms). Single slide -> no cycling.
welcomeStyleCalamares: false
welcomeExpandingLogo: true

# Window sizing: percentage of the screen. "800px,520px" is an absolute fallback.
windowExpanding: normal
windowSize: 900px,560px
windowPlacement: center

# Product strings shown throughout the UI.
strings:
    productName:         Azzio Linux
    shortProductName:    Azzio
    version:             rolling
    shortVersion:        rolling
    versionedName:       Azzio Linux (rolling)
    shortVersionedName:  Azzio rolling
    bootloaderEntryName: Azzio
    productUrl:          https://github.com/michaelilgiaev/azzio
    supportUrl:          https://github.com/michaelilgiaev/azzio
    knownIssuesUrl:      https://github.com/michaelilgiaev/azzio/issues
    releaseNotesUrl:     https://github.com/michaelilgiaev/azzio
    donateUrl:           ""

# Optional images (product logo / window icon).
#   productIcon -> the WINDOW ICON. Set to the "Az'" app tile shipped INTO this branding
#     dir as productIcon.png (see PRODUCT_ICON_FILE / compiler.py). Calamares sets the
#     window icon from QIcon(imagePath(ProductIcon)); a real file in the component dir
#     resolves to an absolute path so the icon actually loads and OpenBox draws it on the
#     titlebar (fixes the "installer has no topbar icon" report). It matches the Desktop /
#     application-menu launcher icon (both are the same source asset).
#   productLogo / productWelcome -> still EMPTY (no such PNGs shipped): Calamares skips
#     empty image keys and uses its built-in default, avoiding a "does not exist" log.
images:
    productLogo:   ""
    productIcon:   \"""" + PRODUCT_ICON_FILE + """\"
    productWelcome: ""

# Slideshow: a single QML slide placeholder shown during the exec phase.
slideshow: "show.qml"
slideshowAPI: 2

# UI colors. Minimal near-black + slate + blue theme matching the installer
# inspiration (assets/raw calameres slide): a very dark background with a blue
# "Az'" accent, muted slate labels, no decorative noise. NOTE: the real
# branding.desc style keys are Capitalized -- lowercase variants are silently
# ignored. The sidebar (#070e1b) sits a hair lighter than the page body (#030712)
# so the step list reads as a panel; the selected step is white, the rest slate
# (#64748b), and the accent is blue (#3b82f6) to match the "Az'" wordmark.
style:
    SidebarBackground:    "#070e1b"
    SidebarText:          "#64748b"
    SidebarTextSelect:    "#ffffff"
    SidebarTextHighlight: "#3b82f6"
"""


def branding_show_qml() -> str:
    """A minimal, valid Calamares slideshow (slideshowAPI 2). One static, centered
    slide -- no external assets, NO motivational/marketing copy (the user asked for
    a "get out of my way" installer): just "Installing Azzio Linux" with the "Az'"
    wordmark blue, and a small dim status line. Matches the near-black + blue theme
    (bg #030712, brand #3b82f6, muted #64748b) of branding.desc.

    Single slide, so the Timer does not cycle (goToNextSlide would loop back to the
    same slide); it is kept only because Presentation expects the structure."""
    return """\
/* Azzio Linux -- minimal single-slide installer slideshow (no marketing copy). */
import QtQuick 2.0
import calamares.slideshow 1.0

Presentation {
    id: presentation

    Timer {
        interval: 20000
        running: presentation.activatedInCalamares
        repeat: true
        onTriggered: presentation.goToNextSlide()
    }

    Slide {
        anchors.fill: parent

        Rectangle {
            anchors.fill: parent
            color: "#030712"
        }

        Column {
            anchors.centerIn: parent
            spacing: 10

            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 0
                Text {
                    text: "Installing "
                    color: "#ffffff"
                    font.pixelSize: 30
                    font.weight: Font.DemiBold
                }
                Text {
                    text: "Az'"
                    color: "#3b82f6"
                    font.pixelSize: 30
                    font.weight: Font.Bold
                }
                Text {
                    text: "arch"
                    color: "#ffffff"
                    font.pixelSize: 30
                    font.weight: Font.Bold
                }
                Text {
                    text: " Linux"
                    color: "#ffffff"
                    font.pixelSize: 30
                    font.weight: Font.DemiBold
                }
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Please wait while the system is being installed."
                color: "#64748b"
                font.pixelSize: 14
            }
        }
    }

    function onActivate() {}
    function onLeave() {}
}
"""
