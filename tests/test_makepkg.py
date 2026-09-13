"""makepkg -- the own-package build stage.

The heavy lifting (makepkg, sudo, gpg) is io-heavy and not unit-tested here. The
one pure, load-bearing branch is produced_names(): it decides which packages are
EXCLUDED from the Arch `pacman -Sw` download. Get it wrong and the build tries to
download a package that is on no mirror (Arch dropped calamares from extra/, so a
missing exclusion makes `pacman -Sw calamares` fail with "target not found" and
aborts the whole download). Both tiers now build calamares + librewolf.
_repo_has_all is pure given a dir.

The OFFLINE-RERUN branch is also covered here (with the real makepkg monkeypatched
away): the DEFAULT tier must SKIP makepkg when the cached packages are present,
while a --full-compile rerun must RE-COMPILE from the cached source tree WITHOUT
wiping it and WITHOUT any network. _scratch_has_sources is pure given a dir.

_harden_dlagents is the other pure, load-bearing piece: a real build aborted on a
transient `curl: (92) HTTP/2 stream reset (0x8 CANCEL)` fetching the calamares
tarball despite the stock DLAGENT's --retry 3, because plain --retry recovers from
neither a mid-stream reset nor a slow-crawl stall. It rewrites the system
makepkg.conf's network curl agents to add --retry-all-errors + --speed-time/-limit
(and makepkg is pointed at the result via --config). These tests pin exactly which
agents are hardened, that nothing else in the config is disturbed, that there are no
duplicate flags, and that it is idempotent. _write_hardened_conf is its IO wrapper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import makepkg
import pkgbuild as pkgbuild_cfg


# A faithful slice of Arch's stock /etc/makepkg.conf DLAGENTS block: the local
# file:: copy, the three network curl agents (each already carrying the stock
# --retry 3 --retry-delay 3), and the non-curl rsync/scp agents. Used by the
# DLAGENTS-hardening tests so they don't depend on the host's real config.
_STOCK_DLAGENTS = """\
# some preamble
CFLAGS="-march=native -O2"
DLAGENTS=('file::/usr/bin/curl -qgC - -o %o %u'
          'ftp::/usr/bin/curl -qgfC - --ftp-pasv --retry 3 --retry-delay 3 -o %o %u'
          'http::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u'
          'https::/usr/bin/curl -qgb "" -fLC - --retry 3 --retry-delay 3 -o %o %u'
          'rsync::/usr/bin/rsync --no-motd -z %u %o'
          'scp::/usr/bin/scp -C %u %o')
