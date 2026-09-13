"""specification_stock_baseline -- the frozen stock archiso `releng` package baseline.

This module is the ground truth for the "Stock Arch" edition: every package the
official install medium already pulls in. The specification tooling treats STOCK_PACKAGES
as a *set* and subtracts it from the final package list to decide what counts as
an "Azzio Component". Three things silently corrupt that subtraction and none
of them raise at import time:

  * a duplicate entry -- harmless to the set math, but a signal the human copied
    the upstream list wrong, and it makes the "diff cleanly against upstream"
    promise in the module docstring a lie;
  * a token carrying whitespace, an inline comment, or a stray `#` -- it would
    never equal a real package name, so it silently never subtracts, and the
    package it was meant to represent gets mislabelled an Azzio Component;
  * losing the tuple type or the C-locale sort -- the tuple is the "this is fixed
    reference data, not an editable manifest" guarantee, and the sort is what
    makes an archiso bump a readable diff.

These tests pin the invariants the docstring asserts (tuple, unique, sorted,
clean tokens) plus the ARCHISO_VERSION staleness marker. Pure data checks: no
tmp_path, no fakes, no network/subprocess.
"""

from __future__ import annotations

import specification_stock_baseline as baseline
from specification_stock_baseline import ARCHISO_VERSION, STOCK_PACKAGES


# --- type / immutability ----------------------------------------------------

def test_stock_packages_is_a_tuple():
    # A tuple, not a list: it must read as fixed reference data that a stray
    # keystroke cannot mutate in place.
    assert isinstance(STOCK_PACKAGES, tuple)


def test_stock_packages_is_non_empty():
    # An empty baseline would make every stock package look like an Azzio
    # Component -- the subtraction it feeds would be a no-op.
    assert len(STOCK_PACKAGES) > 0


# --- uniqueness -------------------------------------------------------------

def test_stock_packages_has_no_duplicates():
    # len(set) == len is the "copied upstream exactly once each" guard.
    assert len(set(STOCK_PACKAGES)) == len(STOCK_PACKAGES)


def test_stock_packages_duplicate_report_is_empty():
    # Same invariant, but name the offenders if it ever breaks so the failure
    # points straight at the bad line instead of "128 != 127".
    seen: set[str] = set()
    dupes = [pkg for pkg in STOCK_PACKAGES if pkg in seen or seen.add(pkg)]
    assert dupes == []


# --- ordering ---------------------------------------------------------------

def test_stock_packages_is_c_locale_sorted():
    # Kept C-locale sorted (== Python's default codepoint sort for these ASCII
    # names) so an archiso bump diffs cleanly against upstream.
    assert list(STOCK_PACKAGES) == sorted(STOCK_PACKAGES)


def test_stock_packages_sort_is_strictly_increasing():
    # Adjacent-pair check: strictly increasing proves both sorted AND no
    # duplicates in one pass, and localises the first out-of-order pair.
    pairs = list(zip(STOCK_PACKAGES, STOCK_PACKAGES[1:]))
    offenders = [(a, b) for a, b in pairs if not a < b]
    assert offenders == []


# --- token cleanliness ------------------------------------------------------

def test_every_entry_is_a_str():
    assert all(isinstance(pkg, str) for pkg in STOCK_PACKAGES)


def test_no_entry_is_empty():
    # An empty token would collide with the manifest tokenizer's dropped blanks
    # and never match a real package.
    assert all(pkg != "" for pkg in STOCK_PACKAGES)


def test_no_entry_has_surrounding_whitespace():
    # Each token must already be stripped: `pkg == pkg.strip()` for every entry.
    unstripped = [pkg for pkg in STOCK_PACKAGES if pkg != pkg.strip()]
    assert unstripped == []


def test_no_entry_contains_internal_whitespace():
    # No spaces/tabs anywhere: a package name is a single shell/pacman token.
    dirty = [pkg for pkg in STOCK_PACKAGES if any(c.isspace() for c in pkg)]
    assert dirty == []


def test_no_entry_is_or_contains_a_comment():
    # Comments/blank lines were meant to be stripped when the list was captured;
    # a leftover `#` (leading or inline) would never equal a real package name.
    with_hash = [pkg for pkg in STOCK_PACKAGES if "#" in pkg]
    assert with_hash == []


def test_entries_survive_the_manifest_tokenizer_unchanged():
    # The consumer runs each line through `line.split("#", 1)[0].strip()`. For a
    # clean baseline that transform must be the identity on every token.
    for pkg in STOCK_PACKAGES:
        assert pkg.split("#", 1)[0].strip() == pkg


# --- staleness marker -------------------------------------------------------

def test_archiso_version_is_a_non_empty_str():
    # ARCHISO_VERSION is the "how stale is this baseline" marker; it must exist
    # and carry a value a reader can compare against upstream.
    assert isinstance(ARCHISO_VERSION, str)
    assert ARCHISO_VERSION.strip() != ""


def test_archiso_version_literal():
    # Pin the captured release so a silent edit to the list without bumping the
    # marker (or vice versa) is visible in the diff.
    assert ARCHISO_VERSION == "88-1"


# --- module surface ---------------------------------------------------------

def test_module_exposes_expected_names():
    # The specification tooling imports exactly these two names; guard against a rename
    # that would break the consumers at import time.
    assert hasattr(baseline, "STOCK_PACKAGES")
    assert hasattr(baseline, "ARCHISO_VERSION")
