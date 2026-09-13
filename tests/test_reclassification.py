"""Lock in the libraries/ classification.

After the consolidation there are just TWO buckets, with a precise, load-bearing meaning a
future edit could silently violate:

  libraries/            the general build machinery -- the compiler's own flat modules
                        (compiler, paths, emit, makepkg, pacman, package_discovery, ...) PLUS
                        our own package recipes (pkgbuild.py) and the entry point.
  libraries/packages/   EVERY package the build ships, each its OWN directory module (a dir
                        with an __init__.py). This holds BOTH the things WE author
                        (application_menu/, azzio/, passwords/, and the critically-modified
                        calamares/) AND the upstream software we merely tailor (openbox/,
                        librewolf/, kitty/, gedit/, file_manager/, fastfetch/, the per-app tweaks).
                        There is NO separate modifications/ tree anymore.

This is also a genuine IMPORT hazard: the compiler's package-cache module is `downloader`
(was `packages.py`) precisely so the payload `packages/` directory can be a real import
package (`packages.application_menu`, `packages.openbox`) without the module shadowing the
directory (or vice versa). If someone renames `downloader.py` back to `packages.py`, the
imports below break. These tests catch that -- and the "one flat home for everything a package
ships" invariant -- at unit-test time.

`packages/` carries no top-level `__init__.py` -- it is an implicit namespace package, resolved
off PYTHONPATH (= libraries/). Every SUB-directory of packages/ is a REGULAR package with its
own `__init__.py`, so it resolves under the namespace `packages` and package_discovery can find
it. The four merges (home_directory->file_manager, scale->openbox, ckbcomp->calamares,
timedate->librewolf) live as files INSIDE their target package, so they no longer appear as
top-level entries.
"""

from __future__ import annotations

import paths


def test_downloader_and_packages_dir_coexist():
    # The package-cache module is `downloader` (a flat compiler module) so the
    # `packages/` payload dir can be a real import package. Both must resolve.
    import downloader  # the renamed package-cache module (was packages.py)

    assert downloader.__file__.endswith("libraries/downloader.py")
    # `packages` here is the payload directory-package, NOT downloader. It carries
    # no __init__.py (implicit namespace package), so `__file__` is None and its
    # search path points at libraries/packages/.
    import packages as payload_pkg
    assert payload_pkg.__file__ is None
    assert any(p.endswith("libraries/packages") for p in payload_pkg.__path__)


def test_pkgbuild_is_a_flat_library_module():
    # pkgbuild.py holds OUR package recipes and is general build machinery, so it moved UP out
    # of packages/ to sit flat in libraries/ next to makepkg/compiler/paths. It imports bare as
    # `import pkgbuild` (NOT `packages.pkgbuild`).
    import pkgbuild

    assert pkgbuild.__file__.endswith("libraries/pkgbuild.py")
    assert not pkgbuild.__file__.endswith("libraries/packages/pkgbuild.py")
    assert callable(pkgbuild.recipe_dirs)


def test_our_packages_import_from_packages_bucket():
    # The application-menu build wiring is one of OUR packages, so it imports from packages.*.
    from packages.application_menu import application_menu

    assert callable(application_menu.emit_plan)


def test_every_packages_subdirectory_is_a_regular_package():
    # Every SUB-directory of libraries/packages/ must carry an __init__.py so it is a real,
    # importable package that package_discovery loads (application_menu, azzio, calamares,
    # passwords, openbox, librewolf, kitty, gedit, file_manager, ...). A directory without one would
    # be silently skipped by discovery.
    packages_dir = paths.PACKAGESDIR
    for child in packages_dir.iterdir():
        if not child.is_dir() or child.name.startswith("__"):
            continue
        assert (child / "__init__.py").is_file(), (
            f"packages/{child.name}/ must have an __init__.py (every packages subdirectory "
            "is a regular package)"
        )
    # The critically-modified calamares install config is one of OUR packages.
    assert (packages_dir / "calamares" / "__init__.py").is_file()


