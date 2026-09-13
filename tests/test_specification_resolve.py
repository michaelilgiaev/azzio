"""specification_resolve -- the dependency-graph engine behind the Azzio specification.

Every number the specification pages report (what a package brings, what is exclusive to
one manifest entry, how deep a package sits in the dependency stack, which
packages are stock Arch vs. Azzio additions) is derived here from the raw
`%DEPENDS%`/`%PROVIDES%` data. A silent off-by-one in the closure walk, a seed
that leaks into (or out of) a transitive count, a cycle that inflates a height,
or a provider tie-break that flips would corrupt the whole downstream report
without ever raising. These are pure-graph functions, so we pin their exact
outputs against hand-built dict fixtures small enough to reason about by hand.
"""

from __future__ import annotations

import specification_resolve
from specification_resolve import (
    load_manifest,
    resolve_token,
    resolve_closure,
    stock_reachable,
    attribute_entries,
    compute_tiers,
    _reach_from,
    _strongly_connected_components,
    _longest_height,
)


# --- fixtures --------------------------------------------------------------

def _chain_packages():
    """a -> b -> c -> (missing).  A straight dependency chain with one dangling
    dep so the unresolved path is exercised too."""
    return {
        "a": {"depends": ["b"]},
        "b": {"depends": ["c"]},
        "c": {"depends": ["missing"]},
        "other": {"depends": []},
    }


def _shared_packages():
    """Two independent entries p and q both need `common`; p also needs `ponly`.
    Gives a graph with both an exclusive and a shared package."""
    return {
        "p": {"depends": ["common", "ponly"]},
        "q": {"depends": ["common"]},
        "common": {"depends": []},
        "ponly": {"depends": []},
    }


# --- load_manifest ---------------------------------------------------------

def test_load_manifest_counts_dedups_and_keeps_inline_hash(tmp_path):
    # Blank/whitespace-only lines and whole-line comments are dropped; a repeated
    # token is de-duped but still counts once toward the raw line count; a token
    # with a trailing "# ..." is kept VERBATIM (this tokenizer does NOT strip
    # inline comments, unlike the pacman-config tokenizer), and surrounding
    # whitespace is trimmed.
    manifest = tmp_path / "packages.x86_64"
    manifest.write_text(
        "# comment line\n"
        "\n"
        "foo\n"
        "bar\n"
        "foo\n"          # duplicate -> de-duped, but still a raw line
        "baz # inline\n"  # inline hash retained as part of the token
        "   \n"           # whitespace-only -> treated as blank
        "   qux   \n"     # trimmed to 'qux'
    )
    unique, raw = load_manifest(str(manifest))
    assert unique == ["foo", "bar", "baz # inline", "qux"]
    assert raw == 5  # foo,bar,foo,baz#inline,qux  (comment+blanks excluded)


def test_load_manifest_first_occurrence_order_preserved(tmp_path):
    manifest = tmp_path / "m"
    manifest.write_text("z\na\nz\nb\na\n")
    unique, raw = load_manifest(str(manifest))
    assert unique == ["z", "a", "b"]  # order of FIRST sighting
    assert raw == 5


# --- resolve_token ---------------------------------------------------------

def test_resolve_token_direct_package_beats_provides():
    # A concrete package of that exact name wins over any virtual provider.
    assert resolve_token("foo", {"foo": {}}, {"foo": ["other"]}) == "foo"


def test_resolve_token_provider_tiebreak_is_sorted_first():
    # No direct package -> fall through to provides; ties broken by sorting the
    # provider names and taking the first. Also proves dep_name strips ": desc".
    assert resolve_token("virt: desc", {}, {"virt": ["zeta", "alpha"]}) == "alpha"


def test_resolve_token_strips_version_operator():
    assert resolve_token("foo>=1.2", {"foo": {}}, {}) == "foo"


def test_resolve_token_unresolvable_returns_none():
    assert resolve_token("nope", {}, {}) is None


# --- resolve_closure -------------------------------------------------------

def test_resolve_closure_bfs_edges_and_unresolved():
    pkgs = _chain_packages()
    res = resolve_closure(["a"], pkgs, {}, {})
    assert res["roots"] == {"a"}
    assert res["closure"] == {"a", "b", "c"}
    # edges are intersected with the closure; 'missing' never enters it.
    assert {k: sorted(v) for k, v in res["edges"].items()} == {"a": ["b"], "b": ["c"]}
    # c's dangling dep is recorded, not silently dropped.
    assert res["unresolved"] == [("c", "missing")]


