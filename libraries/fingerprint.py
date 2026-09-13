"""Own-package recipe fingerprinting -- the staleness gate for Azzio's own packages.

Split out of makepkg.py (which was over the module-size budget): these are the pure,
side-effect-light primitives that decide whether a cached .pkg.tar.zst still matches the
recipe that would build it now. makepkg re-exports them and pairs them with _repo_has_all
in _repo_is_current (the orchestration gate that also checks the package file exists).

THE BUG THIS GUARDS: the offline default tier SKIPS makepkg when the own packages are
already in the repo (the fast rerun). That skip used to be content-BLIND -- it only checked
that a file named `calamares-*.pkg.tar.zst` existed, never whether it was built from the
CURRENT recipe. So editing a recipe (adding the networkq source patch to calamares, or
editing the file manager's vendored C) did NOT invalidate the cached package: the stale binary was
reused and the ISO/box shipped it. The fix: fingerprint the recipe and re-check on reuse; a
missing package, a missing/old sidecar, or a changed recipe all force a rebuild.

WHAT THE FINGERPRINT COVERS: every file in the recipe (PKGBUILD + all companion files, e.g.
the five calamares patches) AND, for a recipe that consumes a vendored source DIRECTORY
(pkgbuild.recipe_source_trees -- file_manager), that tree's content too. So ANY change -- an edit
to a single patch, OR an edit to the vendored source -- flips it. The source-tree half was a
later fix (see _source_tree_fingerprint): without it, editing the file manager's vendored source did
NOT invalidate the cache -- the same content-blind-reuse bug in a different spot.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import paths
import pkgbuild as pkgbuild_cfg


class FingerprintError(RuntimeError):
    """A recipe declares a source tree that is missing/empty -- a build-config error, not
    a cache miss (see _source_tree_fingerprint). makepkg re-exports this."""


FINGERPRINT_SUFFIX = ".recipe-fingerprint"


def _recipe_fingerprint(files: dict[str, str]) -> str:
    """A stable content hash of one recipe (the {filename: content} dict emitted by
    pkgbuild.recipe_dirs). Sorted by filename so the digest is order-independent, and
    both names and bodies are folded in so adding/removing/renaming a companion file
    (a patch) changes the result. Pure -- unit-tested."""
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(files[name].encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _source_tree_fingerprint(tree: Path) -> str:
    """A stable content hash of a whole VENDORED source directory (the git-cloned tree
    a recipe consumes via recipe_source_trees, e.g. packages/file_manager/source). Walks
    every regular file, sorted by its POSIX-relative path, and folds BOTH the relative
    path and the file bytes into the digest so an edit, an add/remove, or a rename all
    change the result. Deterministic (sorted; no mtime/inode/order noise) so the same
    bytes on disk always yield the same digest -- unit-tested.

    WHY THIS EXISTS: _recipe_fingerprint only sees the recipe's text {filename: content}
    dict. A recipe whose source is a DIRECTORY has that tree copied in by _emit_recipes
    OUTSIDE that dict, so editing the vendored C (dropping the file manager's Help menu, say) did
    NOT flip the recipe fingerprint -- the offline cache reused the pre-edit binary and
    the ISO shipped an unfixed file manager. Folding this tree hash into the recipe's
    fingerprint (see _current_recipe_fingerprints) closes that hole, mirroring the
    companion-file (patch) case _recipe_fingerprint already covers.

    Each field is LENGTH-PREFIXED (not delimiter-separated): file CONTENT can legally
    contain any byte, including a separator like NUL (the tree carries binary PNG icons),
    so a NUL-delimited encoding is not injective -- two different trees could serialize to
    the same byte stream and collide to one hash, reusing a stale package. Prefixing every
    variable-length field with its length makes the encoding unambiguous. The exec BIT is
    folded in too: build() runs ./autogen.sh directly and prepare()'s cp -aL / makepkg's
    copytree preserve mode, so losing a script's +x would break the build -- a change that
    must invalidate the cache even though the file's BYTES are unchanged.

    Fails LOUD on a missing/empty tree: an empty digest would be a stable, plausible hash
    that silently masks a misconfigured recipe_source_trees() (a path that moved or a typo),
    letting a stale package be reused forever. A recipe that declares a source tree must
    have one, so its absence is a build-config error, not a cache-miss."""
    if not tree.is_dir() or not any(tree.rglob("*")):
        raise FingerprintError(
            f"source tree for fingerprinting is missing or empty: {tree} "
            "(recipe_source_trees points at a non-existent/empty path)"
        )
    h = hashlib.sha256()

    def _field(b: bytes) -> None:
        # length-prefixed so the byte stream is unambiguous regardless of b's contents
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)

    files = sorted(p for p in tree.rglob("*") if p.is_file())
    _field(str(len(files)).encode("utf-8"))  # file count: also catches add/remove
    for p in files:
        _field(p.relative_to(tree).as_posix().encode("utf-8"))
        st = p.stat()
        # only the owner-exec bit matters for the build (scripts run vs not); fold it in
        _field(b"x" if (st.st_mode & 0o100) else b"-")
        _field(p.read_bytes())
    return h.hexdigest()


def _current_recipe_fingerprints(full_compile: bool) -> dict[str, str]:
    """Map each produced package name -> the fingerprint of the recipe that would
    build it right now. The recipe DIR name (recipe_dirs' first tuple element) is the
    package name for our recipes (calamares/librewolf/file_manager), which is the key the
    sidecar files and produced_names use.

    For a recipe that consumes a VENDORED source TREE (recipe_source_trees -- file_manager
    today), the tree's content hash is folded into the fingerprint too, so editing the
    vendored source invalidates the cached package exactly like editing the PKGBUILD or
    a patch does. Text-only recipes (calamares/librewolf) are unaffected -- they have no
    entry in the source-tree map, so their fingerprint is the pure recipe-text hash."""
    trees = pkgbuild_cfg.recipe_source_trees()
    out: dict[str, str] = {}
    for dirname, files in pkgbuild_cfg.recipe_dirs(full_compile):
        fp = _recipe_fingerprint(files)
        tree = trees.get(dirname)
        if tree is not None:
            # Combine the two hashes into one digest so any change to either side
            # (recipe text OR vendored source) flips the recipe's fingerprint.
            fp = hashlib.sha256(
                f"{fp}\0{_source_tree_fingerprint(Path(tree))}".encode("utf-8")
            ).hexdigest()
        out[dirname] = fp
    return out


def _fingerprint_dir() -> Path:
    """Where the recipe-fingerprint sidecars live -- a dedicated dir, NOT PKG_REPO
    (which is cp -r'd wholesale into the ISO payload; build metadata stays out of it).
    Read at call time so tests that monkeypatch paths.PKG_FINGERPRINTS take effect."""
    return paths.PKG_FINGERPRINTS


def _fingerprint_path(fp_dir: Path, name: str) -> Path:
    return fp_dir / f"{name}{FINGERPRINT_SUFFIX}"


def _write_recipe_fingerprint(fp_dir: Path, name: str, fingerprint: str) -> None:
    """Record the recipe fingerprint for a freshly built package so a later run can
    tell whether the cached package still matches the recipe. Best-effort: a write
    failure just means the next run treats the cache as stale and rebuilds (safe --
    never ships a stale package), so it must not abort the build."""
    try:
        fp_dir.mkdir(parents=True, exist_ok=True)
        _fingerprint_path(fp_dir, name).write_text(
            json.dumps({"name": name, "fingerprint": fingerprint}) + "\n"
        )
    except OSError as e:
        print(f"    [!] Could not write recipe fingerprint for {name}: {e} "
              "(cache will be treated as stale next run).")


def _read_recipe_fingerprint(fp_dir: Path, name: str) -> str | None:
    """The fingerprint recorded for a previously built package, or None if the
    sidecar is absent/unreadable/malformed (any of which means 'can't prove it's
    current' -> caller must rebuild)."""
    try:
        data = json.loads(_fingerprint_path(fp_dir, name).read_text())
    except (OSError, ValueError):
        return None
    fp = data.get("fingerprint") if isinstance(data, dict) else None
    return fp if isinstance(fp, str) else None
