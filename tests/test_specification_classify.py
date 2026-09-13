"""specification_classify -- the editorial layer that decides, for every package in the
closure, (a) which edition it belongs to (stock Arch vs an Azzio Component)
and (b) a single human-language category role.

These are pure functions over plain dicts -- no metadata is fetched, nothing is
guessed at random -- so they are the cheapest place to lock down behavior that
otherwise fails silently:

  * The edition literal is the exact string "azzio" WITH the apostrophe. A
    renderer that groups by edition string will silently split the graph in two
    if this ever drifts to "azzio".
  * The 5-stage category cascade (curated -> group -> dev-tool -> name pattern ->
    desc keyword -> shared-lib/System fallback) has a fixed precedence. A curated
    name must beat everything; a group must beat a name prefix; etc. If the order
    slips, a compositor gets tagged as a plain library and the graph is wrong but
    still "valid".
  * The desc keyword rules are WHOLE-WORD regexes on purpose: "ssl" must not fire
    on "lossless", "crypto" must not fire on "non-cryptographic". A regression to
    raw substring matching would mislabel dozens of packages and nothing would
    catch it.
  * CATEGORY_ORDER (the legend/SVG order) and CATEGORY_COLORS (the palette) must
    describe the exact same set of categories, or the legend and the colours drift
    apart and a category renders with no colour or vice versa.
  * classify() must emit a stable 4-key record for every closure member so the
    downstream renderers can index it without KeyErrors.
"""

from __future__ import annotations

import re

from specification_classify import (
    edition_of,
    category_of,
    classify,
    _compile_desc_rules,
    _provides_shared_lib,
    CATEGORY_ORDER,
    CATEGORY_COLORS,
    CURATED,
    GROUP_ROLE,
    DEV_TOOL_NAMES,
    AZZIO_CONFIGURED,
    AZZIO_REMOVED,
)


# --- edition_of -------------------------------------------------------------

def test_edition_literal_apostrophe():
    # Not in the stock-reachable set -> it is an Azzio Component. The literal
    # MUST carry the apostrophe: "azzio", not "azzio".
    assert edition_of("x", set()) == "azzio"


def test_edition_stock_when_reachable():
    # In the stock releng closure -> inherited from stock Arch.
    assert edition_of("x", {"x"}) == "stock"


def test_edition_only_two_values():
    # There are exactly two editions, nothing else.
    assert edition_of("a", {"a"}) == "stock"
    assert edition_of("b", {"a"}) == "azzio"


# --- category cascade precedence -------------------------------------------

def test_cascade_curated_beats_all_compositor():
    # kwin is curated as a compositor; the name prefix "kwin"/"kde" heuristics
    # must not override the curated role.
    assert category_of("kwin", {}) == "Window/compositor"


def test_cascade_curated_xml_library_not_graphics():
    # libxml2 is curated as a Shared library specifically so the "libx*" graphics
    # name rule does NOT wrongly tag it as graphics.
    assert category_of("libxml2", {}) == "Shared library"


def test_group_beats_name_prefix():
    # plasma-foo would match the "plasma" name prefix -> "Desktop shell", but its
    # kf6 GROUP membership is checked first -> "GUI toolkit/framework".
    assert category_of(
        "plasma-foo", {"plasma-foo": {"groups": ["kf6"]}}
    ) == "GUI toolkit/framework"


def test_curated_beats_group():
    # plasma-nm is curated as Networking even though a plasma group would say
    # "Desktop shell". Curated (stage 1) wins over group (stage 2).
    assert category_of(
        "plasma-nm", {"plasma-nm": {"groups": ["plasma"]}}
    ) == "Networking"


def test_dev_tool_exact_name():
    # cmake is in DEV_TOOL_NAMES (stage 2b), reached before any name pattern.
    assert category_of("cmake", {}) == "Developer tools"


def test_dev_tool_beats_desc_keyword():
    # make is a DEV_TOOL_NAME; even a desc that would match "build" resolves via
    # the exact-name stage first (both land on Developer tools, so assert make).
    assert category_of("make", {"make": {"desc": "the GNU build utility"}}) == "Developer tools"


def test_name_prefix_rule_hit():
    # qt6-* -> GUI toolkit/framework via NAME_PREFIX_RULES (no curated/group hit).
    assert category_of("qt6-svg", {}) == "GUI toolkit/framework"


def test_name_substr_rule_hit():
    # "-icon-theme" is a NAME_SUBSTR rule -> Fonts & icons.
    assert category_of("papirus-icon-theme", {}) == "Fonts & icons"


# --- desc keyword whole-word regex ------------------------------------------

def test_desc_ssl_no_false_positive_in_lossless():
    # Raw substring matching would fire "ssl" inside "lossless"; the whole-word
    # regex must NOT.
    pat = _compile_desc_rules([("ssl", "X")])[0][0]
    assert not pat.search("lossless")


def test_desc_ssl_true_positive_whole_word():
    pat = _compile_desc_rules([("ssl", "X")])[0][0]
    assert pat.search("uses ssl")


