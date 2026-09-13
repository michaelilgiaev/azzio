"""Azzio Image Viewer -- the vendored, version-controlled Xviewer source in one package.

Azzio's "Photos"/image-viewer default is Xviewer (the GTK image viewer). This package OWNS it:
the full upstream source tree is vendored under packages/image_viewer/source/ (committed straight
into the Azzio repo), the same "vendor-the-source, build-it-ourselves, no AUR" pattern as
packages/file_manager and packages/calamares. Keeping the source in-tree means the build is
reproducible offline and every change to it is auditable with `git diff`.

STATUS. This package currently ships ONLY the vendored source tree (source/); the build wiring
(a pkgbuild.pkgbuild_image_viewer recipe) and any Azzio config/emit_plan() are still to come. It
therefore exposes NO emit_plan() yet, so package_discovery.with_emit_plan() does not emit it and
the compiler does not drive it -- but it IS a real, importable package (this file), which is the
invariant tests/test_reclassification.py enforces for every packages/ subdirectory. The custom
Azzio "Photos" icon override that repoints xviewer.desktop lives separately in packages/xviewer
(the .desktop + icon tweak); this package is the software itself.
"""

from __future__ import annotations
