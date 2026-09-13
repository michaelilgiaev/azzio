"""packages.xviewer -- replace xviewer's off-putting "eye" icon (PROMPT task 5).

Why these tests matter: xviewer is the Photos default, so its icon is visible. The override
must point the .desktop Icon= at our custom icon (a private name so a package upgrade cannot
revert it) and ship that icon; the .desktop dest is package-owned, so it must be wired into
pacman.ISO_APP_OVERRIDES (NoExtract + post-pacstrap) exactly like gedit's.
"""

from __future__ import annotations

import paths
from packages import xviewer


def test_desktop_points_icon_at_custom_private_name():
    d = xviewer.xviewer_desktop()
    assert f"Icon={xviewer.XVIEWER_ICON_NAME}\n" in d
    assert xviewer.XVIEWER_ICON_NAME == "azzio-xviewer"   # private name (upgrade-proof)
    assert "Icon=xviewer\n" not in d                        # not the stock eye icon


def test_desktop_keeps_stock_exec_and_full_mime_list():
    # xviewer stays the Photos handler: Exec + the full image MimeType list are preserved.
    d = xviewer.xviewer_desktop()
    assert "Exec=xviewer %U" in d
    assert "MimeType=" in d
    for mime in ("image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"):
        assert mime in d, mime


def test_emit_plan_ships_icon_and_desktop_override():
    plan = xviewer.emit_plan()
    by_dest = {e["dest"]: e for e in plan}
    # scalable SVG master (our asset, our name), root-owned overlay file
    svg = by_dest[xviewer.ICON_SCALABLE_PATH]
    assert svg.get("asset") == xviewer.ICON_ASSET
    assert svg["owner"] == "root"
    # the .desktop override, root-owned
    assert by_dest[xviewer.XVIEWER_DESKTOP_PATH]["owner"] == "root"
    # a PNG render per size
    for size in xviewer.ICON_PNG_SIZES:
        dest = f"/usr/share/icons/hicolor/{size}x{size}/apps/{xviewer.XVIEWER_ICON_NAME}.png"
        assert by_dest[dest].get("render") == {"asset": xviewer.ICON_ASSET, "size": size}


def test_desktop_override_is_wired_into_iso_app_overrides():
    # package-owned dest -> must be NoExtract'd + staged post-pacstrap (like gedit's .desktop).
    import pacman
    override_targets = {t for _b, t, _r in pacman.ISO_APP_OVERRIDES}
    assert xviewer.XVIEWER_DESKTOP_PATH in override_targets


def test_icon_asset_exists_and_is_svg():
    p = paths.ASSETSDIR / xviewer.ICON_ASSET
    assert p.is_file()
    assert p.suffix == ".svg"
    head = p.read_text()[:200]
    assert "<svg" in head
