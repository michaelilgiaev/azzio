"""paths -- the filesystem layout. The Docker bind mounts and the ownership
handback all depend on these paths composing to exactly the right places, so a
dropped path segment here is a silent, build-breaking regression.
"""

from __future__ import annotations

import importlib

import paths


def test_repodir_is_repo_root():
    # paths.py is libraries/paths.py -> repo root is two parents up and
    # must contain the known top-level entries.
    assert (paths.REPODIR / "libraries").is_dir()
    assert (paths.REPODIR / "compile.sh").is_file()


def test_static_dirs_are_under_repodir():
    for d in (paths.LIBDIR, paths.PACKAGESDIR, paths.ASSETSDIR,
              paths.PKGDIR, paths.CACHEDIR, paths.BUILDDIR, paths.LOGDIR,
              paths.CKBCOMP_SRC, paths.MAKEPKG_SRC_CACHE):
        assert str(d).startswith(str(paths.REPODIR))


def test_ckbcomp_src_is_a_calamares_companion_file():
    # ckbcomp is a companion file of the calamares package (Calamares is the only thing that
    # shells out to it), so its vendored script lives at packages/calamares/ckbcomp.py (still
    # emitted to /usr/bin/ckbcomp without the .py suffix).
    assert paths.CKBCOMP_SRC == paths.PACKAGESDIR / "calamares" / "ckbcomp.py"
    assert paths.CKBCOMP_SRC.is_file()


def test_packagesdir_is_libraries_packages():
    assert paths.PACKAGESDIR == paths.REPODIR / "libraries" / "packages"


def test_application_menu_dir_is_under_packages():
    assert paths.APPLICATION_MENU_DIR == paths.PACKAGESDIR / "application_menu"


def test_package_stores_compose_correctly():
    assert paths.PKG_REPO == paths.CACHEDIR / "pkgs" / "repo"
    assert paths.PKG_DB == paths.CACHEDIR / "pkgs" / "db"
    assert paths.PKG_SYNC_DB == paths.PKG_DB / "sync"
    assert paths.LOCALREPO_INDEX == paths.PKG_REPO / "pacstrap-azzio-repo.db"
    assert paths.LOCALREPO_INDEX_TAR == paths.PKG_REPO / "pacstrap-azzio-repo.db.tar.gz"


def test_makepkg_src_cache_survives_the_scratch_wipe():
    # The source-tarball cache (SRCDEST) must be a SIBLING of the makepkg scratch,
    # NOT a subdir of it: build_own_packages does `rm -rf cache/makepkg` at the start
    # of every online build, so anything under it is destroyed and the upstream
    # tarball (calamares' Codeberg release) would be re-downloaded every run. Keeping
    # it outside means makepkg reuses the cached tarball and skips the download.
    scratch = paths.CACHEDIR / "makepkg"
    assert paths.MAKEPKG_SRC_CACHE == paths.CACHEDIR / "makepkg-src"
    assert not str(paths.MAKEPKG_SRC_CACHE).startswith(str(scratch) + "/")
    assert paths.MAKEPKG_SRC_CACHE != scratch


def test_log_paths():
    assert paths.FULL_LOG == paths.LOGDIR / "compile-full.log"
    assert paths.STEPS_LOG == paths.LOGDIR / "compile-steps.log"
    assert paths.PACKAGES_FILE == paths.PACKAGESDIR / "packages.x86_64"


def _reload_with_dockerenv(monkeypatch, present: bool):
    # WORKDIR is chosen at module-import time from in_docker(), which reads
    # Path("/.dockerenv").exists(). importlib.reload re-runs that top-level code and
    # rebinds in_docker to the real function, so patching in_docker is useless across
    # a reload -- we must fake the filesystem probe itself. Patch pathlib.Path.exists
    # to report /.dockerenv present/absent, then reload and read WORKDIR.
    import pathlib

    real_exists = pathlib.Path.exists

    def fake_exists(self, *a, **kw):
        if str(self) == "/.dockerenv":
            return present
        return real_exists(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "exists", fake_exists)
    return importlib.reload(paths)


def test_workdir_native_is_in_cache(monkeypatch):
    # On a native (non-Docker) run, WORKDIR lives under cache/ so it is discoverable.
    reloaded = _reload_with_dockerenv(monkeypatch, present=False)
    try:
        assert reloaded.in_docker() is False
        assert reloaded.WORKDIR == reloaded.CACHEDIR / "build"
    finally:
        monkeypatch.undo()
        importlib.reload(paths)  # restore clean module-level state for other tests


def test_workdir_docker_is_container_internal(monkeypatch):
    # In Docker, WORKDIR must live OUTSIDE the bind mounts so a hard `docker kill`
    # can never leave root-owned scratch on the host.
    from pathlib import Path

    reloaded = _reload_with_dockerenv(monkeypatch, present=True)
    try:
        assert reloaded.in_docker() is True
        assert reloaded.WORKDIR == Path("/tmp/azzio-build")
        assert not str(reloaded.WORKDIR).startswith(str(reloaded.CACHEDIR))
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


def test_in_docker_reads_dockerenv(monkeypatch):
    import paths as p

    seen = {}

    class FakePath:
        def __init__(self, s):
            seen["arg"] = s

        def exists(self):
            return True

    monkeypatch.setattr(p, "Path", FakePath)
    assert p.in_docker() is True
    assert seen["arg"] == "/.dockerenv"
