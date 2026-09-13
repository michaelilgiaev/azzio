"""specification_svg -- the single self-contained SVG view of the dependency graph.

The image places every package on one of seven horizontal bands by its true
dependency depth (the longest chain of dependencies below it), colours it by
category, and draws only the most load-bearing subset per band. Three pure
helpers carry the load-bearing logic and are what silently corrupt the picture
if they drift:

  * `layer_of` -- the depth->band threshold ladder. Because `height` is a true
    partial order (dep below dependent), the ladder is what guarantees a
    dependency never renders above its dependent. A shifted boundary (e.g. a
    kernel at height 22 sliding out of band 4) mislabels the whole stack, and
    nothing raises.
  * `_representatives` -- picks which packages a band shows. The top band (apps)
    ranks leaves by how much they pull in (`trans_deps`, transitive
    *dependencies*); every other band ranks by how much rests on them
    (`trans_dependents`). These two look-alike keys are trivially swappable and
    the swap changes which boxes appear with no error.
  * `_wrap` / `_text_w` -- greedy word wrap against an approximate glyph-width
    model so rail labels never spill onto the component boxes.

These are pure functions, so we pin their exact outputs against hand-built
fixtures small enough to reason about by hand.
"""

from __future__ import annotations

import specification_svg
from specification_svg import (
    layer_of,
    _representatives,
    _wrap,
    _text_w,
    _esc,
    LAYER_DEFS,
    EDITION_MARK,
)


# --- layer_of: the depth -> band threshold ladder --------------------------

def test_layer_of_height_zero_is_foundation():
    # height 0 == a true sink (nothing below it) == the bottom band.
    assert layer_of("anything", "any-category", 0) == 0


def test_layer_of_full_boundary_ladder():
    # Every band's exact inclusive boundaries, straight from the source ladder:
    #   0 -> 0, 1..3 -> 1, 4..8 -> 2, 9..14 -> 3, 15..23 -> 4, 24..33 -> 5, 34+ ->6.
    expected = {
        0: 0,
        1: 1, 2: 1, 3: 1,
        4: 2, 5: 2, 6: 2, 7: 2, 8: 2,
        9: 3, 10: 3, 11: 3, 12: 3, 13: 3, 14: 3,
        15: 4, 16: 4, 17: 4, 18: 4, 19: 4, 20: 4, 21: 4, 22: 4, 23: 4,
        24: 5, 25: 5, 26: 5, 27: 5, 28: 5, 29: 5, 30: 5, 31: 5, 32: 5, 33: 5,
        34: 6, 40: 6, 44: 6, 100: 6,
    }
    got = {h: layer_of("p", "c", h) for h in expected}
    assert got == expected


def test_layer_of_boundary_edges_exact():
    # The exact upper edge of each band and the first value of the next band --
    # the off-by-one spots where a dependency could slide above its dependent.
    assert layer_of("p", "c", 3) == 1 and layer_of("p", "c", 4) == 2
    assert layer_of("p", "c", 8) == 2 and layer_of("p", "c", 9) == 3
    assert layer_of("p", "c", 14) == 3 and layer_of("p", "c", 15) == 4
    assert layer_of("p", "c", 23) == 4 and layer_of("p", "c", 24) == 5
    assert layer_of("p", "c", 33) == 5 and layer_of("p", "c", 34) == 6


def test_layer_of_kernel_and_coreutils_land_in_band_4():
    # The docstring names these two heights explicitly as belonging to band 4;
    # this is the invariant the comment stresses (kernel h=22, coreutils h=21).
    assert layer_of("linux", "Kernel & firmware", 22) == 4
    assert layer_of("coreutils", "Core system", 21) == 4


def test_layer_of_ignores_pkg_and_category():
    # `pkg` and `category` are unused (colour axis, not vertical). Same height
    # with wildly different pkg/category must yield the same band.
    a = layer_of("kwin", "Window/compositor", 20)
    b = layer_of("glibc", "Shared library", 20)
    assert a == b == 4


def test_layer_of_never_exceeds_last_band_index():
    # Band index is always a valid index into LAYER_DEFS regardless of height.
    for h in (0, 1, 33, 34, 44, 999):
        assert 0 <= layer_of("p", "c", h) < len(LAYER_DEFS)


# --- _representatives: which boxes a band draws ----------------------------

def _tiers(trans_dependents, trans_deps, indeg):
    """Minimal tiers dict with only the keys _representatives reads."""
    return {
        "trans_dependents": trans_dependents,
        "trans_deps": trans_deps,
        "indeg": indeg,
    }


