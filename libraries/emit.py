"""Emit contract: write configuration-as-Python content out as real files in the ISO tree.

The package configuration modules (``packages.*``) hold each artifact's content as a
Python string. These helpers place that content on disk with the right mode,
and copy the few verbatim data files. This is the seam between "configuration as data"
(the strings) and "build logic" (where they go).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import paths


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str, mode: int = 0o644) -> Path:
    """Write a generated configuration file, creating parent dirs. Normalizes to a single
    trailing newline (the archiso/pacman/systemd parsers all expect one)."""
    path = Path(path)
    _ensure_parent(path)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def write_exec(path: Path, text: str) -> Path:
    """Write a script and make it executable (0o755)."""
    return write_text(path, text, mode=0o755)


def write_bytes(path: Path, data: bytes, mode: int = 0o644) -> Path:
    """Write generated BINARY content verbatim (no newline normalization), creating parent
    dirs. For artifacts whose bytes are meaningful and must not be touched -- e.g. a compiled
    gettext .mo catalog (packages/file_manager/locale.mo_bytes)."""
    path = Path(path)
    _ensure_parent(path)
    path.write_bytes(data)
    os.chmod(path, mode)
    return path


def copy_data(rel: str, dest: Path, mode: int | None = None) -> Path:
    """Copy a verbatim file from libraries/packages/<rel> to dest.

    Used for the few files a package ships byte-for-byte rather than generating from a Python
    string -- e.g. the vendored ckbcomp script (packages/calamares/ckbcomp.py -> /usr/bin/ckbcomp).
    (The function name predates the tree consolidation under libraries/packages/ and is kept for
    call-site stability.)"""
    src = paths.PACKAGESDIR / rel
    dest = Path(dest)
    _ensure_parent(dest)
    shutil.copy2(src, dest)
    if mode is not None:
        os.chmod(dest, mode)
    return dest


def copy_asset(rel: str, dest: Path, mode: int | None = None) -> Path:
    """Copy a verbatim file from assets/<rel> to dest (binaries, scripts, images)."""
    src = paths.ASSETSDIR / rel
    dest = Path(dest)
    _ensure_parent(dest)
    shutil.copy2(src, dest)
    if mode is not None:
        os.chmod(dest, mode)
    return dest


# SVG->PNG rasterizers, in preference order. rsvg-convert is the cleanest and is present
# on the build host AND the live/installed medium (librsvg is pulled in by the GTK stack);
# ImageMagick `convert` and `inkscape` are fallbacks so the build does not hard-depend on a
# single tool. Each entry builds the argv for "rasterize SRC to DEST at SIZExSIZE".
def _rsvg_argv(src: str, dest: str, size: int) -> list[str]:
    return ["rsvg-convert", "-w", str(size), "-h", str(size), src, "-o", dest]


def _magick_argv(src: str, dest: str, size: int) -> list[str]:
    # -background none keeps transparency; the geometry forces the square size.
    return ["convert", "-background", "none", "-density", "384",
            src, "-resize", f"{size}x{size}", dest]


def _inkscape_argv(src: str, dest: str, size: int) -> list[str]:
    return ["inkscape", src, "--export-type=png", f"--export-filename={dest}",
            "-w", str(size), "-h", str(size)]


_SVG_RASTERIZERS = (
    ("rsvg-convert", _rsvg_argv),
    ("convert", _magick_argv),
    ("inkscape", _inkscape_argv),
)


def render_svg_png(rel_asset: str, dest: Path, size: int, mode: int = 0o644) -> Path:
    """Rasterize the SVG asset assets/<rel_asset> to a square <size>px PNG at dest.

    Used for icon PNGs whose single source of truth is a vector asset (e.g. kitty's
    in-window titlebar icon kitty.app.png, derived from assets/icons/kitty.svg). Tries
    rsvg-convert, then ImageMagick `convert`, then inkscape -- whichever is installed --
    so the build does not hard-depend on one tool. Raises if none is available or the
    conversion fails (a silently-missing icon would regress the feature)."""
    src = paths.ASSETSDIR / rel_asset
    dest = Path(dest)
    _ensure_parent(dest)
    last_err: Exception | None = None
    for tool, argv_fn in _SVG_RASTERIZERS:
        if shutil.which(tool) is None:
            continue
        argv = argv_fn(str(src), str(dest), size)
        proc = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode == 0 and dest.is_file():
            os.chmod(dest, mode)
            return dest
        last_err = RuntimeError(
            f"{tool} failed to rasterize {src} -> {dest}: {proc.stderr.decode(errors='replace')}"
        )
    if last_err is not None:
        raise last_err
    raise RuntimeError(
        f"no SVG rasterizer found (need one of: {', '.join(t for t, _ in _SVG_RASTERIZERS)}) "
        f"to render {src} -> {dest}"
    )


def copy_tree(src: Path, dest: Path) -> None:
    """Recursively copy src/* into dest (like `cp -r src/. dest/`)."""
    src = Path(src)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(item, target)


def link(target: str, linkname: Path) -> None:
    """Create/replace a symlink linkname -> target (for systemd .wants links)."""
    linkname = Path(linkname)
    _ensure_parent(linkname)
    if linkname.is_symlink() or linkname.exists():
        linkname.unlink()
    linkname.symlink_to(target)


def mkdir(path: Path, mode: int | None = None) -> Path:
    """Create a directory (and parents). With `mode` given, force that permission on the
    directory itself (via chmod, so it wins over the process umask and over a pre-existing
    dir) -- used for dirs whose permission is load-bearing, e.g. ~/.ssh at 0700 (sshd
    refuses a group/world-accessible .ssh)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if mode is not None:
        os.chmod(path, mode)
    return path
