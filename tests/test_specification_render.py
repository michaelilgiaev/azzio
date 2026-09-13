"""specification_render -- the two pure helpers that turn resolved package data into the
General specification's DERIVED numbers.

These matter because they are the only place the developer-facing document turns
byte counts into human-readable sizes and folds the classified closure into the
per-category capability table. Both are silent-failure surfaces:

  * `_human_size` picks a unit and a precision by threshold. The GiB branch uses
    TWO decimals, the MiB/KiB branches use ONE, and raw bytes get no unit math.
    A wrong threshold (`>` vs `>=`) or a swapped precision would render a size
    like "1024.0 KiB" instead of "1.0 MiB", or lose a digit on the total ISO
    size -- nothing downstream catches it because the output is prose.
  * `_category_summary` walks the closure once, tallies count+installed-size per
    category, EMITS THEM IN `CATEGORY_ORDER`, OMITS empty categories, and IGNORES
    any category not in `CATEGORY_ORDER`. A regression there reorders the
    capability table, invents empty rows, or double-counts sizes.

Everything here is pure: hand-built dicts, zero network/subprocess/filesystem.
The exact literals below were verified against the module source and against a
live call -- e.g. exactly 1 GiB renders "1.00 GiB", exactly 1 MiB renders
"1.0 MiB", and `CATEGORY_ORDER` is the canonical 22-entry ordering.
"""

from __future__ import annotations

import specification_render as R
import specification_classify as K


# --------------------------------------------------------------------------- #
# _human_size -- threshold ladder + per-branch precision
# --------------------------------------------------------------------------- #

def test_human_size_raw_bytes_no_unit_math():
    # Below 1024 the value is emitted verbatim with a " B" suffix, no division.
    assert R._human_size(0) == "0 B"


def test_human_size_just_below_kib_stays_bytes():
    # 1023 is one byte under the KiB threshold -> still bytes, not "1.0 KiB".
    assert R._human_size(1023) == "1023 B"


def test_human_size_kib_threshold_is_inclusive():
    # Exactly 1024 crosses into KiB (the guard is >=, not >).
    assert R._human_size(1024) == "1.0 KiB"


def test_human_size_kib_one_decimal():
    # KiB branch uses exactly one decimal place.
    assert R._human_size(1536) == "1.5 KiB"


def test_human_size_just_below_mib_stays_kib():
    # One byte under 1 MiB: still the KiB branch, rounding up to "1024.0 KiB".
    # This is the boundary the MiB precision-swap would corrupt.
    assert R._human_size(1024 ** 2 - 1) == "1024.0 KiB"


def test_human_size_mib_threshold_is_inclusive():
    # Exactly 1 MiB crosses into MiB (>= guard) with one decimal.
    assert R._human_size(1024 ** 2) == "1.0 MiB"


def test_human_size_mib_one_decimal():
    assert R._human_size(int(1.5 * 1024 ** 2)) == "1.5 MiB"


def test_human_size_just_below_gib_stays_mib():
    # One byte under 1 GiB -> MiB branch, one decimal, "1024.0 MiB".
    assert R._human_size(1024 ** 3 - 1) == "1024.0 MiB"


def test_human_size_gib_threshold_is_inclusive_and_two_decimals():
    # Exactly 1 GiB crosses into GiB. GiB is the ONLY branch with two decimals.
    assert R._human_size(1024 ** 3) == "1.00 GiB"


def test_human_size_gib_two_decimals_nontrivial():
    assert R._human_size(int(2.5 * 1024 ** 3)) == "2.50 GiB"


def test_human_size_gib_precision_is_higher_than_lower_branches():
    # Guard the deliberate asymmetry: GiB keeps two fractional digits, the
    # smaller units keep one. A copy-paste that unified them would break this.
    gib = R._human_size(1024 ** 3)
    mib = R._human_size(1024 ** 2)
    assert gib.endswith(" GiB") and gib.split(" ")[0].count(".") == 1
    assert len(gib.split(".")[1].split(" ")[0]) == 2   # two digits after point
    assert len(mib.split(".")[1].split(" ")[0]) == 1   # one digit after point


# --------------------------------------------------------------------------- #
# _category_summary -- ordering, omission, ignore-unknown, size aggregation
# --------------------------------------------------------------------------- #

def _tags(mapping):
    """Wrap a name->category map into the {name: {'category': cat}} shape the
    walker expects."""
    return {name: {"category": cat} for name, cat in mapping.items()}


def test_category_summary_empty_closure_is_empty_list():
    # No packages -> every category count is 0 -> nothing emitted.
    assert R._category_summary({}, [], {}) == []