def test_representatives_non_top_ranks_by_transitive_dependents():
    # Non-top bands rank by how much rests on a package (trans_dependents),
    # descending. Highest dependents first.
    pkgs = ["a", "b", "c"]
    tiers = _tiers(
        trans_dependents={"a": 1, "b": 9, "c": 4},
        trans_deps={"a": 0, "b": 0, "c": 0},
        indeg={"a": 0, "b": 0, "c": 0},
    )
    assert _representatives(pkgs, 2, tiers, per_layer=30) == ["b", "c", "a"]


def test_representatives_non_top_ignores_trans_deps_key():
    # The look-alike swap: for a non-top band, trans_deps (dependencies) MUST NOT
    # drive the order. Here trans_deps would sort the opposite way; the result
    # must still follow trans_dependents.
    pkgs = ["a", "b"]
    tiers = _tiers(
        trans_dependents={"a": 10, "b": 1},   # a first by dependents
        trans_deps={"a": 1, "b": 10},          # b first by dependencies (a trap)
        indeg={"a": 0, "b": 0},
    )
    assert _representatives(pkgs, 2, tiers, per_layer=30) == ["a", "b"]


def test_representatives_tiebreak_is_name_ascending():
    # Equal rank -> sorted by package name ascending (the `, p)` secondary key).
    pkgs = ["zeta", "alpha", "mid"]
    tiers = _tiers(
        trans_dependents={"zeta": 5, "alpha": 5, "mid": 5},
        trans_deps={"zeta": 0, "alpha": 0, "mid": 0},
        indeg={"zeta": 0, "alpha": 0, "mid": 0},
    )
    assert _representatives(pkgs, 3, tiers, per_layer=30) == ["alpha", "mid", "zeta"]


def test_representatives_top_band_ranks_leaves_by_trans_deps():
    # Top band (idx 6) shows the LEAVES (indeg==0) ranked by how much they pull
    # in (trans_deps, transitive dependencies), descending. Here trans_dependents
    # is the trap: it would order the opposite way and must be ignored.
    pkgs = ["app1", "app2", "app3"]
    tiers = _tiers(
        trans_dependents={"app1": 9, "app2": 8, "app3": 7},  # trap ordering
        trans_deps={"app1": 1, "app2": 2, "app3": 3},         # real ordering
        indeg={"app1": 0, "app2": 0, "app3": 0},
    )
    assert _representatives(pkgs, 6, tiers, per_layer=30) == ["app3", "app2", "app1"]


def test_representatives_top_band_filters_to_leaves():
    # In the top band only indeg==0 packages (real leaves) are drawn; a package
    # something still depends on (indeg>0) is dropped even if it sits in the band.
    pkgs = ["leaf", "notleaf"]
    tiers = _tiers(
        trans_dependents={"leaf": 0, "notleaf": 0},
        trans_deps={"leaf": 5, "notleaf": 9},
        indeg={"leaf": 0, "notleaf": 3},   # notleaf has an in-edge -> not a leaf
    )
    assert _representatives(pkgs, 6, tiers, per_layer=30) == ["leaf"]


def test_representatives_top_band_falls_back_to_all_when_no_leaves():
    # `[...] or pkgs`: if the band has NO indeg==0 members, it falls back to the
    # whole band rather than drawing nothing.
    pkgs = ["x", "y"]
    tiers = _tiers(
        trans_dependents={"x": 0, "y": 0},
        trans_deps={"x": 1, "y": 2},
        indeg={"x": 1, "y": 1},   # nobody is a leaf
    )
    assert set(_representatives(pkgs, 6, tiers, per_layer=30)) == {"x", "y"}


def test_representatives_top_band_missing_indeg_treated_as_leaf():
    # indeg.get(p, 0) == 0: a package absent from the indeg map counts as a leaf.
    pkgs = ["absent"]
    tiers = _tiers(
        trans_dependents={"absent": 0},
        trans_deps={"absent": 7},
        indeg={},   # 'absent' not present -> defaults to 0 -> counts as a leaf
    )
    assert _representatives(pkgs, 6, tiers, per_layer=30) == ["absent"]


def test_representatives_respects_per_layer_cap():
    # Only the first `per_layer` after sorting are returned.
    pkgs = ["a", "b", "c", "d"]
    tiers = _tiers(
        trans_dependents={"a": 4, "b": 3, "c": 2, "d": 1},
        trans_deps={"a": 0, "b": 0, "c": 0, "d": 0},
        indeg={"a": 0, "b": 0, "c": 0, "d": 0},
    )
    assert _representatives(pkgs, 1, tiers, per_layer=2) == ["a", "b"]


