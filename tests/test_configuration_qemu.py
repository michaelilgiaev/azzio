"""packages.qemu -- drop qemu-block-gluster so a qemu-full upgrade can proceed.

Why these tests matter: compiler concatenates drop_block_gluster_sh() onto
customize_airootfs.sh after pacstrap. The removal has to be `pacman -Rdd`: a plain
`pacman -R` fails while qemu-full 11.0 still depends on the package, and
`pacman --noconfirm` answers the conflict prompt with No. A drift back to `pacman -R`
makes the ISO build, and every later upgrade, abort.
"""

from __future__ import annotations

from pathlib import Path

from packages import qemu


def test_drop_uses_rdd_not_a_plain_remove():
    # -Rdd skips the dependency check. -R would refuse because qemu-full 11.0
    # still requires qemu-block-gluster.
    out = qemu.drop_block_gluster_sh()
    assert f"pacman -Q {qemu.QEMU_BLOCK_GLUSTER} >/dev/null 2>&1" in out
    assert f"pacman -Rdd --noconfirm {qemu.QEMU_BLOCK_GLUSTER}" in out
    assert f"pacman -R {qemu.QEMU_BLOCK_GLUSTER}" not in out


def test_package_name_is_the_removed_split_package():
    assert qemu.QEMU_BLOCK_GLUSTER == "qemu-block-gluster"


def test_iso_build_appends_the_drop_after_pacstrap():
    # The shell must run inside customize_airootfs.sh (post-pacstrap). Owning the
    # text in packages/qemu is not enough if the build never concatenates it.
    compiler_py = (Path(__file__).resolve().parent.parent
                   / "libraries/compiler.py").read_text(encoding="utf-8")
    assert "qemu.drop_block_gluster_sh()" in compiler_py


def test_qemu_full_stays_in_the_manifest():
    # qvm needs qemu-full. The fix removes only the gluster split package.
    manifest = (Path(__file__).resolve().parent.parent
                / "libraries/packages/packages.x86_64").read_text(encoding="utf-8")
    names = set()
    for line in manifest.splitlines():
        tok = line.split("#", 1)[0].strip()
        if tok:
            names.add(tok)
    assert "qemu-full" in names
    assert "qemu-block-gluster" not in names
