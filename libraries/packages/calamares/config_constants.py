"""Shared constants for the Calamares configuration builders.

These four values are used across several config-builder submodules
(config_settings / config_storage / config_system / config_branding), so they
live in ONE place here to stay a single source of truth. The submodules import
from this module ONLY -- never from the `calamares` facade -- so there is no
circular import. The facade (`calamares.py`) re-exports these names, so callers
keep using `calamares.BRANDING`, `calamares.ARCHISO_SFS`, etc. unchanged.
"""

from __future__ import annotations

# The branding component directory name (under branding/) and product identity.
BRANDING = "azzio"
PRODUCT = "Azzio Linux"

# The Calamares WINDOW ICON (the "Az'" app tile). Shipped as a REAL PNG inside the
# branding component dir (branding/azzio/) and named by its branding-relative filename
# in branding.desc's `productIcon`. This is what makes the icon show on OpenBox's titlebar
# (the `N` in rc.xml's titleLayout): Calamares' CalamaresApplication sets the window icon
# with QIcon( Branding::imagePath(ProductIcon) ), i.e. it constructs a QIcon from the
# STORED string DIRECTLY -- so productIcon MUST resolve to a real FILE PATH, not a bare
# freedesktop icon name. Branding.cpp turns a branding-relative filename that EXISTS in the
# component dir into an absolute path (componentDir.absoluteFilePath), so QIcon(path) loads
# it; a bare theme name would only pass load-time validation (via QIcon::fromTheme) yet come
# back out of imagePath() as the bare name, and QIcon("azzio-installer") then reads it as a
# missing file -> no titlebar icon. Hence a shipped file. compiler.py rasterizes the
# standardized vector assets/icons/azzio.svg (see packages/openbox.INSTALLER_ICON_ASSET)
# to a PNG at branding/azzio/PRODUCT_ICON_FILE, so the window icon matches the .desktop
# launcher icon (both derive from the one SVG master).
PRODUCT_ICON_FILE = "productIcon.png"

# The live archiso SquashFS image. On a booted archiso medium the boot device is
# mounted at /run/archiso/bootmnt and the root image lives at
# <install_dir>/<arch>/airootfs.sfs under it. Azzio's install_dir is "arch"
# (see libraries/profile.py INSTALL_DIR) and arch is x86_64, so the canonical,
# widely-used unpackfs source is the path below with sourcefs "squashfs".
# (Caveat: booting with the `copytoram` option unmounts bootmnt and moves the
# image to /run/archiso/copytoram/; Azzio does not enable copytoram by default.)
ARCHISO_SFS = "/run/archiso/bootmnt/arch/x86_64/airootfs.sfs"