def test_modifications_tree_is_gone():
    # There is no separate libraries/modifications/ tree anymore -- everything a package ships
    # (ours AND the upstream software we tailor) lives under libraries/packages/. paths must not
    # expose a MODIFICATIONSDIR either.
    assert not (paths.LIBDIR / "modifications").exists(), (
        "libraries/modifications/ must be gone -- all packages live under libraries/packages/"
    )
    assert not hasattr(paths, "MODIFICATIONSDIR")


def test_the_upstream_tailoring_packages_live_under_packages():
    # The packages that tailor upstream software are directory modules under packages/, each
    # with an __init__.py, and import as `from packages import <name>`.
    packages_dir = paths.PACKAGESDIR
    for name in ("fastfetch", "librewolf", "openbox", "kitty", "gedit", "file_manager",
                 "vlc", "gimp", "xviewer", "libreoffice"):
        assert (packages_dir / name / "__init__.py").is_file(), (
            f"packages/{name}/__init__.py must exist so the package loads"
        )


def test_the_merges_live_inside_their_target_package():
    # The merged modules are FILES inside their target package now, not their own
    # top-level directories. Each is importable as a submodule of that package.
    from packages.file_manager import home_directory   # home_directory -> file_manager
    from packages.file_manager import templates        # templates -> file_manager
    from packages.openbox import scale           # scale -> openbox
    from packages.librewolf import timedate      # timedate -> librewolf
    assert home_directory.__file__.endswith("libraries/packages/file_manager/home_directory.py")
    assert templates.__file__.endswith("libraries/packages/file_manager/templates.py")
    assert scale.__file__.endswith("libraries/packages/openbox/scale.py")
    assert timedate.__file__.endswith("libraries/packages/librewolf/timedate.py")
    # ckbcomp is a companion SCRIPT (copied verbatim, never imported), so it is just a file
    # under the calamares package, not a submodule.
    assert (paths.PACKAGESDIR / "calamares" / "ckbcomp.py").is_file()
    # ...and their old top-level homes must be gone.
    for gone in ("home_directory", "templates", "scale", "ckbcomp", "timedate"):
        assert not (paths.PACKAGESDIR / gone).exists(), (
            f"packages/{gone}/ must be gone -- it was merged into its target package"
        )


def test_our_own_modules_are_not_stray_packages():
    # The general build machinery (pkgbuild, profile, pacman, installer, system, ...) stays FLAT
    # in libraries/ -- it must NOT have leaked into packages/ as a directory or a stray file.
    packages_dir = paths.PACKAGESDIR
    names = {p.name for p in packages_dir.iterdir()}
    for ours in ("pkgbuild", "profile", "pacman", "installer", "system", "compiler",
                 "emit", "makepkg", "downloader", "package_discovery"):
        assert ours not in names, f"{ours} is general machinery -- it must stay flat in libraries/"
    # The only non-directory entry allowed directly under packages/ is the pacman manifest.
    stray_files = {p.name for p in packages_dir.iterdir()
                   if p.is_file() and p.name != "packages.x86_64"}
    assert not stray_files, f"packages/ should hold only directory modules + packages.x86_64, found: {stray_files}"


def test_locale_lives_with_the_calamares_package():
    # locale.py is Calamares install-time config, so it lives with the calamares package.
    from packages.calamares import locale

    assert locale.__file__.endswith("libraries/packages/calamares/locale.py")
    assert callable(locale.resolver_country_table_py)


def test_openbox_is_the_desktop_package():
    # openbox/ configures the upstream OpenBox WM (the whole live desktop). It is a directory
    # module under packages/ and imports as `from packages import openbox`.
    from packages import openbox

    assert openbox.__file__.endswith("libraries/packages/openbox/__init__.py")
    assert callable(openbox.emit_plan)


def test_compiler_is_the_entry_point():
    # build.py was cannibalized into compiler.py, so `python3 -m compiler` is the
    # entry point: compiler must own BOTH the step sequencer and the driver's main().
    import compiler

    assert compiler.__file__.endswith("libraries/compiler.py")
    assert callable(compiler.main)          # the folded-in driver
    assert callable(compiler.run)           # the step sequencer
    assert callable(compiler.cache_is_complete)