def test_representatives_empty_band_returns_empty():
    tiers = _tiers(trans_dependents={}, trans_deps={}, indeg={})
    assert _representatives([], 3, tiers, per_layer=30) == []


# --- _text_w: approximate glyph-width model --------------------------------

def test_text_w_default_factor_arithmetic():
    # len(s) * size * factor, factor defaults to 0.55.
    assert _text_w("abcd", 10) == 4 * 10 * 0.55   # == 22.0


def test_text_w_empty_string_is_zero():
    assert _text_w("", 18) == 0


def test_text_w_custom_factor():
    assert _text_w("ab", 10, factor=1.0) == 20.0


# --- _wrap: greedy word wrap against the width model -----------------------

def test_wrap_short_text_stays_single_line():
    # Well within max_w -> one line, unmodified.
    assert _wrap("libc filesystem", 1000, 11) == ["libc filesystem"]


def test_wrap_empty_text_yields_no_lines():
    assert _wrap("", 100, 11) == []


def test_wrap_breaks_when_next_word_overflows():
    # Two words fit; adding the third would exceed max_w, so it starts a new line.
    # Each word "ab" is 2*10*0.55 = 11.0 px; " " joins add width too.
    # max_w chosen so exactly two words fit per line.
    text = "aa bb cc dd"
    lines = _wrap(text, 60, 10, factor=0.55)
    # "aa bb" -> 5 chars * 10 * 0.55 = 27.5 <= 60; "aa bb cc" -> 8*5.5 = 44 <= 60;
    # "aa bb cc dd" -> 11*5.5 = 60.5 > 60, so "dd" wraps.
    assert lines == ["aa bb cc", "dd"]


def test_wrap_never_splits_an_oversized_single_word():
    # `or not cur`: a single word wider than max_w is still emitted alone rather
    # than dropped or chopped mid-word.
    lines = _wrap("supercalifragilisticexpialidocious", 10, 11)
    assert lines == ["supercalifragilisticexpialidocious"]


def test_wrap_oversized_word_forces_its_own_line():
    # A normal word, then an oversized word that cannot join: the oversized word
    # gets a line to itself and does not swallow the previous line's content.
    lines = _wrap("hi hugewordthatwontfitanywhere ok", 40, 11, factor=0.55)
    assert lines[0] == "hi"
    assert "hugewordthatwontfitanywhere" in lines


def test_wrap_lines_are_reassemblable_into_original_words():
    # No word is lost or duplicated: concatenating all wrapped lines back returns
    # the original whitespace-split word sequence in order.
    text = "one two three four five six seven eight nine ten"
    lines = _wrap(text, 45, 11)
    assert " ".join(lines).split() == text.split()


def test_wrap_collapses_runs_of_whitespace():
    # text.split() with no arg collapses arbitrary internal whitespace runs.
    lines = _wrap("a    b\t c", 1000, 11)
    assert lines == ["a b c"]


# --- _esc: SVG text escaping -----------------------------------------------

def test_esc_escapes_xml_metacharacters():
    # quote=True so both kinds of quotes are entity-encoded alongside < > &.
    assert _esc("<a> & \"b\" 'c'") == "&lt;a&gt; &amp; &quot;b&quot; &#x27;c&#x27;"


def test_esc_coerces_non_strings():
    # _esc(str(s)): an int is stringified before escaping (used for pkg counts).
    assert _esc(42) == "42"


def test_esc_plain_text_unchanged():
    assert _esc("plain-name_1.2") == "plain-name_1.2"


# --- LAYER_DEFS / EDITION_MARK: static data invariants ---------------------

def test_layer_defs_has_seven_bands():
    # Seven depth bands, each a (title, subtitle) pair.
    assert len(LAYER_DEFS) == 7
    assert all(len(entry) == 2 for entry in LAYER_DEFS)


def test_layer_defs_titles_are_the_expected_ladder_labels():
    titles = [t for t, _ in LAYER_DEFS]
    assert titles == [
        "Foundation",
        "Depth 1-3",
        "Depth 4-8",
        "Depth 9-14",
        "Depth 15-23",
        "Depth 24-33",
        "Leaves (34+)",
    ]


def test_edition_mark_star_only_for_azzio():
    # An Azzio Component gets the star glyph; Stock Arch is unmarked (empty
    # glyph, no colour). The empty-string glyph is what makes render skip the
    # marker draw for stock packages.
    assert EDITION_MARK["azzio"] == ("★", specification_svg.BRAND_CYAN)
    assert EDITION_MARK["stock"] == ("", None)


def test_edition_mark_keys_are_exactly_two_editions():
    assert set(EDITION_MARK) == {"azzio", "stock"}
