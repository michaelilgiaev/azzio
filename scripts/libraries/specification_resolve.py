"""
specification_resolve -- turn the package manifest + the Arch DB universe into the real
dependency graph of the Azzio distribution.

Steps:
  1. Load the explicit manifest (libraries/packages/packages.x86_64).
  2. Resolve every entry to a concrete package, expanding groups (e.g. xorg)
     to their members and following `provides` for virtual deps.
  3. Walk the full transitive dependency closure from those roots.
  4. Compute graph tiers: base/sinks (out-degree 0), top/leaves (in-degree 0),
     transitive dependent/dependency counts, and layer heights.

Everything here is derived from the real `%DEPENDS%`/`%PROVIDES%` data -- no
guessing from package names.
"""

import sys
from collections import defaultdict, deque

from specification_db import dep_name

sys.setrecursionlimit(100000)


def load_manifest(manifest_path):
    """Return (unique_tokens_in_order, raw_line_count). Dupes de-duped, order kept."""
    raw = []
    with open(manifest_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw.append(line)
    seen = set()
    unique = []
    for tok in raw:
        if tok not in seen:
            seen.add(tok)
            unique.append(tok)
    return unique, len(raw)


def resolve_token(token, packages, provides):
    """Resolve one dep/manifest token to a concrete package name, or None."""
    name = dep_name(token)
    if name in packages:
        return name
    if name in provides:
        return sorted(provides[name])[0]
    return None


def resolve_closure(manifest_tokens, packages, provides, groups):
    """Expand groups, then BFS the full transitive closure.

    Returns a dict with: manifest_map, roots (set), closure (set),
    edges (pkg -> set of dep pkgs within the closure), unresolved (list).
    """
    manifest_map = {}
    roots = set()

    for token in manifest_tokens:
        if token in groups:
            members = sorted(set(groups[token]))
            manifest_map[token] = {"kind": "group", "members": members}
            roots.update(members)
            continue
        pkg = resolve_token(token, packages, provides)
        if pkg:
            manifest_map[token] = {"kind": "package", "resolved": pkg}
            roots.add(pkg)
        else:
            manifest_map[token] = {"kind": "unresolved"}

    closure = set()
    edges = defaultdict(set)
    unresolved = []
    queue = deque(sorted(roots))
    while queue:
        pkg = queue.popleft()
        if pkg in closure:
            continue
        closure.add(pkg)
        rec = packages.get(pkg)
        if not rec:
            continue
        for dtok in rec["depends"]:
            dep_pkg = resolve_token(dtok, packages, provides)
            if dep_pkg is None:
                unresolved.append((pkg, dtok))
                continue
            edges[pkg].add(dep_pkg)
            if dep_pkg not in closure:
                queue.append(dep_pkg)

    return {
        "manifest_map": manifest_map,
        "roots": roots,
        "closure": closure,
        "edges": {k: (v & closure) for k, v in edges.items()},
        "unresolved": unresolved,
    }


def stock_reachable(stock_tokens, azzio_closure, packages, provides, groups):
    """Return the set of packages in the Azzio closure that the STOCK archiso
    `releng` medium already pulls in.

    We resolve the stock releng manifest through the exact same machinery as the
    Azzio manifest (groups expanded, `provides` followed) and walk its full
    transitive closure, then intersect with the Azzio closure. A package is
    "Stock Arch" iff it lands in that intersection; everything else in the
    Azzio closure is there only because of an Azzio addition.

    Resolving the stock list on its own graph (rather than reusing the Azzio
    edges) is deliberate: it answers "what would plain archiso install?" honestly,
    independent of what Azzio happens to also request.
    """
    stock = resolve_closure(stock_tokens, packages, provides, groups)
    return stock["closure"] & azzio_closure


def _reach_from(seeds, edges, closure):
    """Every package in `closure` reachable from `seeds` by following `edges`
    (dependency direction), including the seeds themselves. Iterative BFS."""
    seen = set()
    stack = [s for s in seeds if s in closure]
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        for d in edges.get(p, ()):  # edges are already intersected with closure
            if d not in seen:
                stack.append(d)
    return seen


def attribute_entries(resolved):
    """Answer "which manifest entry is responsible for each component, and what
    does each entry pull into the system?" purely from the resolved graph.

    A manifest ENTRY is one line the author wrote in packages.x86_64 (a package,
    or a group like `xorg` that expands to its members). Each entry has a
    set of ROOTS (itself, or the group's members). An entry "brings" every package
    reachable from its roots along the dependency edges -- its transitive closure
    contribution. Because dependency graphs share heavily, one package is usually
    brought by several entries; we record all of them (attribution) and single out
    the ones a given entry brings ALONE.

    A package is EXCLUSIVE to an entry when that entry is the only one that reaches
    it: remove the entry from the manifest and the package leaves the set. That is
    exactly the "what do I lose if I cut this" signal for pruning the manifest.
    (Group members are treated as one entry: dropping the group drops them all, so
    a package only they reach is exclusive to the group entry.)

    Returns a dict:
      entries      -- ordered list (manifest order) of per-entry records:
                        {token, kind, roots, brings (sorted), exclusive (sorted),
                         shared (sorted), sharedCount}
      broughtBy    -- {pkg: [entry_token, ...]}  every entry that reaches pkg
      exclusiveTo  -- {pkg: entry_token}  the sole entry, when pkg has exactly one
      order        -- the entry tokens in manifest order
    """
    manifest_map = resolved["manifest_map"]
    edges = resolved["edges"]
    closure = resolved["closure"]

    # roots per entry (skip tokens that resolved to nothing -- they bring nothing)
    entry_roots = {}
    for token, info in manifest_map.items():
        if info["kind"] == "group":
            entry_roots[token] = [m for m in info["members"] if m in closure]
        elif info["kind"] == "package":
            r = info["resolved"]
            entry_roots[token] = [r] if r in closure else []
        else:  # unresolved
            entry_roots[token] = []

    reach = {tok: _reach_from(roots, edges, closure)
             for tok, roots in entry_roots.items()}

    # attribution: for each package, which entries reach it
    brought_by = defaultdict(list)
    for tok in manifest_map:                       # manifest order preserved
        for p in reach[tok]:
            brought_by[p].append(tok)

    exclusive_to = {p: toks[0] for p, toks in brought_by.items() if len(toks) == 1}

    entries = []
    order = []
    for tok, info in manifest_map.items():
        order.append(tok)
        brings = reach[tok]
        excl = sorted(p for p in brings if len(brought_by[p]) == 1)
        shared = sorted(p for p in brings if len(brought_by[p]) > 1)
        entries.append({
            "token": tok,
            "kind": info["kind"],
            "roots": sorted(entry_roots[tok]),
            "brings": sorted(brings),
            "exclusive": excl,
            "shared": shared,
            "sharedCount": len(shared),
        })

    return {
        "entries": entries,
        "broughtBy": {p: toks for p, toks in brought_by.items()},
        "exclusiveTo": exclusive_to,
        "order": order,
    }


def _strongly_connected_components(closure, edges):
    """Tarjan's SCC algorithm (iterative, so deep dependency graphs don't blow
    the Python stack). Returns (comp_id_of_node, list_of_components).

    Real package graphs contain dependency cycles; grouping cyclic packages into
    one component is what lets the height below be computed deterministically.
    """
    index_of = {}
    low = {}
    on_stack = set()
    stack = []
    comp_of = {}
    comps = []
    counter = 0

    for root in sorted(closure):
        if root in index_of:
            continue
        # work stack of (node, iterator-position) frames
        work = [(root, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = sorted(edges.get(node, ()))
            if pi < len(children):
                work[-1] = (node, pi + 1)
                child = children[pi]
                if child not in index_of:
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
            else:
                if low[node] == index_of[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp_of[w] = len(comps)
                        comp.append(w)
                        if w == node:
                            break
                    comps.append(comp)
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
    return comp_of, comps


def _longest_height(closure, edges):
    """Longest chain of dependencies below each package (0 = sink).

    Computed on the SCC condensation so it is deterministic and cycle-safe: a
    dependency cycle is one tier (its members share a height), and the height is
    1 + the deepest height among the *other* components it depends on. This
    avoids the order-dependent result a naive recursive cycle-cut produces.
    """
    comp_of, comps = _strongly_connected_components(closure, edges)

    # Edges between distinct components, in the condensation DAG.
    comp_edges = defaultdict(set)
    for a, deps in edges.items():
        ca = comp_of[a]
        for d in deps:
            cd = comp_of[d]
            if cd != ca:
                comp_edges[ca].add(cd)

    # Longest path in the condensation DAG via memoized recursion. No cycles
    # remain, so the memo is unconditional and the result is order-independent.
    comp_h = {}

    def cheight(c):
        if c in comp_h:
            return comp_h[c]
        best = 0
        for nxt in comp_edges.get(c, ()):
            h = cheight(nxt) + 1
            if h > best:
                best = h
        comp_h[c] = best
        return best

    for c in range(len(comps)):
        cheight(c)

    return {p: comp_h[comp_of[p]] for p in closure}


def compute_tiers(resolved):
    """Enrich the resolved closure with per-package graph metrics + tier lists."""
    closure = resolved["closure"]
    edges = resolved["edges"]

    rev = defaultdict(set)
    for a, deps in edges.items():
        for d in deps:
            rev[d].add(a)

    outdeg = {p: len(edges.get(p, ())) for p in closure}
    indeg = {p: len(rev.get(p, ())) for p in closure}

    def _reachable(start_map, p):
        seen = set()
        stack = list(start_map.get(p, ()))
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(start_map.get(x, ()))
        return len(seen)

    trans_dependents = {p: _reachable(rev, p) for p in closure}
    trans_deps = {p: _reachable(edges, p) for p in closure}
    heights = _longest_height(closure, edges)

    leaves = sorted(p for p in closure if indeg[p] == 0)
    bases = sorted(p for p in closure if outdeg[p] == 0)

    return {
        "rev": {k: sorted(v) for k, v in rev.items()},
        "outdeg": outdeg,
        "indeg": indeg,
        "trans_dependents": trans_dependents,
        "trans_deps": trans_deps,
        "heights": heights,
        "leaves": leaves,
        "bases": bases,
        "max_height": max(heights.values()) if heights else 0,
    }
