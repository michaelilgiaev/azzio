"""QEMU modification -- drop the removed gluster block driver.

QEMU (shipped as the `qemu-full` package, pkgbase `qemu`) is what `qvm` shells out
to: `qemu-system-x86_64` runs the VM and `qemu-img` creates the qcow2 disk. A shipped
image installed qemu-full 11.0, and that version depends on the split package
`qemu-block-gluster`. QEMU 11.1 removed the gluster block driver. `qemu-common` 11.1
conflicts with the leftover, and `pacman --noconfirm` answers
"Remove qemu-block-gluster? [y/N]" with No, so `pacman -Syu` and `pacman -S qemu-full`
abort. `pacman -R qemu-block-gluster` aborts too, because qemu-full 11.0 still requires
the package. `pacman -Rdd` skips that dependency check. qemu-full 11.1 does not depend
on it. Nothing else requires it. `qvm` does not use the driver.

WHERE IT RUNS. compiler.py appends drop_block_gluster_sh() to customize_airootfs.sh,
which mkarchiso runs inside the pacstrapped rootfs AFTER packages are installed and
then deletes. A current pacstrap that never installed qemu-block-gluster hits the if
and does nothing. An image that already shipped the package runs the same shell once,
as root, before the upgrade.

Pure standard library (returns a string). No emit_plan(): this is not a file planted
over the qemu-full package, it is a package removal the build must run after pacstrap.
"""

from __future__ import annotations

# The split package qemu-full 11.0 depends on and qemu-common 11.1 conflicts with.
# Not in packages.x86_64: it was pulled in by qemu-full, and 11.1 stopped shipping it.
QEMU_BLOCK_GLUSTER = "qemu-block-gluster"


def drop_block_gluster_sh() -> str:
    """Return the post-pacstrap shell that removes qemu-block-gluster when present.

    compiler.py concatenates this onto customize_airootfs.sh. The same text is what
    an already-installed system runs once, as root, before `pacman -Syu`. -Rdd is
    required: `pacman -R` refuses to break the dependency qemu-full 11.0 still has.
    """
    pkg = QEMU_BLOCK_GLUSTER
    return f"""\
# packages/qemu: {pkg} conflicts with qemu-common 11.1.
# A noninteractive pacman will not remove it, and pacman -R fails while
# qemu-full 11.0 still depends on it, so -Rdd. Absent: no-op.
if pacman -Q {pkg} >/dev/null 2>&1; then
    pacman -Rdd --noconfirm {pkg}
fi
"""