def test_desc_multiword_matches_hyphen_but_not_glued():
    # "virtual machine" must match "virtual-machine" (flexible separator) but NOT
    # the glued "virtualmachine".
    pat = _compile_desc_rules([("virtual machine", "V")])[0][0]
    assert pat.search("a virtual-machine monitor")
    assert not pat.search("virtualmachine")


def test_desc_keyword_drives_category():
    # No curated/group/name hit; the desc "firewall" keyword lands it in
    # Security & crypto (whole-word DESC rule).
    assert category_of(
        "zzznotcurated", {"zzznotcurated": {"desc": "a simple firewall frontend"}}
    ) == "Security & crypto"


# --- fallback: shared lib vs System -----------------------------------------

def test_fallback_lib_prefix_is_shared_library():
    # Uncurated name starting with "lib" and no other signal -> Shared library.
    assert category_of("libwhatever", {}) == "Shared library"


def test_fallback_provides_so_is_shared_library():
    # No "lib" prefix but provides a .so -> Shared library.
    assert category_of(
        "zzz", {"zzz": {"provides_raw": ["something.so=1-64"]}}
    ) == "Shared library"


def test_fallback_nothing_is_system():
    # No signal at all -> the generic System bucket.
    assert category_of("zzz", {}) == "System"


# --- _provides_shared_lib ---------------------------------------------------

def test_provides_shared_lib_detects_so():
    assert _provides_shared_lib({"provides_raw": ["libfoo.so=1-64"]}) is True


def test_provides_shared_lib_ignores_non_so():
    assert _provides_shared_lib({"provides_raw": ["foo=1", "bar"]}) is False


def test_provides_shared_lib_missing_key():
    # Absent provides_raw must not raise; defaults to no shared lib.
    assert _provides_shared_lib({}) is False


def test_provides_shared_lib_strips_version_before_check():
    # The "=" version suffix is stripped before the .so test; the base still has .so.
    assert _provides_shared_lib({"provides_raw": ["libx.so=2-64"]}) is True


# --- classify() shape + note ------------------------------------------------

def test_classify_keys_equal_closure():
    out = classify({}, ["a", "b", "c"], set())
    assert set(out) == {"a", "b", "c"}


def test_classify_record_has_four_keys():
    out = classify({"fastfetch": {}}, ["fastfetch"], set())
    assert set(out["fastfetch"]) == {"edition", "category", "azzio_note", "removed"}


def test_classify_azzio_note_from_configured():
    # fastfetch is an AZZIO_CONFIGURED package; its note must be the exact string.
    out = classify({"fastfetch": {}}, ["fastfetch"], set())
    assert out["fastfetch"]["azzio_note"] == AZZIO_CONFIGURED["fastfetch"]


def test_classify_note_none_for_unconfigured():
    # A package with no Azzio modification has a None note.
    out = classify({}, ["linux"], {"linux"})
    assert out["linux"]["azzio_note"] is None


def test_classify_edition_split():
    # closure member in stock_reachable -> stock; the other -> azzio.
    out = classify({}, ["linux", "fastfetch"], {"linux"})
    assert out["linux"]["edition"] == "stock"
    assert out["fastfetch"]["edition"] == "azzio"


def test_classify_removed_flag_is_false_when_removed_set_empty():
    # AZZIO_REMOVED is empty, so nothing is ever flagged removed.
    out = classify({"fastfetch": {}}, ["fastfetch"], set())
    assert out["fastfetch"]["removed"] is False


# --- cross-cutting legend/palette invariants --------------------------------

def test_category_order_equals_colors():
    # The legend order and the colour palette must cover the exact same categories.
    assert set(CATEGORY_COLORS) == set(CATEGORY_ORDER)


def test_category_order_has_no_duplicates():
    assert len(CATEGORY_ORDER) == len(set(CATEGORY_ORDER))


def test_every_color_is_six_hex():
    for cat, color in CATEGORY_COLORS.items():
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", color), (cat, color)


def test_curated_categories_within_order():
    # Every category a curated entry can emit must be a real legend category.
    order = set(CATEGORY_ORDER)
    for pkg, cat in CURATED.items():
        assert cat in order, (pkg, cat)


def test_group_and_devtool_categories_within_order():
    order = set(CATEGORY_ORDER)
    for grp, cat in GROUP_ROLE.items():
        assert cat in order, (grp, cat)
    assert "Developer tools" in order
    # DEV_TOOL_NAMES all resolve to the Developer tools bucket.
    assert all(category_of(name, {}) == "Developer tools" for name in DEV_TOOL_NAMES)


# --- AZZIO_CONFIGURED / AZZIO_REMOVED invariants --------------------------

def test_azzio_configured_exact_keys():
    assert set(AZZIO_CONFIGURED) == {
        "fastfetch", "pacman", "filesystem", "systemd",
        "grub", "syslinux", "sudo", "ufw",
    }


def test_azzio_configured_notes_non_empty_strings():
    for pkg, note in AZZIO_CONFIGURED.items():
        assert isinstance(note, str) and note.strip(), pkg


def test_azzio_removed_is_empty_set():
    assert AZZIO_REMOVED == set()