def test_resolve_closure_edges_are_subset_of_closure():
    pkgs = _chain_packages()
    res = resolve_closure(["a"], pkgs, {}, {})
    closure = res["closure"]
    for src, deps in res["edges"].items():
        assert src in closure
        assert deps <= closure


def test_resolve_closure_expands_group_to_members():
    pkgs = {
        "m1": {"depends": []},
        "m2": {"depends": []},
        "solo": {"depends": []},
    }
    # group members are de-duped and sorted in the manifest_map record.
    groups = {"grp": ["m2", "m1", "m1"]}
    res = resolve_closure(["grp", "solo"], pkgs, {}, groups)
    assert res["manifest_map"]["grp"] == {"kind": "group", "members": ["m1", "m2"]}
    assert res["manifest_map"]["solo"] == {"kind": "package", "resolved": "solo"}
    assert res["roots"] == {"m1", "m2", "solo"}
    assert res["closure"] == {"m1", "m2", "solo"}


def test_resolve_closure_marks_unresolved_token():
    res = resolve_closure(["ghost"], {}, {}, {})
    assert res["manifest_map"]["ghost"] == {"kind": "unresolved"}
    assert res["roots"] == set()
    assert res["closure"] == set()


# --- stock_reachable -------------------------------------------------------

def test_stock_reachable_is_intersection_with_azzio_closure():
    pkgs = _chain_packages()
    # Stock manifest ['a'] reaches {a,b,c}; intersect with {'b','z'} -> {'b'}.
    assert stock_reachable(["a"], {"b", "z"}, pkgs, {}, {}) == {"b"}


def test_stock_reachable_empty_when_disjoint():
    pkgs = _chain_packages()
    assert stock_reachable(["a"], {"z1", "z2"}, pkgs, {}, {}) == set()


# --- _reach_from -----------------------------------------------------------

def test_reach_from_includes_the_seed():
    pkgs = _chain_packages()
    res = resolve_closure(["a"], pkgs, {}, {})
    # Following dependency edges from 'a' returns a,b,c -- the seed 'a' INCLUDED.
    assert _reach_from(["a"], res["edges"], res["closure"]) == {"a", "b", "c"}


def test_reach_from_drops_seed_outside_closure():
    pkgs = _chain_packages()
    res = resolve_closure(["a"], pkgs, {}, {})
    # A seed not in the closure contributes nothing.
    assert _reach_from(["zzz"], res["edges"], res["closure"]) == set()


# --- compute_tiers ---------------------------------------------------------

def test_compute_tiers_chain_metrics_exact():
    pkgs = _chain_packages()
    res = resolve_closure(["a"], pkgs, {}, {})
    t = compute_tiers(res)
    assert t["outdeg"] == {"a": 1, "b": 1, "c": 0}
    assert t["indeg"] == {"a": 0, "b": 1, "c": 1}
    assert t["heights"] == {"a": 2, "b": 1, "c": 0}
    assert t["leaves"] == ["a"]   # in-degree 0
    assert t["bases"] == ["c"]    # out-degree 0
    assert t["max_height"] == 2


def test_compute_tiers_rev_index_sorted():
    pkgs = _chain_packages()
    res = resolve_closure(["a"], pkgs, {}, {})
    t = compute_tiers(res)
    # reverse edges: b depended-on by a; c depended-on by b.
    assert t["rev"] == {"b": ["a"], "c": ["b"]}


def test_trans_counts_exclude_the_seed_itself():
    pkgs = _chain_packages()
    res = resolve_closure(["a"], pkgs, {}, {})
    t = compute_tiers(res)
    # trans_deps counts what a package DEPENDS ON, not counting itself.
    assert t["trans_deps"] == {"a": 2, "b": 1, "c": 0}
    # trans_dependents counts what DEPENDS ON a package, not counting itself.
    assert t["trans_dependents"] == {"a": 0, "b": 1, "c": 2}
    # Contrast with _reach_from, which DOES include the seed:
    assert _reach_from(["c"], res["edges"], res["closure"]) == {"c"}
    assert t["trans_deps"]["c"] == 0


def test_compute_tiers_empty_closure_max_height_zero():
    res = resolve_closure([], {}, {}, {})
    t = compute_tiers(res)
    assert t["max_height"] == 0
    assert t["leaves"] == []
    assert t["bases"] == []


# --- SCC + longest height (cycle safety) -----------------------------------

