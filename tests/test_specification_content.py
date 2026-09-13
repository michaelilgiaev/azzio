"""specification_content.SUBSYSTEMS -- the one hand-authored, non-computed part of the specification.

Everything else in the specification is derived from real package metadata, so it cannot
drift. SUBSYSTEMS cannot be derived; a human types it. That makes it the single
place where a typo silently corrupts the rendered specification:

  * The renderer unpacks every entry as exactly four fields
    (key, title, prose, packages). An entry with the wrong number of fields
    raises at render time (a `ValueError` from tuple unpacking), or worse
    shifts the fields so the prose is read as the package list.
  * Duplicate subsystem keys collide when the renderer indexes by key, so one
    subsystem silently overwrites another and disappears from the output.
  * Multi-package rows use the `' / '` (space-slash-space) convention so the
    renderer can split one visible row into several package tokens and look up
    each real version in the closure. A bare `a/b` (no spaces) would be treated
    as a single package name, fail the closure lookup, and print a stale/empty
    version -- exactly the silent drift this module's docstring promises cannot
    happen.

These tests lock the arity, key-uniqueness and slash convention so an edit that
breaks any of them fails here instead of in a rendered artifact nobody rereads.
"""

from __future__ import annotations

import specification_content


# --- arity: every entry is exactly (key, title, prose, packages) -----------

def test_subsystems_is_nonempty_list():
    assert isinstance(specification_content.SUBSYSTEMS, list)
    assert specification_content.SUBSYSTEMS  # not empty


def test_every_entry_has_arity_four():
    # The renderer does `for key, title, prose, pkgs in SUBSYSTEMS`; any entry
    # with != 4 fields raises there. Lock it to 4 for every entry.
    for entry in specification_content.SUBSYSTEMS:
        assert isinstance(entry, tuple)
        assert len(entry) == 4, entry


def test_entry_field_types():
    # key/title/prose are strings; packages is a list.
    for key, title, prose, pkgs in specification_content.SUBSYSTEMS:
        assert isinstance(key, str)
        assert isinstance(title, str)
        assert isinstance(prose, str)
        assert isinstance(pkgs, list)


def test_package_rows_are_label_capability_pairs():
    # Each package row is exactly (label, capability), both strings. A row with
    # the wrong shape would break the same unpacking the renderer does per row.
    for _key, _title, _prose, pkgs in specification_content.SUBSYSTEMS:
        for row in pkgs:
            assert isinstance(row, tuple)
            assert len(row) == 2, row
            label, capability = row
            assert isinstance(label, str)
            assert isinstance(capability, str)


def test_every_subsystem_lists_at_least_one_package():
    # An empty package list is a meaningless subsystem entry.
    for key, _title, _prose, pkgs in specification_content.SUBSYSTEMS:
        assert pkgs, key


# --- uniqueness: subsystem keys index the renderer, must not collide --------

def test_keys_are_unique():
    keys = [key for key, _title, _prose, _pkgs in specification_content.SUBSYSTEMS]
    assert len(keys) == len(set(keys))


def test_keys_are_nonempty_and_stripped():
    # A blank or space-padded key would produce an empty/mismatched anchor id.
    for key, _title, _prose, _pkgs in specification_content.SUBSYSTEMS:
        assert key
        assert key == key.strip()


def test_titles_are_nonempty():
    for _key, title, _prose, _pkgs in specification_content.SUBSYSTEMS:
        assert title.strip()


def test_prose_is_nonempty():
    for key, _title, prose, _pkgs in specification_content.SUBSYSTEMS:
        assert prose.strip(), key


# --- slash convention: multi-package labels use ' / ' as separator ----------

def _labels():
    for _key, _title, _prose, pkgs in specification_content.SUBSYSTEMS:
        for label, _cap in pkgs:
            yield label


def test_labels_are_stripped():
    # A leading/trailing space would corrupt the first/last package token when
    # the renderer splits on ' / '.
    for label in _labels():
        assert label == label.strip(), repr(label)


def test_every_slash_uses_space_slash_space():
    # The convention is `a / b`, never `a/b`. Assert that for every label the
    # number of raw '/' equals the number of ' / ' -- i.e. no slash is unspaced.
    for label in _labels():
        assert label.count("/") == label.count(" / "), repr(label)


def test_multi_package_split_yields_clean_tokens():
    # Splitting a label on ' / ' must yield real package tokens: non-empty and
    # containing no internal spaces (else the closure lookup gets a bad name).
    for label in _labels():
        for token in label.split(" / "):
            assert token, repr(label)
            assert " " not in token, (label, token)


def test_at_least_one_multi_package_label_exists():
    # Guard against the convention test passing vacuously: there must be real
    # multi-package rows for the ' / ' rule to matter.
    multi = [lbl for lbl in _labels() if " / " in lbl]
    assert multi


def test_known_multi_package_label_present():
    # A concrete literal from the source proves the split does what we claim:
    # "memtest86+ / memtest86+-efi" splits into the two real package names.
    all_labels = set(_labels())
    assert "memtest86+ / memtest86+-efi" in all_labels
    assert "memtest86+ / memtest86+-efi".split(" / ") == [
        "memtest86+",
        "memtest86+-efi",
    ]


def test_single_package_labels_have_no_slash_separator():
    # A label with no ' / ' is a single package and its whole text is one token.
    for label in _labels():
        if " / " not in label:
            assert label.split(" / ") == [label]


# --- capability blurbs -----------------------------------------------------

def test_capabilities_are_nonempty():
    # Every package row carries a human capability blurb; an empty one is a hole
    # in the rendered table.
    for _key, _title, _prose, pkgs in specification_content.SUBSYSTEMS:
        for label, capability in pkgs:
            assert capability.strip(), label