PKGEXT='.pkg.tar.zst'
"""


def _agent_line(conf: str, proto: str) -> str:
    """The single DLAGENTS line defining `proto::` in a rendered makepkg.conf."""
    return next(l for l in conf.splitlines() if f"{proto}::" in l)


def test_produced_names_default_tier_builds_calamares_and_librewolf():
    # Arch dropped calamares from extra/, so the default tier must build it too
    # (it can no longer be pacman-downloaded). file_manager is ALSO built here: it is Azzio's own
    # file manager, built from the vendored source and dropped into the offline repo (it replaces
    # stock thunar via provides/conflicts/replaces). All three are produced by makepkg.
    assert makepkg.produced_names(full_compile=False) == ("calamares", "librewolf", "file_manager")


def test_produced_names_is_tier_independent():
    # --full-compile only changes the RECIPE, not the set of names built.
    assert makepkg.produced_names(full_compile=True) == makepkg.produced_names(full_compile=False)


def test_produced_constant_matches_produced_names():
    assert makepkg.PRODUCED == makepkg.produced_names(full_compile=False)


# --- compile parallelism cap (build_jobs) -----------------------------------
# Regression guard: a build left every compiler (calamares' cmake, LibreWolf's
# bsys6 make) auto-detecting the core count and pinned all 24 CPUs at 100% for the
# whole compile, making the machine unusable. build_jobs is the single ceiling
# _makepkg_one exports so no build system grabs every core. PROMPT: the cap is now
# a HARDCODED 75% of the cores (floor(cores * 0.75)) -- earlier size-scaled reserves
# still "lagged my PC", so the policy is dead-simple and predictable -- with a
# --use-each-cpu escape hatch that lifts it to EVERY core. These tests pin the 75%
# shape, the "never all cores by default" safety invariant, and both the flag and
# the AZZIO_USE_EACH_CPU env override.


@pytest.fixture(autouse=True)
def _reset_use_each_cpu():
    """Keep the process-wide --use-each-cpu opt-in from leaking between tests: reset
    the module flag to "unset" and clear the env var before the test, then RESTORE the
    real prior state after it -- so build_jobs() sees the default 75% cap here, and no
    later test (even in another file that reads build_jobs() live) inherits a stray value.

    Restore is done by hand, NOT monkeypatch: set_use_each_cpu() assigns os.environ
    RAW (os.environ[...] = ...), which monkeypatch does not track -- so a monkeypatch
    teardown would leave that raw write in place and leak AZZIO_USE_EACH_CPU into the
    next file. Snapshotting the real prior value and writing it back covers that."""
    import os
    prior_flag = makepkg._USE_EACH_CPU
    prior_env = os.environ.get(makepkg._USE_EACH_CPU_ENV)
    makepkg._USE_EACH_CPU = None
    os.environ.pop(makepkg._USE_EACH_CPU_ENV, None)
    try:
        yield
    finally:
        makepkg._USE_EACH_CPU = prior_flag
        if prior_env is None:
            os.environ.pop(makepkg._USE_EACH_CPU_ENV, None)
        else:
            os.environ[makepkg._USE_EACH_CPU_ENV] = prior_env


# --- THE "don't kill the PC" invariant --------------------------------------
def test_build_jobs_never_uses_every_core_by_default():
    # THE safety guarantee (default, no --use-each-cpu): on ANY machine with more
    # than one core, the build must leave at least one core free so the desktop stays
    # responsive -- it must never request a job per core (the bug that pinned all 24
    # CPUs at 100%). Swept across laptops, desktops, and big workstations. (A 1-core
    # box is the sole exception: one job is unavoidable there.)
    for cores in range(2, 257):
        jobs = makepkg.build_jobs(cores=cores)
        assert 1 <= jobs <= cores - 1, (
            f"{cores} cores -> {jobs} jobs leaves no headroom (would pin the machine)"
        )


def test_build_jobs_is_hardcoded_75_percent():
    # The policy IS floor(cores * 0.75) at every size -- a flat 75%, not a
    # size-scaled reserve. Pin it exactly across the sweep so the number can't drift.
    import math
    for cores in range(1, 257):
        assert makepkg.build_jobs(cores=cores) == max(1, math.floor(cores * 0.75))


def test_build_jobs_single_core_uses_the_only_core():
    # Degenerate case: with one core there is nothing to spare, so one job.
    assert makepkg.build_jobs(cores=1) == 1
    # Zero/negative core counts should never crash or return < 1 (defensive).
    assert makepkg.build_jobs(cores=0) == 1


def test_build_jobs_concrete_values_at_75_percent():
    # Concrete expectations for representative rigs (job count = floor(cores*0.75)):
    assert makepkg.build_jobs(cores=2) == 1      # floor(1.5)  -> 1 free
    assert makepkg.build_jobs(cores=4) == 3      # floor(3.0)  -> 1 free
    assert makepkg.build_jobs(cores=5) == 3      # floor(3.75) -> 2 free
    assert makepkg.build_jobs(cores=8) == 6      # floor(6.0)  -> 2 free
    assert makepkg.build_jobs(cores=12) == 9     # floor(9.0)  -> 3 free
    assert makepkg.build_jobs(cores=16) == 12    # floor(12.0) -> 4 free
    assert makepkg.build_jobs(cores=24) == 18    # floor(18.0) -> 6 free (reported host)
    assert makepkg.build_jobs(cores=64) == 48    # floor(48.0) -> 16 free
    assert makepkg.build_jobs(cores=128) == 96   # floor(96.0) -> 32 free


def test_build_jobs_is_monotonic_in_cores():
    # More cores must never mean fewer jobs -- a sanity check that the 75% floor has
    # no dips as the machine grows.
    prev = 0
    for cores in range(1, 257):
        jobs = makepkg.build_jobs(cores=cores)
        assert jobs >= prev, f"{cores} cores gave {jobs} jobs, fewer than {prev}"
        prev = jobs


def test_build_jobs_defaults_to_live_cpu_count(monkeypatch):
    # With no argument it reads the real (affinity-aware) core count so production
    # code needs no wiring; the injectable arg is only for tests.
    monkeypatch.setattr(makepkg, "_cpu_count", lambda: 24)
    assert makepkg.build_jobs() == makepkg.build_jobs(cores=24) == 18


# --- the --use-each-cpu escape hatch ----------------------------------------
def test_use_each_cpu_flag_lifts_the_cap_to_all_cores():
    # PROMPT: --use-each-cpu uses EVERY logical CPU (one job per core). set_use_each_cpu
    # records the opt-in process-wide; build_jobs then returns the full core count.
    makepkg.set_use_each_cpu(True)
    for cores in (1, 2, 4, 8, 24, 64, 128):
        assert makepkg.build_jobs(cores=cores) == cores


def test_use_each_cpu_setter_also_exports_the_env(monkeypatch):
    # set_use_each_cpu mirrors the choice into AZZIO_USE_EACH_CPU so child processes
    # (and profile.profiledef_sh's mkarchiso thread cap) inherit the same decision.
    makepkg.set_use_each_cpu(True)
    assert makepkg.use_each_cpu() is True
    import os
    assert os.environ[makepkg._USE_EACH_CPU_ENV] == "1"
    makepkg.set_use_each_cpu(False)
    assert makepkg.use_each_cpu() is False
    assert os.environ[makepkg._USE_EACH_CPU_ENV] == "0"
    # Back to the capped default once turned off.
    assert makepkg.build_jobs(cores=24) == 18


def test_use_each_cpu_env_fallback_when_flag_unset(monkeypatch):
    # With the process-wide flag left unset (None), the env var is the fallback so an
    # externally-set AZZIO_USE_EACH_CPU (or one inherited by a subprocess) still opts in.
    monkeypatch.setattr(makepkg, "_USE_EACH_CPU", None, raising=False)
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(makepkg._USE_EACH_CPU_ENV, truthy)
        assert makepkg.use_each_cpu() is True
        assert makepkg.build_jobs(cores=8) == 8
    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv(makepkg._USE_EACH_CPU_ENV, falsy)
        assert makepkg.use_each_cpu() is False
        assert makepkg.build_jobs(cores=8) == 6


def test_use_each_cpu_flag_overrides_env(monkeypatch):
    # The explicit process-wide flag wins over the environment: set_use_each_cpu(False)
    # keeps the 75% cap even if AZZIO_USE_EACH_CPU=1 is present in the environment.
    monkeypatch.setenv(makepkg._USE_EACH_CPU_ENV, "1")
    makepkg.set_use_each_cpu(False)
    assert makepkg.use_each_cpu() is False
    assert makepkg.build_jobs(cores=8) == 6


def test_repo_has_all_true_when_every_name_present(tmp_path):
    (tmp_path / "librewolf-1.0-1-x86_64.pkg.tar.zst").write_text("")
    assert makepkg._repo_has_all(tmp_path, ("librewolf",)) is True


def test_repo_has_all_false_when_a_name_missing(tmp_path):
    (tmp_path / "librewolf-1.0-1-x86_64.pkg.tar.zst").write_text("")
    # calamares file absent -> not all present.
    assert makepkg._repo_has_all(tmp_path, ("calamares", "librewolf")) is False


def test_repo_has_all_matches_by_name_prefix(tmp_path):
    # A different package that merely starts similarly must not satisfy the glob.
    (tmp_path / "librewolf-common-1.0-1-x86_64.pkg.tar.zst").write_text("")
    # glob is "librewolf-*", which DOES match librewolf-common; the point of this
    # test is to document that behavior so a future tightening is a conscious change.
    assert makepkg._repo_has_all(tmp_path, ("librewolf",)) is True


def test_repo_has_all_false_for_wrong_extension(tmp_path):
    # The glob is "<name>-*.pkg.tar.zst". A package compressed as .xz (the older
    # default) is NOT a zst and must not satisfy the presence check, otherwise an
    # offline build would skip makepkg while pacstrap later can't find the .zst.
    (tmp_path / "librewolf-1.0-1-x86_64.pkg.tar.xz").write_text("")
    assert makepkg._repo_has_all(tmp_path, ("librewolf",)) is False


# --- recipe fingerprinting: reuse a cached own package ONLY if its recipe is unchanged
# The networkq regression: an offline default rerun reused a calamares package built
# before the networkq patch existed (the presence check is content-blind), so the ISO
# shipped a calamares that listed `networkq` in settings.conf but had no such module.
# These pin the fingerprint that now guards reuse.
def test_recipe_fingerprint_is_order_independent():
    # The digest folds files in sorted order, so emitting the same files in a
    # different dict order yields the same fingerprint.
    a = {"PKGBUILD": "body", "z.patch": "z", "a.patch": "a"}
    b = {"a.patch": "a", "PKGBUILD": "body", "z.patch": "z"}
    assert makepkg._recipe_fingerprint(a) == makepkg._recipe_fingerprint(b)


def test_recipe_fingerprint_changes_on_content_change():
    base = {"PKGBUILD": "body", "p.patch": "orig"}
    edited = {"PKGBUILD": "body", "p.patch": "EDITED"}
    assert makepkg._recipe_fingerprint(base) != makepkg._recipe_fingerprint(edited)


def test_recipe_fingerprint_changes_when_patch_added():
    # Adding a companion file (exactly the networkq/networkcfg-static case) must flip
    # the fingerprint even though every pre-existing file is byte-identical.
    before = {"PKGBUILD": "body", "a.patch": "a"}
    after = {"PKGBUILD": "body", "a.patch": "a", "networkq.patch": "new"}
    assert makepkg._recipe_fingerprint(before) != makepkg._recipe_fingerprint(after)


# --- vendored source-tree fingerprinting -----------------------------------
# The file manager regression: a recipe whose source is a VENDORED DIRECTORY
# (recipe_source_trees, e.g. packages/file_manager/source) had that tree copied into
# the recipe dir OUTSIDE the {filename: content} dict, so _recipe_fingerprint never
# saw it. Editing the vendored C (e.g. thunar-window.c to drop the Help menu) did NOT
# flip the recipe fingerprint, so the offline cache reused the pre-edit binary and the
# ISO/box shipped an UNFIXED file manager. These pin the tree-content hash that now guards it.
def test_source_tree_fingerprint_changes_on_file_content(tmp_path):
    tree = tmp_path / "src"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "a.c").write_text("orig")
    before = makepkg._source_tree_fingerprint(tree)
    (tree / "sub" / "a.c").write_text("EDITED")
    assert makepkg._source_tree_fingerprint(tree) != before


def test_source_tree_fingerprint_changes_when_file_added(tmp_path):
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "a.c").write_text("a")
    before = makepkg._source_tree_fingerprint(tree)
    (tree / "b.c").write_text("b")
    assert makepkg._source_tree_fingerprint(tree) != before


def test_source_tree_fingerprint_stable_across_calls(tmp_path):
    # Pure/deterministic: same bytes on disk -> same digest (no mtime/order noise).
    tree = tmp_path / "src"
    (tree / "d").mkdir(parents=True)
    (tree / "d" / "x.c").write_text("x")
    (tree / "y.c").write_text("y")
    assert makepkg._source_tree_fingerprint(tree) == makepkg._source_tree_fingerprint(tree)


def test_source_tree_fingerprint_tracks_relative_path(tmp_path):
    # Same content under a different relative path must differ (a moved/renamed file
    # is a real recipe change), so paths are folded in, not just bytes.
    t1 = tmp_path / "one"
    (t1 / "a").mkdir(parents=True)
    (t1 / "a" / "f.c").write_text("body")
    t2 = tmp_path / "two"
    (t2 / "b").mkdir(parents=True)
    (t2 / "b" / "f.c").write_text("body")
    assert makepkg._source_tree_fingerprint(t1) != makepkg._source_tree_fingerprint(t2)


def test_source_tree_fingerprint_injective_with_nul_content(tmp_path):
    # Content can contain any byte (the real tree has binary PNG icons). A naive
    # NUL-delimited encoding would let these two DIFFERENT trees collide to one hash:
    #   A: one file "a" with bytes  \x00 b \x00 Y
    #   B: two files "a"=empty, "b"=b"Y"
    # both flatten to  a \0 \0 b \0 Y \0  under delimiter-joining. Length-prefixing
    # must keep them distinct so a stale package is never reused.
    a = tmp_path / "A"
    a.mkdir()
    (a / "a").write_bytes(b"\x00b\x00Y")
    b = tmp_path / "B"
    b.mkdir()
    (b / "a").write_bytes(b"")
    (b / "b").write_bytes(b"Y")
    assert makepkg._source_tree_fingerprint(a) != makepkg._source_tree_fingerprint(b)


def test_source_tree_fingerprint_tracks_exec_bit(tmp_path):
    # build() runs ./autogen.sh directly and the copy preserves mode, so flipping a
    # script's owner-exec bit changes build behaviour and MUST flip the fingerprint
    # even though the file bytes are identical.
    tree = tmp_path / "src"
    tree.mkdir()
    script = tree / "autogen.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o644)
    before = makepkg._source_tree_fingerprint(tree)
    script.chmod(0o755)
    assert makepkg._source_tree_fingerprint(tree) != before


def test_source_tree_fingerprint_raises_on_missing_tree(tmp_path):
    # A declared-but-absent source tree is a build-config error; returning the empty
    # digest would silently mask it and reuse a stale package forever. Fail loud.
    import pytest
    with pytest.raises(makepkg.FingerprintError):
        makepkg._source_tree_fingerprint(tmp_path / "does-not-exist")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(makepkg.FingerprintError):
        makepkg._source_tree_fingerprint(empty)


def test_current_fingerprints_fold_in_source_tree(tmp_path, monkeypatch):
    # The end-to-end guard: editing a recipe's vendored source tree MUST change that
    # recipe's entry in _current_recipe_fingerprints (so the stale-cache gate rebuilds).
    tree = tmp_path / "vendored"
    (tree / "thunar").mkdir(parents=True)
    win = tree / "thunar" / "thunar-window.c"
    win.write_text("/* pretend file-manager source */\n")
    # The recipe-dir key is "file_manager" (== the produced package name); the source-tree map
    # must be keyed by it so _current_recipe_fingerprints folds the tree hash into that entry.
    monkeypatch.setattr(
        makepkg.pkgbuild_cfg, "recipe_source_trees", lambda: {"file_manager": tree}
    )
    before = makepkg._current_recipe_fingerprints(full_compile=False)["file_manager"]
    win.write_text("/* Azzio: Help menu removed */\n")
    after = makepkg._current_recipe_fingerprints(full_compile=False)["file_manager"]
    assert before != after


def test_current_fingerprints_unaffected_for_treeless_recipe(tmp_path, monkeypatch):
    # A recipe with NO source tree (calamares) must be identical whether or not the
    # source-tree map is consulted -- the tree hash only applies where a tree exists.
    monkeypatch.setattr(
        makepkg.pkgbuild_cfg, "recipe_source_trees", lambda: {}
    )
    fps = makepkg._current_recipe_fingerprints(full_compile=False)
    # calamares is text-only; its fingerprint equals the pure recipe-text hash.
    text_only = {
        dirname: makepkg._recipe_fingerprint(files)
        for dirname, files in makepkg.pkgbuild_cfg.recipe_dirs(False)
    }
    assert fps["calamares"] == text_only["calamares"]


def test_fingerprint_sidecar_round_trips(tmp_path):
    makepkg._write_recipe_fingerprint(tmp_path, "calamares", "abc123")
    assert makepkg._read_recipe_fingerprint(tmp_path, "calamares") == "abc123"


def test_read_fingerprint_none_when_absent(tmp_path):
    assert makepkg._read_recipe_fingerprint(tmp_path, "calamares") is None


def test_read_fingerprint_none_when_malformed(tmp_path):
    # A truncated/garbage sidecar must read as "unknown" (None) so the caller rebuilds
    # rather than trusting a corrupt stamp.
    makepkg._fingerprint_path(tmp_path, "calamares").write_text("{not json")
    assert makepkg._read_recipe_fingerprint(tmp_path, "calamares") is None


def test_repo_is_current_false_when_package_missing(tmp_path):
    # No package files at all -> not current (nothing to reuse).
    assert makepkg._repo_is_current(tmp_path, full_compile=False, fp_dir=tmp_path) is False


def test_repo_is_current_false_when_fingerprint_absent(tmp_path):
    # Package present but no sidecar (a cache from an older Azzio, or the networkq
    # regression) -> not current -> rebuild.
    for name in makepkg.produced_names(full_compile=False):
        (tmp_path / f"{name}-1-1-x86_64.pkg.tar.zst").write_text("")
    assert makepkg._repo_is_current(tmp_path, full_compile=False, fp_dir=tmp_path) is False


def test_repo_is_current_true_when_fingerprints_match(tmp_path):
    fps = makepkg._current_recipe_fingerprints(full_compile=False)
    for name, fp in fps.items():
        (tmp_path / f"{name}-1-1-x86_64.pkg.tar.zst").write_text("")
        makepkg._write_recipe_fingerprint(tmp_path, name, fp)
    assert makepkg._repo_is_current(tmp_path, full_compile=False, fp_dir=tmp_path) is True


def test_repo_is_current_false_when_one_fingerprint_stale(tmp_path):
    # One package's recipe changed (its stamp no longer matches) -> whole set is stale.
    fps = makepkg._current_recipe_fingerprints(full_compile=False)
    for name, fp in fps.items():
        (tmp_path / f"{name}-1-1-x86_64.pkg.tar.zst").write_text("")
        makepkg._write_recipe_fingerprint(tmp_path, name, fp)
    makepkg._write_recipe_fingerprint(tmp_path, "calamares", "changed")
    assert makepkg._repo_is_current(tmp_path, full_compile=False, fp_dir=tmp_path) is False


def test_sudo_root_vs_nonroot(monkeypatch):
    # _sudo() prepends nothing when already root (already privileged), and a bare
    # "sudo" (no -n, unlike steps/build) when not -- so an interactive password
    # prompt is allowed for the makepkg host-dep installs.
    monkeypatch.setattr(makepkg.paths, "is_root", lambda: True)
    assert makepkg._sudo() == []
    monkeypatch.setattr(makepkg.paths, "is_root", lambda: False)
    assert makepkg._sudo() == ["sudo"]


# --- _sanitize_build_path: keep user-local toolchain shims out of the build ---
# A uv/pipx/pyenv shim on the invoking user's PATH (e.g. ~/.local/bin/python3.12)
# must not be discoverable during the build, or cmake's find_package(Python ...)
# links the wrong libpython into the package (the calamares libpython3.12.so.1.0
# breakage). These pin the pure filtering logic.
_HOME = "/home/main"


def test_sanitize_build_path_strips_local_bin():
    p = f"/home/main/.local/bin:/usr/bin:/bin"
    assert makepkg._sanitize_build_path(p, _HOME) == "/usr/bin:/bin"


def test_sanitize_build_path_strips_nested_local_paths():
    # Both the bin shim and a deeper ~/.local/share entry go; system paths stay,
    # order preserved.
    p = ("/home/main/.local/bin:/usr/local/bin:"
         "/home/main/.local/share/uv/python/cpython-3.12/bin:/usr/bin")
    assert makepkg._sanitize_build_path(p, _HOME) == "/usr/local/bin:/usr/bin"


def test_sanitize_build_path_keeps_lookalike_prefix():
    # A directory whose name merely STARTS with ".local" (but is not the ~/.local
    # tree) must be kept -- only ~/.local and its subdirs are stripped.
    p = "/home/main/.locallib/bin:/usr/bin"
    assert makepkg._sanitize_build_path(p, _HOME) == "/home/main/.locallib/bin:/usr/bin"


def test_sanitize_build_path_falls_back_when_emptied():
    # If every entry was user-local, don't hand makepkg an empty PATH (it would fail
    # to find even coreutils) -- fall back to a minimal system PATH.
    p = "/home/main/.local/bin:/home/main/.local/share/foo/bin"
    assert makepkg._sanitize_build_path(p, _HOME) == "/usr/bin:/bin"


def test_sanitize_build_path_noop_without_home():
    # No HOME -> nothing to anchor ~/.local on -> leave PATH untouched.
    p = "/home/main/.local/bin:/usr/bin"
    assert makepkg._sanitize_build_path(p, "") == p


# --- _scratch_has_sources: the "can an offline recompile succeed?" check -----
def _populate_scratch(scratch, *, with_build_content):
    """Create the expected recipe dirs under scratch, each with a PKGBUILD and a
    .build dir. with_build_content controls whether .build has any files (the real
    "sources were fetched" signal)."""
    for dirname, _files in pkgbuild_cfg.recipe_dirs(full_compile=True):
        d = scratch / dirname
        (d / ".build").mkdir(parents=True)
        (d / "PKGBUILD").write_text("x")
        if with_build_content:
            (d / ".build" / "tree").write_text("x")


def test_scratch_has_sources_true(tmp_path):
    _populate_scratch(tmp_path, with_build_content=True)
    assert makepkg._scratch_has_sources(tmp_path, full_compile=True) is True


def test_scratch_has_sources_false_empty_build(tmp_path):
    # PKGBUILD present but .build empty -> no fetched source -> False.
    _populate_scratch(tmp_path, with_build_content=False)
    assert makepkg._scratch_has_sources(tmp_path, full_compile=True) is False


def test_scratch_has_sources_false_missing_pkgbuild(tmp_path):
    assert makepkg._scratch_has_sources(tmp_path, full_compile=True) is False


# --- build_own_packages offline branch, per tier ----------------------------
def _stage_current_own_packages(pkg_repo, full_compile=False):
    """Populate pkg_repo with a package file AND a matching current-recipe fingerprint
    sidecar for every own package -- i.e. a cache that is genuinely up to date, which
    the offline default tier is allowed to reuse without rebuilding."""
    fps = makepkg._current_recipe_fingerprints(full_compile=full_compile)
    for name, fp in fps.items():
        (pkg_repo / f"{name}-1-1-x86_64.pkg.tar.zst").write_text("")
        makepkg._write_recipe_fingerprint(pkg_repo, name, fp)


def test_offline_default_skips_makepkg(monkeypatch, tmp_path):
    # DEFAULT tier + complete AND up-to-date cache -> skip makepkg entirely (the fast
    # rerun). All THREE built packages must be present with a matching recipe
    # fingerprint for the cache to count as current.
    monkeypatch.setattr(makepkg.paths, "PKG_REPO", tmp_path)
    monkeypatch.setattr(makepkg.paths, "PKG_FINGERPRINTS", tmp_path)
    monkeypatch.setattr(makepkg.paths, "is_root", lambda: False)
    _stage_current_own_packages(tmp_path)

    def must_not_build(*a, **k):
        raise AssertionError("default offline rerun must not run makepkg")
    monkeypatch.setattr(makepkg, "_makepkg_one", must_not_build)

    makepkg.build_own_packages(offline=True, full_compile=False, progress=lambda _p: None)


def test_offline_default_stale_recipe_raises(monkeypatch, tmp_path):
    # DEFAULT tier offline, package files present but built from an OLDER recipe (a
    # stale/absent fingerprint) -> must NOT silently reuse the stale binary. This is
    # the networkq regression: a calamares package built before the networkq patch was
    # reused, so the ISO shipped a calamares whose settings listed a module it did not
    # have. The offline path fails loudly (the online path is what actually rebuilds;
    # cache_is_complete demotes a stale cache to online before we get here offline).
    monkeypatch.setattr(makepkg.paths, "PKG_REPO", tmp_path)
    monkeypatch.setattr(makepkg.paths, "PKG_FINGERPRINTS", tmp_path)
    monkeypatch.setattr(makepkg.paths, "is_root", lambda: False)
    # Package files exist but with NO fingerprint sidecars -> looks like an old cache.
    (tmp_path / "calamares-1-1-x86_64.pkg.tar.zst").write_text("stale")
    (tmp_path / "librewolf-1-1-x86_64.pkg.tar.zst").write_text("stale")
    (tmp_path / "file_manager-1-1-x86_64.pkg.tar.zst").write_text("stale")
    monkeypatch.setattr(makepkg, "_makepkg_one",
                        lambda *a, **k: pytest.fail("must not reuse a stale-recipe package"))
    with pytest.raises(makepkg.MakepkgError):
        makepkg.build_own_packages(offline=True, full_compile=False, progress=lambda _p: None)


def test_offline_default_changed_recipe_raises(monkeypatch, tmp_path):
    # Same as above but the sidecar EXISTS with a non-matching hash (a recipe was
    # edited since the package was built, e.g. adding the networkq patch). Still stale.
    monkeypatch.setattr(makepkg.paths, "PKG_REPO", tmp_path)
    monkeypatch.setattr(makepkg.paths, "PKG_FINGERPRINTS", tmp_path)
    monkeypatch.setattr(makepkg.paths, "is_root", lambda: False)
    _stage_current_own_packages(tmp_path)
    # Break just calamares' fingerprint to simulate its recipe changing.
    makepkg._write_recipe_fingerprint(tmp_path, "calamares", "stale_recipe_hash")
    monkeypatch.setattr(makepkg, "_makepkg_one",
                        lambda *a, **k: pytest.fail("must rebuild after a recipe change"))
    with pytest.raises(makepkg.MakepkgError):
        makepkg.build_own_packages(offline=True, full_compile=False, progress=lambda _p: None)


def test_offline_full_missing_source_raises(monkeypatch, tmp_path):
    # FULL tier offline but the cached source tree is gone -> fail loudly, never
    # silently go online.
    monkeypatch.setattr(makepkg.paths, "CACHEDIR", tmp_path)
    monkeypatch.setattr(makepkg.paths, "PKG_REPO", tmp_path / "repo")
    monkeypatch.setattr(makepkg.paths, "is_root", lambda: False)
    monkeypatch.setattr(makepkg, "_makepkg_one",
                        lambda *a, **k: pytest.fail("must not build when source missing"))
    with pytest.raises(makepkg.MakepkgError):
        makepkg.build_own_packages(offline=True, full_compile=True, progress=lambda _p: None)


def test_offline_full_recompiles_and_preserves_scratch(monkeypatch, tmp_path):
    # FULL tier offline with a populated scratch -> RE-COMPILE (offline=True on every
    # makepkg call) AND leave the fetched source tree intact (the offline path must
    # never wipe the scratch, or the next rerun loses the Firefox source).
    scratch = tmp_path / "makepkg"
    repo = tmp_path / "repo"
    fps = tmp_path / "fps"
    repo.mkdir(parents=True)
    monkeypatch.setattr(makepkg.paths, "CACHEDIR", tmp_path)
    monkeypatch.setattr(makepkg.paths, "PKG_REPO", repo)
    monkeypatch.setattr(makepkg.paths, "PKG_FINGERPRINTS", fps)
    monkeypatch.setattr(makepkg.paths, "is_root", lambda: False)
    monkeypatch.setattr(makepkg, "_ensure_builder_user", lambda: "me")

    for dirname, _files in pkgbuild_cfg.recipe_dirs(full_compile=True):
        d = scratch / dirname
        (d / ".build").mkdir(parents=True)
        (d / "PKGBUILD").write_text("x")
        (d / ".build" / "sentinel").write_text("keep")

    calls = []

    def fake_one(builder, d, offline=False):
        calls.append((d.name, offline))
        (d / f"{d.name}-1-1-x86_64.pkg.tar.zst").write_text("")
    monkeypatch.setattr(makepkg, "_makepkg_one", fake_one)

    makepkg.build_own_packages(offline=True, full_compile=True, progress=lambda _p: None)

    assert calls, "offline full recompile did not invoke makepkg"
    assert all(off is True for _name, off in calls), "recompile must pass offline=True"
    # scratch (and its fetched source tree) must survive the recompile.
    assert (scratch / "librewolf" / ".build" / "sentinel").exists()
    # A recompile RE-STAMPS the recipe fingerprints (in the dedicated dir, full tier)
    # so the next run can detect a recipe change. calamares is one of the own packages.
    assert makepkg._read_recipe_fingerprint(fps, "calamares") == \
        makepkg._current_recipe_fingerprints(full_compile=True)["calamares"]


# --- _harden_dlagents: retry/stall-recovery flags on network curl agents -----
# A real build died fetching the calamares tarball with `curl: (92) HTTP/2 stream
# reset (0x8 CANCEL)` even though the stock https DLAGENT already had --retry 3.
# These tests pin the fix: network curl agents gain --retry-all-errors (retry a
# mid-stream reset, which plain --retry won't) and --speed-time/--speed-limit
# (abort a slow-crawl stall so a retry can happen), without disturbing anything
# else in the config, without duplicate --retry flags, and idempotently.
def test_harden_adds_recovery_flags_to_https_agent():
    out = makepkg._harden_dlagents(_STOCK_DLAGENTS)
    https = _agent_line(out, "https")
    # The two flags the stock agent lacked -- the actual fix for the observed failure.
    assert "--retry-all-errors" in https
    assert "--speed-time" in https and "--speed-limit" in https


def test_harden_hardens_all_network_curl_agents():
    out = makepkg._harden_dlagents(_STOCK_DLAGENTS)
    for proto in ("http", "https", "ftp"):
        assert "--retry-all-errors" in _agent_line(out, proto), proto


def test_harden_leaves_local_file_agent_untouched():
    # file:: is a local copy; retry/speed flags are meaningless there. It shares the
    # physical `DLAGENTS=(...` line, so this also guards the parser from hardening an
    # agent just because the line starts with DLAGENTS.
    out = makepkg._harden_dlagents(_STOCK_DLAGENTS)
    assert _agent_line(out, "file") == _agent_line(_STOCK_DLAGENTS, "file")


def test_harden_leaves_non_curl_agents_untouched():
    out = makepkg._harden_dlagents(_STOCK_DLAGENTS)
    assert _agent_line(out, "rsync") == _agent_line(_STOCK_DLAGENTS, "rsync")
    assert _agent_line(out, "scp") == _agent_line(_STOCK_DLAGENTS, "scp")


def test_harden_no_duplicate_retry_flags():
    # The stock line already had `--retry 3`; ours must REPLACE it, not append a
    # second one (curl would take the last value, silently overriding our count).
    out = makepkg._harden_dlagents(_STOCK_DLAGENTS)
    https = _agent_line(out, "https")
    assert https.count("--retry ") == 1
    assert https.count("--retry-delay") == 1
    assert https.count("--speed-limit") == 1


def test_harden_preserves_curl_url_placeholders_and_flags():
    # makepkg substitutes %o (output) and %u (url); losing either breaks every
    # download. The stock functional flags must also survive.
    out = makepkg._harden_dlagents(_STOCK_DLAGENTS)
    https = _agent_line(out, "https")
    assert https.rstrip().endswith("-o %o %u'")
    assert '-qgb ""' in https and "-fLC -" in https


def test_harden_preserves_unrelated_config_lines():
    out = makepkg._harden_dlagents(_STOCK_DLAGENTS)
    assert 'CFLAGS="-march=native -O2"' in out
    assert "PKGEXT='.pkg.tar.zst'" in out
    assert "# some preamble" in out


def test_harden_is_idempotent():
    once = makepkg._harden_dlagents(_STOCK_DLAGENTS)
    twice = makepkg._harden_dlagents(once)
    assert once == twice


def test_harden_handles_config_without_dlagents():
    # A config that never defines DLAGENTS (makepkg would use its built-in default)
    # must pass through unchanged rather than error.
    conf = 'CFLAGS="-O2"\nPKGEXT=".pkg.tar.zst"\n'
    assert makepkg._harden_dlagents(conf) == conf


# --- _write_hardened_conf: the thin IO wrapper around _harden_dlagents -------
def test_write_hardened_conf_writes_hardened_file(tmp_path):
    recipe = tmp_path / "calamares"
    recipe.mkdir()
    sysconf = tmp_path / "makepkg.conf"
    sysconf.write_text(_STOCK_DLAGENTS)

    dest = makepkg._write_hardened_conf(recipe, system_conf=sysconf)

    assert dest is not None and dest.exists()
    assert dest.parent == recipe
    text = dest.read_text()
    assert "--retry-all-errors" in _agent_line(text, "https")
    # It is a standalone config (no `source`/include) so makepkg needs nothing else.
    assert "source " not in text


def test_write_hardened_conf_returns_none_when_system_conf_missing(tmp_path):
    # Missing system config -> None (makepkg falls back to its own default; we lose
    # only the extra resilience, never the build).
    recipe = tmp_path / "calamares"
    recipe.mkdir()
    missing = tmp_path / "does-not-exist.conf"
    assert makepkg._write_hardened_conf(recipe, system_conf=missing) is None