def test_scc_groups_a_two_node_cycle():
    # a <-> b form a cycle; external c depends on a but is not part of it.
    pkgs = {
        "a": {"depends": ["b"]},
        "b": {"depends": ["a"]},
        "c": {"depends": ["a"]},
    }
    res = resolve_closure(["c"], pkgs, {}, {})
    comp_of, comps = _strongly_connected_components(res["closure"], res["edges"])
    assert comp_of["a"] == comp_of["b"]
    assert comp_of["c"] != comp_of["a"]
    # every closure member is assigned to exactly one component.
    assert set(comp_of) == res["closure"]
    assert sum(len(c) for c in comps) == len(res["closure"])


def test_longest_height_cycle_is_one_tier_and_terminates():
    # A cycle must not recurse forever and its members must share one height.
    pkgs = {
        "a": {"depends": ["b"]},
        "b": {"depends": ["a"]},
        "c": {"depends": ["a"]},
    }
    res = resolve_closure(["c"], pkgs, {}, {})
    h = _longest_height(res["closure"], res["edges"])
    # a and b are one SCC (height 0); c sits one tier above the cycle.
    assert h["a"] == h["b"] == 0
    assert h["c"] == 1


def test_longest_height_diamond():
    # a -> b -> d and a -> c -> d.  Longest chain below a is 2.
    pkgs = {
        "a": {"depends": ["b", "c"]},
        "b": {"depends": ["d"]},
        "c": {"depends": ["d"]},
        "d": {"depends": []},
    }
    res = resolve_closure(["a"], pkgs, {}, {})
    h = _longest_height(res["closure"], res["edges"])
    assert h == {"a": 2, "b": 1, "c": 1, "d": 0}


# --- attribute_entries -----------------------------------------------------

def test_attribute_entries_exclusive_vs_shared():
    pkgs = _shared_packages()
    res = resolve_closure(["p", "q"], pkgs, {}, {})
    attr = attribute_entries(res)

    assert attr["order"] == ["p", "q"]  # manifest order preserved

    # 'common' is reached by both entries -> shared, not exclusive to either.
    assert sorted(attr["broughtBy"]["common"]) == ["p", "q"]
    assert "common" not in attr["exclusiveTo"]

    # 'ponly' is only reachable via p -> exclusive to p.
    assert attr["exclusiveTo"]["ponly"] == "p"
    assert attr["broughtBy"]["ponly"] == ["p"]

    by_token = {e["token"]: e for e in attr["entries"]}
    p = by_token["p"]
    assert p["kind"] == "package"
    assert p["roots"] == ["p"]
    assert p["brings"] == ["common", "p", "ponly"]  # includes p itself
    assert p["exclusive"] == ["p", "ponly"]
    assert p["shared"] == ["common"]
    assert p["sharedCount"] == 1


def test_attribute_entries_group_member_package_is_exclusive_to_group():
    # A package reachable only through a group's members is exclusive to the
    # single group entry (dropping the group drops it).
    pkgs = {
        "m1": {"depends": ["shared"]},
        "m2": {"depends": ["shared", "only2"]},
        "shared": {"depends": []},
        "only2": {"depends": []},
    }
    groups = {"grp": ["m1", "m2"]}
    res = resolve_closure(["grp"], pkgs, {}, groups)
    attr = attribute_entries(res)
    # Only one entry ('grp') exists, so everything it reaches is exclusive to it.
    assert attr["exclusiveTo"]["only2"] == "grp"
    assert attr["exclusiveTo"]["shared"] == "grp"
    grp = attr["entries"][0]
    assert grp["token"] == "grp"
    assert grp["kind"] == "group"
    assert grp["roots"] == ["m1", "m2"]
    assert grp["shared"] == []  # nothing is shared with a second entry
    assert grp["sharedCount"] == 0


def test_attribute_entries_unresolved_token_brings_nothing():
    # An unresolved manifest token contributes no roots and reaches no package,
    # so it appears in the ordering but with empty brings/exclusive/shared.
    pkgs = {"a": {"depends": []}}
    res = resolve_closure(["a", "ghost"], pkgs, {}, {})
    attr = attribute_entries(res)
    by_token = {e["token"]: e for e in attr["entries"]}
    ghost = by_token["ghost"]
    assert ghost["kind"] == "unresolved"
    assert ghost["roots"] == []
    assert ghost["brings"] == []
    assert ghost["exclusive"] == []
    assert ghost["shared"] == []
    assert ghost["sharedCount"] == 0
    # 'a' is not brought by 'ghost'.
    assert attr["broughtBy"]["a"] == ["a"]


# --- module wiring ---------------------------------------------------------

def test_recursion_limit_raised_at_import():
    # Deep real dependency graphs would overflow the default 1000-frame limit;
    # the module raises it on import so the recursive height memo cannot crash.
    assert specification_resolve.sys.getrecursionlimit() >= 100000