def test_category_summary_emits_in_category_order_not_closure_order():
    # 'Audio' appears earlier in the closure than 'Core system', but 'Core system'
    # precedes 'Audio' in CATEGORY_ORDER, so the row order must follow the order,
    # never the closure iteration order.
    assert K.CATEGORY_ORDER.index("Core system") < K.CATEGORY_ORDER.index("Audio")
    packages = {"a": {"isize": 1000}, "b": {"isize": 500}}
    closure = ["a", "b"]
    tags = _tags({"a": "Audio", "b": "Core system"})
    result = R._category_summary(packages, closure, tags)
    assert [c for c, _n, _s in result] == ["Core system", "Audio"]


def test_category_summary_counts_and_sizes_aggregate_per_category():
    # Two packages in one category -> count 2 and summed installed size.
    packages = {"a": {"isize": 1000}, "b": {"isize": 500}, "c": {"isize": 2048}}
    closure = ["a", "b", "c"]
    tags = _tags({"a": "Audio", "b": "Core system", "c": "Audio"})
    result = R._category_summary(packages, closure, tags)
    assert result == [("Core system", 1, 500), ("Audio", 2, 3048)]


def test_category_summary_omits_categories_with_zero_packages():
    # Only 'Audio' is populated; none of the other 21 categories appear as rows.
    packages = {"a": {"isize": 10}}
    closure = ["a"]
    tags = _tags({"a": "Audio"})
    result = R._category_summary(packages, closure, tags)
    assert [c for c, _n, _s in result] == ["Audio"]


def test_category_summary_ignores_category_not_in_order():
    # A package classified into a category outside CATEGORY_ORDER is dropped
    # entirely -- it neither becomes a row nor perturbs the counts.
    bogus = "Definitely Not A Real Category"
    assert bogus not in K.CATEGORY_ORDER
    packages = {"a": {"isize": 10}, "x": {"isize": 9999}}
    closure = ["a", "x"]
    tags = _tags({"a": "Audio", "x": bogus})
    result = R._category_summary(packages, closure, tags)
    assert result == [("Audio", 1, 10)]


def test_category_summary_missing_package_contributes_zero_size():
    # Package present in closure/tags but absent from `packages`, or present with
    # no 'isize' key, must fall back to 0 size (still counted), never KeyError.
    packages = {"a": {"isize": 4096}, "b": {}}   # 'c' absent entirely, 'b' has no isize
    closure = ["a", "b", "c"]
    tags = _tags({"a": "Audio", "b": "Audio", "c": "Audio"})
    result = R._category_summary(packages, closure, tags)
    assert result == [("Audio", 3, 4096)]


def test_category_summary_result_shape_is_three_tuples():
    # Each row is exactly (category:str, count:int, size:int).
    packages = {"a": {"isize": 2048}}
    closure = ["a"]
    tags = _tags({"a": "Audio"})
    (row,) = R._category_summary(packages, closure, tags)
    cat, count, size = row
    assert isinstance(cat, str) and isinstance(count, int) and isinstance(size, int)
    assert (cat, count, size) == ("Audio", 1, 2048)


def test_category_summary_does_not_mutate_category_order():
    # The walker builds its own dicts from CATEGORY_ORDER; it must leave the
    # shared classify constant untouched.
    before = list(K.CATEGORY_ORDER)
    R._category_summary({"a": {"isize": 1}}, ["a"], _tags({"a": "Audio"}))
    assert list(K.CATEGORY_ORDER) == before


# --------------------------------------------------------------------------- #
# Supporting pure helpers used by the render() body
# --------------------------------------------------------------------------- #

def test_capability_known_category_returns_role_blurb():
    # A category present in CATEGORY_BLURB returns its role-level description,
    # never the bare name.
    blurb = R._capability("Audio")
    assert blurb == R.CATEGORY_BLURB["Audio"]
    assert blurb != "Audio"


def test_capability_unknown_category_falls_back_to_name():
    # Categories with no blurb fall back to the category name verbatim.
    assert R._capability("Zzz Nonexistent") == "Zzz Nonexistent"


def test_capability_covers_every_category_in_order():
    # Every canonical category has a blurb, so the capability table never falls
    # back to a bare name for a real row.
    for c in K.CATEGORY_ORDER:
        assert c in R.CATEGORY_BLURB, c


def test_pkg_version_present_returns_version_field():
    assert R._pkg_version({"x": {"version": "1.2-3"}}, "x") == "1.2-3"


def test_pkg_version_missing_returns_question_mark():
    # Unknown package -> "?" sentinel, never a KeyError.
    assert R._pkg_version({}, "x") == "?"
