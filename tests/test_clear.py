"""clear.sh -- the repo's build-tree wipe script (dev-only; never shipped to the ISO).

`clear.sh` removes the generated build directories (output/ logs/ cache/) and sweeps every
__pycache__ in the tree, reporting per-target what happened. This suite pins its SELECTIVE
flags: a developer can clear only PART of the tree instead of the whole lot.

Contract (from the request):
  * no flags       -> clear all three dirs AND every __pycache__ (the original behaviour, UNCHANGED)
  * -o / --output  -> clear ONLY output/
  * -l / --logs    -> clear ONLY logs/
  * -c / --cache   -> clear ONLY cache/  (and __pycache__ is swept WITH cache, never on its own)
  * flags COMBINE  -> `-o -l` clears output/ and logs/ but leaves cache/ (and __pycache__)
  * -h / --help    -> print usage, exit 0, delete nothing
  * unknown flag   -> exit non-zero, print usage, delete nothing

Everything runs against a THROWAWAY copy of clear.sh in a tmp dir with fake output//logs//cache/
/__pycache__ trees -- the real repo tree is never touched. (clear.sh cd's to its own dir and the
__pycache__ find is rooted there, so a copy in tmp_path is fully self-contained.)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CLEAR_SH = REPO / "clear.sh"

# The three build dirs clear.sh manages, in the order the script names them.
BUILD_DIRS = ("logs", "cache", "output")


def _make_tree(root: Path) -> Path:
    """Copy clear.sh into `root` and lay down fake output//logs//cache/ (each with a file)
    plus a couple of __pycache__ dirs (one nested under a source-ish subtree). Returns the
    path of the clear.sh copy to invoke."""
    for d in BUILD_DIRS:
        (root / d).mkdir()
        (root / d / "junk.bin").write_text("x")           # non-empty so du/rm have something real
    # __pycache__ scattered like pytest/imports leave them: one at the top, one nested.
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "mod.cpython-311.pyc").write_text("x")
    (root / "libraries" / "sub").mkdir(parents=True)
    (root / "libraries" / "sub" / "__pycache__").mkdir()
    (root / "libraries" / "sub" / "__pycache__" / "a.cpython-311.pyc").write_text("x")

    script = root / "clear.sh"
    shutil.copy2(CLEAR_SH, script)
    return script


def _run(script: Path, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *flags],
        cwd=str(script.parent),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _pycache_dirs(root: Path) -> list[Path]:
    return [p for p in root.rglob("__pycache__") if p.is_dir()]


# --- no flags: the original everything-clear, UNCHANGED ---------------------------

def test_no_flags_clears_all_three_dirs(tmp_path):
    script = _make_tree(tmp_path)
    res = _run(script)
    assert res.returncode == 0, res.stderr
    for d in BUILD_DIRS:
        assert not (tmp_path / d).exists(), f"{d}/ should be gone with no flags\n{res.stdout}"


def test_no_flags_sweeps_pycache(tmp_path):
    script = _make_tree(tmp_path)
    _run(script)
    assert _pycache_dirs(tmp_path) == [], "no flags must sweep every __pycache__"


# --- -o / --output : ONLY output/ --------------------------------------------------

@pytest.mark.parametrize("flag", ["-o", "--output"])
def test_output_flag_clears_only_output(tmp_path, flag):
    script = _make_tree(tmp_path)
    res = _run(script, flag)
    assert res.returncode == 0, res.stderr
    assert not (tmp_path / "output").exists(), "output/ should be cleared"
    assert (tmp_path / "logs").exists(), "logs/ must survive -o"
    assert (tmp_path / "cache").exists(), "cache/ must survive -o"


def test_output_flag_leaves_pycache(tmp_path):
    # __pycache__ is swept WITH cache only -- clearing output/ must not touch it.
    script = _make_tree(tmp_path)
    _run(script, "-o")
    assert len(_pycache_dirs(tmp_path)) == 2, "-o must leave __pycache__ alone"


# --- -l / --logs : ONLY logs/ ------------------------------------------------------

@pytest.mark.parametrize("flag", ["-l", "--logs"])
def test_logs_flag_clears_only_logs(tmp_path, flag):
    script = _make_tree(tmp_path)
    res = _run(script, flag)
    assert res.returncode == 0, res.stderr
    assert not (tmp_path / "logs").exists(), "logs/ should be cleared"
    assert (tmp_path / "output").exists(), "output/ must survive -l"
    assert (tmp_path / "cache").exists(), "cache/ must survive -l"


def test_logs_flag_leaves_pycache(tmp_path):
    script = _make_tree(tmp_path)
    _run(script, "-l")
    assert len(_pycache_dirs(tmp_path)) == 2, "-l must leave __pycache__ alone"


# --- -c / --cache : ONLY cache/, and it sweeps __pycache__ WITH it -----------------

@pytest.mark.parametrize("flag", ["-c", "--cache"])
def test_cache_flag_clears_only_cache(tmp_path, flag):
    script = _make_tree(tmp_path)
    res = _run(script, flag)
    assert res.returncode == 0, res.stderr
    assert not (tmp_path / "cache").exists(), "cache/ should be cleared"
    assert (tmp_path / "output").exists(), "output/ must survive -c"
    assert (tmp_path / "logs").exists(), "logs/ must survive -c"


def test_cache_flag_also_sweeps_pycache(tmp_path):
    # The request is explicit: pycache is cleared WITH cache.
    script = _make_tree(tmp_path)
    _run(script, "-c")
    assert _pycache_dirs(tmp_path) == [], "-c must sweep __pycache__ along with cache/"


# --- combined flags ----------------------------------------------------------------

def test_output_and_logs_combined_leaves_cache_and_pycache(tmp_path):
    script = _make_tree(tmp_path)
    res = _run(script, "-o", "-l")
    assert res.returncode == 0, res.stderr
    assert not (tmp_path / "output").exists(), "output/ cleared by -o"
    assert not (tmp_path / "logs").exists(), "logs/ cleared by -l"
    assert (tmp_path / "cache").exists(), "cache/ must survive -o -l"
    assert len(_pycache_dirs(tmp_path)) == 2, "__pycache__ must survive -o -l (only -c sweeps it)"


# --- help --------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_prints_usage_and_deletes_nothing(tmp_path, flag):
    script = _make_tree(tmp_path)
    res = _run(script, flag)
    assert res.returncode == 0, res.stderr
    out = (res.stdout + res.stderr).lower()
    assert "usage" in out, f"help must print usage\n{res.stdout}\n{res.stderr}"
    # Nothing deleted.
    for d in BUILD_DIRS:
        assert (tmp_path / d).exists(), f"{d}/ must survive --help"
    assert len(_pycache_dirs(tmp_path)) == 2, "--help must not sweep __pycache__"


# --- unknown flag ------------------------------------------------------------------

def test_unknown_flag_exits_nonzero_with_usage_and_deletes_nothing(tmp_path):
    script = _make_tree(tmp_path)
    res = _run(script, "--bogus")
    assert res.returncode != 0, "unknown flag must exit non-zero"
    out = (res.stdout + res.stderr).lower()
    assert "usage" in out, f"unknown flag must print usage\n{res.stdout}\n{res.stderr}"
    # A rejected invocation must not have deleted anything.
    for d in BUILD_DIRS:
        assert (tmp_path / d).exists(), f"{d}/ must survive a rejected invocation"
    assert len(_pycache_dirs(tmp_path)) == 2, "a rejected invocation must not sweep __pycache__"
