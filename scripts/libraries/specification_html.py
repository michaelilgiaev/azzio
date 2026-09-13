"""
specification_html -- render the Azzio component set as ONE self-contained, interactive
HTML page (documentations/SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html).

This is the interactive twin of the SVG (specification_svg): the same layered map -- seven
horizontal bands from the kernel at the bottom up to the leaf applications at the
top, every component drawn as a box coloured by category and marked by edition
(a star marks an Azzio Component; unmarked boxes are Stock Arch) -- but you can
actually *use* it. Click any component to open a detail panel with its
plain-language purpose, version, edition, category, layer, size and upstream link;
the map then highlights everything it requires (below it) and everything that
requires it (above it), so you see its place in the stack at a glance. Search by
name, filter by category and edition, and the bands re-flow live.

Unlike the SVG (which draws a readable subset per band), this page shows ALL
components, because its job is to let a developer decide what to add or pull out.

The file is fully self-contained: all data is embedded as JSON and all CSS/JS is
inlined, so it opens straight from disk in any browser with no server and no
network. The plain-language descriptions are the official Arch package %DESC%
strings (same source as the full-text specification), so nothing is hand-written or drifts.
"""

import html
import json

import specification_classify as K
import specification_svg as S
import specification_resolve as R
import specification_stock_baseline as B

# Same seven layers as the SVG, bottom (0) -> top (6).
LAYER_DEFS = S.LAYER_DEFS


def _fmt_size(nbytes):
    if nbytes >= 1024 ** 3:
        return f"{nbytes / 1024 ** 3:.2f} GiB"
    if nbytes >= 1024 ** 2:
        return f"{nbytes / 1024 ** 2:.1f} MiB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.0f} KiB"
    return f"{nbytes} B"


EDITION_LABEL = {
    "azzio": "Azzio Component",
    "stock": "Stock Arch",
}


def _build_payload(packages, resolved, tiers, tags, glance, attr):
    """Assemble the JSON payload the page's JavaScript renders from.

    `attr` is specification_resolve.attribute_entries(resolved): the manifest-entry
    attribution that powers the second ("Entries") page and the "brought into the
    set by" line on every component.
    """
    closure = resolved["closure"]
    heights = tiers["heights"]
    edges = resolved["edges"]
    rev = tiers["rev"]
    brought_by = attr["broughtBy"]
    exclusive_to = attr["exclusiveTo"]

    comps = {}
    for p in sorted(closure):
        rec = packages.get(p, {})
        tag = tags[p]
        layer = S.layer_of(p, tag["category"], heights[p])
        comps[p] = {
            "name": p,
            "version": rec.get("version", "?"),
            "repo": rec.get("repo", "?"),
            "desc": rec.get("desc") or "(no description in the Arch package DB)",
            "url": rec.get("url") or "",
            "size": _fmt_size(rec.get("isize", 0)),
            "isize": rec.get("isize", 0),
            "license": rec.get("license") or "",
            "edition": tag["edition"],
            "editionLabel": EDITION_LABEL[tag["edition"]],
            "category": tag["category"],
            "layer": layer,
            "azzio": tag.get("azzio_note") or "",
            "removed": bool(tag.get("removed")),
            "requires": sorted(edges.get(p, ())),
            "requiredBy": sorted(rev.get(p, ())),
            "transDeps": tiers["trans_deps"][p],
            "transDependents": tiers["trans_dependents"][p],
            "optdepends": rec.get("optdepends", []) or [],
            # attribution: which manifest entries pull this package into the set,
            # and whether it is exclusive to a single entry (a manifest root, so
            # brought by nothing, has itself as its own sole entry).
            "broughtBy": brought_by.get(p, []),
            "exclusiveTo": exclusive_to.get(p, ""),
            "isRoot": bool(rec) and p in resolved["roots"],
        }

    # Per-category colour + canonical order, straight from specification_classify so the
    # HTML and the SVG use identical colours.
    present_cats = [c for c in K.CATEGORY_ORDER
                    if any(tags[p]["category"] == c for p in closure)]
    cat_colors = {c: K.CATEGORY_COLORS.get(c, "#8892a0") for c in present_cats}

    layers = [{"idx": i, "title": t, "subtitle": st}
              for i, (t, st) in enumerate(LAYER_DEFS)]

    # ----- manifest entries (the second page) -----
    # One record per line the author wrote in packages.x86_64, IN MANIFEST ORDER
    # (attr["entries"] preserves it). Each carries what it brings into the set, how
    # much of that is exclusive to it, its installed footprint, and -- for the
    # hierarchy view -- the layer/category of its primary root package so entries
    # can be grouped the same way components are.
    # An entry's edition is decided by the MANIFEST BLOCK it lives in, not by its
    # anchor package: a line the author wrote is "stock" iff it is one of the
    # baseline archiso releng package names, else it is an Azzio addition. This
    # is exactly the Stock/Azzio delimiter in packages.x86_64.
    stock_tokens = set(B.STOCK_PACKAGES)
    entries = []
    for e in attr["entries"]:
        tok = e["token"]
        roots = e["roots"]
        brings = e["brings"]
        excl = e["exclusive"]
        # A group entry has no single package of its own; anchor its layer/category
        # on its deepest (highest-layer) root so it sorts near what it installs.
        anchor = None
        if roots:
            anchor = max(roots, key=lambda r: (comps[r]["layer"], r)) \
                if e["kind"] == "group" else roots[0]
        excl_isize = sum(comps[p]["isize"] for p in excl)
        brings_isize = sum(comps[p]["isize"] for p in brings)
        edition = "stock" if tok in stock_tokens else "azzio"
        entries.append({
            "token": tok,
            "kind": e["kind"],
            "roots": roots,
            "brings": brings,
            "exclusive": excl,
            "shared": e["shared"],
            "bringsCount": len(brings),
            "exclusiveCount": len(excl),
            "sharedCount": e["sharedCount"],
            "exclSize": _fmt_size(excl_isize),
            "exclIsize": excl_isize,
            "bringsSize": _fmt_size(brings_isize),
            "bringsIsize": brings_isize,
            "edition": edition,
            "editionLabel": EDITION_LABEL[edition],
            # display anchoring for the hierarchy view (None-safe: an unresolved
            # token anchors nowhere)
            "layer": comps[anchor]["layer"] if anchor else 0,
            "category": comps[anchor]["category"] if anchor else "System",
            "resolved": anchor or "",
        })

    # split point in manifest order, for the entries page header/legend.
    stock_entry_count = sum(1 for e in entries if e["edition"] == "stock")

    return {
        "components": comps,
        "order": sorted(comps),
        "entries": entries,
        "entriesStockCount": stock_entry_count,
        "layers": layers,
        "categories": present_cats,
        "catColors": cat_colors,
        "editionLabels": EDITION_LABEL,
        "glance": {
            "base": glance["base"],
            "desktop": glance["desktop"],
            "kernel": glance["kernel"],
            "init": glance["init"],
            "ram": glance["ram"],
            "closure": glance["closure"],
            "byRepo": glance["by_repo"],
            "azzio": glance["azzio"],
            "stock": glance["stock"],
            "maxHeight": glance["max_height"],
            "size": glance["size"],
            "isoVersion": glance["iso_version"],
        },
    }


def render_html(packages, resolved, tiers, tags, glance):
    """Return the complete self-contained HTML document text."""
    attr = R.attribute_entries(resolved)
    payload = _build_payload(packages, resolved, tiers, tags, glance, attr)
    data_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # Guard against an accidental </script> inside data breaking the tag.
    data_json = data_json.replace("</", "<\\/")
    return _PAGE.replace("/*__DATA__*/", data_json)


# --------------------------------------------------------------------------- #
# The page. One template; Python only injects the data JSON at /*__DATA__*/.
# Brand palette matches specification_svg (INK/PANEL/brand cyan->blue).
# --------------------------------------------------------------------------- #
_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Azzio -- component map</title>
<style>
:root{
  --ink:#0d1117; --panel:#161b22; --panel2:#1b222b; --line:#30363d;
  --text:#e6edf3; --dim:#9da7b3; --cyan:#03a5fc; --blue:#0065f9;
  /* rail (layer labels) and detail panel share this width so the panel overlays
     the rail exactly when a component is opened, never covering the boxes. */
  --rail-w:300px;
  /* horizontal padding inside .map -- referenced by the detail panel so it can
     reach past this gap (plus the band's 1px border) and sit its left edge
     directly on top of the rail's left border. */
  --map-pad:16px;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{
  background:var(--ink); color:var(--text);
  font-family:Inter,'Segoe UI',Helvetica,Arial,sans-serif; font-size:13px;
  display:flex; flex-direction:column; height:100vh; overflow:hidden;
}
a{color:var(--cyan)}
/* ---- top bar ---- */
header{
  padding:10px 16px; border-bottom:1px solid var(--line);
  display:flex; align-items:center; gap:16px; flex-wrap:wrap; background:var(--panel);
}
.brand{font-size:20px;font-weight:800;
  background:linear-gradient(90deg,var(--cyan),var(--blue));
  -webkit-background-clip:text;background-clip:text;color:transparent;white-space:nowrap}
.sub{color:var(--dim);font-size:12px}
.glance{margin-left:auto;display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;
  color:var(--dim);font-size:11.5px}
.glance .fact{white-space:nowrap;display:inline-flex;gap:5px;align-items:baseline}
.glance .fact:not(:last-child)::after{content:"·";color:var(--line);margin-left:9px}
.glance b{color:var(--text);font-weight:600}
/* ---- controls ---- */
.controls{padding:8px 16px;border-bottom:1px solid var(--line);
  display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:var(--panel2)}
.controls input[type=search]{
  background:var(--ink);border:1px solid var(--line);color:var(--text);
  border-radius:7px;padding:6px 10px;min-width:220px;font-size:13px}
.controls select{background:var(--ink);border:1px solid var(--line);color:var(--text);
  border-radius:7px;padding:6px 8px;font-size:12px}
.controls label{color:var(--dim);font-size:11.5px;margin-right:2px}
.controls .count{color:var(--dim);font-size:12px;margin-left:auto}
.btn{background:var(--ink);border:1px solid var(--line);color:var(--text);
  border-radius:7px;padding:6px 10px;cursor:pointer;font-size:12px}
.btn:hover{border-color:var(--cyan)}
/* ---- layout ---- */
/* position:relative so the detail panel can overlay the map instead of
   pushing it -- opening a component must NOT resize/reflow the graph. */
.main{flex:1;display:flex;min-height:0;position:relative}
.map{flex:1;overflow:auto;padding:14px var(--map-pad) 40px}
/* Entries flat view has no right-hand rail for the panel to overlay (unlike the
   component bands), so the panel (position:absolute) would sit on top of the last
   column of entry boxes. To avoid that we permanently reserve the panel's width on
   the RIGHT of the flat boxes (see .flatboxes) -- the last column always drops to
   the next line, whether or not the panel is open, so opening it never hides or
   reflows anything. The reservation lives on .flatboxes (flat mode only); the
   hierarchy layer bands keep their own rail and are unaffected. */
/* ---- bands ---- */
/* Boxes on the LEFT, the layer-label rail on the RIGHT (flex order:2). The rail
   width matches the detail panel (--rail-w) so, when a component is clicked, the
   panel overlays the rail exactly and never covers the component boxes. */
.band{border:1px solid var(--line);border-radius:12px;background:var(--panel);
  margin-bottom:14px;display:flex;min-height:96px}
.rail{width:var(--rail-w);flex:none;padding:12px 14px;border-left:1px solid var(--line);order:2}
.rail .t{font-size:15px;font-weight:700}
.rail .s{font-size:11px;color:var(--dim);margin-top:3px;line-height:1.3}
.rail .n{margin-top:8px;font-size:12px;font-weight:700;
  background:linear-gradient(90deg,var(--cyan),var(--blue));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.rail .arrow{margin-top:6px;font-size:10px;color:var(--dim)}
/* centre the boxes horizontally so a partly-filled row sits in the middle of the
   band instead of hugging the left edge and leaving dead space on the right. */
.boxes{flex:1;padding:12px;display:flex;flex-wrap:wrap;gap:7px;align-content:flex-start;
  justify-content:center}
/* ---- component box ---- */
.box{position:relative;width:150px;border-radius:7px;padding:7px 9px;cursor:pointer;
  border:1px solid var(--bc,#555);background:color-mix(in srgb,var(--bc,#555) 16%,transparent);
  transition:transform .05s ease, box-shadow .1s ease, opacity .1s ease}
.box:hover{transform:translateY(-1px);box-shadow:0 3px 12px rgba(0,0,0,.5)}
.box .bn{font-weight:600;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.box .bv{font-size:10px;color:var(--dim);margin-top:1px}
.box .mark{position:absolute;top:5px;right:7px;font-size:11px;line-height:1}
.box.selected{outline:2px solid var(--cyan);outline-offset:1px}
.box.req{outline:2px solid #eab308;outline-offset:1px}
.box.reqby{outline:2px solid #22c55e;outline-offset:1px}
.box.dim{opacity:.16}
.box.hidden{display:none}
.band.empty{display:none}
.emptynote{color:var(--dim);font-size:12px;padding:6px 2px}
/* ---- detail panel ---- */
/* Overlays the RIGHT edge of the map (position:absolute), as wide as the
   band rails (--rail-w) PLUS the map's right padding and the band's 1px right
   border, so its left edge lands directly on top of the rail's left border --
   it sits exactly on top of the "Foundation / 71 pkgs" labels and never covers
   the component boxes. It does NOT resize or reflow the graph -- the boxes
   underneath are only dimmed. */
.panel{position:absolute;top:0;right:0;height:100%;
  width:calc(var(--rail-w) + var(--map-pad) + 1px);z-index:5;
  border-left:1px solid var(--line);background:var(--panel2);
  box-shadow:-8px 0 24px rgba(0,0,0,.45);
  overflow:auto;padding:0;display:flex;flex-direction:column}
.panel.hidden{display:none}
.panel .ph{padding:14px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel2)}
.panel .ph .close{float:right;cursor:pointer;color:var(--dim);font-size:18px;line-height:1}
.panel .ph .close:hover{color:var(--text)}
.panel h2{margin:0;font-size:18px;word-break:break-all}
.panel .ver{color:var(--dim);font-size:12px;margin-top:2px}
.panel .body{padding:14px 16px}
.panel .purpose{font-size:14px;line-height:1.5;margin:0 0 12px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.chip{font-size:11px;padding:3px 8px;border-radius:999px;border:1px solid var(--line);color:var(--text)}
.chip.cat{}
.chip.ed-az{border-color:var(--cyan);color:var(--cyan)}
.chip.ed-sel{border-color:#9da7b3}
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:12px;margin-bottom:12px}
.kv .k{color:var(--dim)}
.azbox{border:1px solid var(--cyan);border-radius:8px;padding:8px 10px;margin-bottom:12px;
  background:color-mix(in srgb,var(--cyan) 10%,transparent);font-size:12.5px;line-height:1.4}
.azbox .h{color:var(--cyan);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}
.dl{margin:0 0 12px}
.dl .h{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim);margin-bottom:5px;font-weight:700}
.dl .h.req{color:#eab308}
.dl .h.reqby{color:#22c55e}
.pill-wrap{display:flex;flex-wrap:wrap;gap:5px}
.pill{font-size:11px;padding:2px 7px;border-radius:6px;background:var(--ink);border:1px solid var(--line);
  cursor:pointer;white-space:nowrap}
.pill:hover{border-color:var(--cyan);color:var(--cyan)}
.pill.none{opacity:.6;cursor:default;border-style:dashed}
.pill.none:hover{border-color:var(--line);color:var(--dim)}
.opt{font-size:11.5px;color:var(--dim);line-height:1.5}
.opt b{color:var(--text);font-weight:600}
/* ---- collapsible bands ---- */
.rail .t{cursor:pointer;user-select:none}
.rail .t::before{content:"▾ ";color:var(--dim);font-size:12px}
.band.collapsed .rail .t::before{content:"▸ ";}
.band.collapsed .boxes{display:none}
.band.collapsed{min-height:0}
.band.collapsed .rail{border-left:none;width:100%;display:flex;align-items:baseline;gap:14px}
.band.collapsed .rail .s{margin-top:0}
.band.collapsed .rail .n{margin-top:0}
.band.collapsed .rail .arrow{display:none}
/* ---- legend (collapsible footer) ---- */
/* The toggle sits just ABOVE the legend's top border, pinned to the right, so it
   never overlaps the legend's own text (and stays clear of the detail panel,
   which opens on the left). When hidden it drops to the bottom-right corner. */
.legend-toggle{position:fixed;right:14px;bottom:calc(var(--legend-h,150px) + 6px);z-index:6;
  background:var(--ink);border:1px solid var(--line);color:var(--dim);
  border-radius:7px;padding:5px 10px;font-size:11.5px;cursor:pointer}
.legend-toggle.down{bottom:10px}
.legend-toggle:hover{border-color:var(--cyan);color:var(--text)}
.legend{border-top:1px solid var(--line);background:var(--panel);padding:8px 16px;
  display:flex;gap:16px;flex-wrap:wrap;align-items:center;font-size:11.5px;color:var(--dim)}
.legend.hidden{display:none}
.legend .sw{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.legend .grp{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
/* The intro + edition key are ONE full-width row (flex-basis:100%) that never
   shares space with the category swatches / keys group below it. nowrap keeps
   the whole line -- the "Foundation ... inspect it" text, "Edition:", and all
   three marks -- on a single line, and each mark's text stays intact. */
.legend .grp.how{flex-basis:100%;color:var(--text);white-space:nowrap}
.legend .grp.how>span{white-space:nowrap}
.legend .cats{display:flex;gap:10px;flex-wrap:wrap}
kbd{background:var(--ink);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:11px}
/* ---- page tabs (Components / Entries) ---- */
.tabs{display:flex;gap:4px;margin-right:6px}
.tab{background:var(--ink);border:1px solid var(--line);color:var(--dim);
  border-radius:7px;padding:6px 12px;cursor:pointer;font-size:12.5px;font-weight:600}
.tab:hover{border-color:var(--cyan);color:var(--text)}
.tab.active{color:var(--text);border-color:var(--cyan);
  background:color-mix(in srgb,var(--cyan) 14%,transparent)}
/* only one page's controls / view are in the DOM flow at a time */
.page.hidden,.pagectl.hidden{display:none}
/* ---- entries view ---- */
/* Reuses the .map / .band / .rail scaffolding, but each box is an ENTRY (a line
   from packages.x86_64) rather than a component. Flat mode drops the rail and
   lays entries out in manifest order under two headers; hierarchy mode reuses the
   layered bands. */
.ent{position:relative;width:190px;border-radius:7px;padding:7px 9px;cursor:pointer;
  border:1px solid var(--bc,#555);background:color-mix(in srgb,var(--bc,#555) 14%,transparent);
  transition:transform .05s ease, box-shadow .1s ease, opacity .1s ease}
.ent:hover{transform:translateY(-1px);box-shadow:0 3px 12px rgba(0,0,0,.5)}
.ent .en{font-weight:600;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ent .em{font-size:10px;color:var(--dim);margin-top:2px;display:flex;gap:8px;flex-wrap:wrap}
.ent .em b{color:var(--text);font-weight:600}
.ent .em .exl{color:#f0883e}
.ent .mark{position:absolute;top:5px;right:7px;font-size:11px;line-height:1}
.ent .grp-badge{font-size:9px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim);
  border:1px solid var(--line);border-radius:4px;padding:0 4px;margin-left:5px;vertical-align:1px}
.ent.selected{outline:2px solid var(--cyan);outline-offset:1px}
.ent.hidden{display:none}
.ent.dim{opacity:.16}
/* flat manifest-order layout: two labelled sections, boxes flow left-to-right */
.flatsec{margin:0 0 10px}
.flathead{display:flex;align-items:baseline;gap:10px;margin:4px 2px 10px;
  padding-bottom:6px;border-bottom:1px solid var(--line)}
.flathead .ft{font-size:14px;font-weight:700}
.flathead .fs{font-size:11.5px;color:var(--dim)}
.flathead .fn{margin-left:auto;font-size:12px;font-weight:700;
  background:linear-gradient(90deg,var(--cyan),var(--blue));
  -webkit-background-clip:text;background-clip:text;color:transparent}
/* Permanently reserve the detail-panel width on the RIGHT so the last column of
   entry boxes always drops to the next line (never hidden under the panel, never
   reflowing when it opens), and centre the boxes so the partly-filled last row --
   and the leftover margin the reservation creates -- sits in the middle of the
   available area instead of hugging the left edge. */
.flatboxes{display:flex;flex-wrap:wrap;gap:7px;justify-content:center;
  padding-right:calc(var(--rail-w) + var(--map-pad) + 1px)}
/* the entries detail panel groups brought-in packages into exclusive vs shared */
.brk{margin:0 0 12px}
.brk .h{font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px;font-weight:700}
.brk .h.excl{color:#f0883e}
.brk .h.shar{color:var(--dim)}
.brk .lead{font-size:12px;color:var(--dim);line-height:1.45;margin:0 0 10px}
.brk .lead b{color:var(--text)}
.pill.ex{border-color:color-mix(in srgb,#f0883e 55%,var(--line))}
.pill.ex:hover{border-color:#f0883e;color:#f0883e}
.entkv{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:12px;margin-bottom:12px}
.entkv .k{color:var(--dim)}
/* ---- entries overview rail ---- */
/* Fills the strip that .flatboxes reserves on the right so it is never dead
   space. Occupies the SAME geometry as the detail .panel (same width, right-
   aligned), so when an entry is clicked the panel (higher z-index + shadow)
   overlays it exactly -- no layout shift. Static, so no shadow of its own. Its
   stats are recomputed from the currently-visible entries on every filter /
   search / sort change, so it always describes what you are looking at. */
.erail{position:absolute;top:0;right:0;height:100%;
  width:calc(var(--rail-w) + var(--map-pad) + 1px);
  border-left:1px solid var(--line);background:var(--panel);
  overflow:auto;padding:14px 16px;z-index:1}
.erail.hidden{display:none}
.erail h3{margin:0 0 2px;font-size:13px;font-weight:700;letter-spacing:.02em}
.erail .rsub{color:var(--dim);font-size:11.5px;line-height:1.4;margin:0 0 12px}
.erail .stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}
.erail .stat{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--ink)}
.erail .stat .v{font-size:17px;font-weight:700;line-height:1.1}
.erail .stat .v.az{color:var(--cyan)}
.erail .stat .v.exl{color:#f0883e}
.erail .stat .l{font-size:10.5px;color:var(--dim);margin-top:2px}
.erail .rh{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);
  font-weight:700;margin:0 0 6px}
.erail .top{list-style:none;margin:0 0 14px;padding:0}
.erail .top li{display:flex;align-items:baseline;gap:8px;padding:4px 6px;border-radius:6px;
  cursor:pointer;font-size:12px}
.erail .top li:hover{background:var(--ink)}
.erail .top .tn{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.erail .top .tc{color:#f0883e;font-weight:700;font-variant-numeric:tabular-nums}
.erail .top .ts{color:var(--dim);font-size:10.5px;font-variant-numeric:tabular-nums;min-width:52px;text-align:right}
.erail .rkey{border-top:1px solid var(--line);padding-top:10px;font-size:11px;color:var(--dim);line-height:1.5}
.erail .rkey b{color:var(--text);font-weight:600}
.erail .rempty{color:var(--dim);font-size:12px;line-height:1.5}
</style>
</head>
<body>
<header>
  <span class="brand">Azzio</span>
  <div class="tabs">
    <button class="tab active" id="tabComponents" title="The layered dependency map of every component (press c)">Components</button>
    <button class="tab" id="tabEntries" title="What each packages.x86_64 entry pulls into the system (press e)">Entries</button>
  </div>
  <span class="sub" id="pageSub">interactive component map</span>
  <div class="glance" id="glance"></div>
</header>
<div class="controls pagectl" id="ctlComponents">
  <input type="search" id="q" placeholder="Search components&hellip;  (press /)" autocomplete="off"/>
  <label>Jump to layer</label><select id="jump" title="Scroll to a layer (press 1-7; 1 = top)"></select>
  <label>Category</label><select id="fcat"></select>
  <label>Edition</label><select id="fed">
    <option value="">all</option>
    <option value="azzio">Azzio Component</option>
    <option value="stock">Stock Arch</option>
  </select>
  <label>Sort</label><select id="sort" title="How boxes are ordered within each layer">
    <option value="name" selected>Name (A-Z)</option>
    <option value="load">Most load-bearing</option>
    <option value="size">Installed size</option>
    <option value="deps">Most dependencies</option>
  </select>
  <button class="btn" id="collapse" title="Collapse / expand all layers (press z)">Collapse all</button>
  <button class="btn" id="reset">Reset</button>
  <span class="count" id="count"></span>
</div>
<div class="controls pagectl hidden" id="ctlEntries">
  <input type="search" id="qe" placeholder="Search manifest entries&hellip;  (press /)" autocomplete="off"/>
  <label>View</label><select id="eview" title="How the manifest entries are laid out">
    <option value="flat" selected>Manifest order (flat)</option>
    <option value="hier">Dependency hierarchy</option>
  </select>
  <label>Block</label><select id="eblock" title="Stock archiso baseline vs Azzio additions">
    <option value="">all</option>
    <option value="azzio">Azzio additions</option>
    <option value="stock">Stock Arch baseline</option>
  </select>
  <label>Sort</label><select id="esort" title="How entries are ordered">
    <option value="manifest" selected>Manifest order</option>
    <option value="excl">Most exclusive pulls</option>
    <option value="brings">Most brought in</option>
    <option value="exclsize">Exclusive size</option>
    <option value="name">Name (A-Z)</option>
  </select>
  <button class="btn" id="ereset">Reset</button>
  <span class="count" id="ecount"></span>
</div>
<div class="main">
  <div class="map page" id="map"></div>
  <div class="map page hidden" id="emap"></div>
  <aside class="erail hidden" id="erail"></aside>
  <aside class="panel hidden" id="panel"></aside>
</div>
<button class="legend-toggle down" id="legendToggle" title="Show / hide the legend (press l)">Show legend ▴</button>
<div class="legend hidden" id="legend"></div>

<script id="data" type="application/json">/*__DATA__*/</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById('data').textContent);
const C = DATA.components, ORDER = DATA.order, CATCOLORS = DATA.catColors;
const EDITION_MARK = {"azzio":"★","stock":""};
const map = document.getElementById('map');
const panel = document.getElementById('panel');
let selected = null;
let filterCat = "", filterEd = "", query = "";

// ---- glance strip ----
(function(){
  const g = DATA.glance, el = document.getElementById('glance');
  const items = [
    ["Kernel", "linux "+g.kernel],
    ["Init", "systemd "+g.init],
    ["Components", g.closure],
    ["Azzio / stock", g.azzio+" / "+g.stock],
    ["Deepest chain", g.maxHeight+" hops"],
    ["Installed size", g.size],
  ];
  // Each fact is ONE inline <span> so it never splits across lines: the glance
  // strip is a flex row, and without wrapping each fact the label and its value
  // would become separate flex items and wrap independently.
  el.innerHTML = items.map(([k,v])=>
    `<span class="fact">${esc(k)}: <b>${esc(v)}</b></span>`).join("");
})();

// ---- category filter options ----
(function(){
  const sel = document.getElementById('fcat');
  sel.innerHTML = '<option value="">all</option>' +
    DATA.categories.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join("");
})();

// Display layer number: 1 = the TOP layer (leaves), counting down to the bottom.
// Internally layers are 0 (sinks/foundation) .. N-1 (leaves); invert for display.
const NLAYERS = DATA.layers.length;
function dispNum(i){ return NLAYERS - i; }        // internal idx -> shown number
function idxFromDisp(n){ return NLAYERS - n; }    // shown number -> internal idx

// ---- layer jump options (top layer first, matching the on-screen order) ----
(function(){
  const sel = document.getElementById('jump');
  const opts = ['<option value="">go to&hellip;</option>'];
  for(let i=DATA.layers.length-1;i>=0;i--){
    opts.push(`<option value="${i}">${dispNum(i)} &middot; ${esc(DATA.layers[i].title)}</option>`);
  }
  sel.innerHTML = opts.join("");
})();

// ---- legend ----
(function(){
  const el = document.getElementById('legend');
  const cats = DATA.categories.map(c=>
    `<span><span class="sw" style="background:${CATCOLORS[c]}66;border:1px solid ${CATCOLORS[c]}"></span>${esc(c)}</span>`
  ).join("");
  el.innerHTML =
    `<div class="grp how">Foundation / sinks (bottom) &#8594; leaf apps (top) `+
    `&#183; click any component to inspect it `+
    `&#183; <b style="color:var(--text)">Edition:</b>`+
    `<span><span style="color:var(--cyan)">★</span> Azzio Component</span>`+
    `<span style="opacity:.7">(no mark) Stock Arch</span></div>`+
    `<div class="cats">${cats}</div>`+
    `<div class="grp"><span style="color:#eab308">■ requires</span>`+
    `<span style="color:#22c55e">■ required by</span></div>`+
    `<div class="grp" style="margin-left:auto">Keys: `+
    `<kbd>c</kbd>/<kbd>e</kbd> components / entries `+
    `<kbd>/</kbd> search <kbd>1</kbd>&ndash;<kbd>7</kbd> jump to layer `+
    `<kbd>z</kbd> collapse all <kbd>l</kbd> legend <kbd>Esc</kbd> close</div>`;
})();

function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}

// within-layer ordering strategies for the Sort control
const SORTS = {
  load: (a,b)=> C[b].transDependents-C[a].transDependents || (a<b?-1:1),
  name: (a,b)=> a<b?-1:(a>b?1:0),
  size: (a,b)=> C[b].isize-C[a].isize || (a<b?-1:1),
  deps: (a,b)=> C[b].requires.length-C[a].requires.length || (a<b?-1:1),
};
let sortMode = "name";

// ---- build the bands + boxes once ----
function buildMap(){
  map.innerHTML = "";
  // group components by layer
  const byLayer = DATA.layers.map(()=>[]);
  for(const name of ORDER){ byLayer[C[name].layer].push(name); }
  for(const arr of byLayer){ arr.sort(SORTS[sortMode]); }
  // draw top layer first (6 -> 0)
  for(let i=DATA.layers.length-1;i>=0;i--){
    const L = DATA.layers[i], names = byLayer[i];
    const band = document.createElement('div'); band.className='band'; band.dataset.layer=i;
    band.id = 'layer-'+i;
    const rail = document.createElement('div'); rail.className='rail';
    rail.innerHTML = `<div class="t" title="click to collapse / expand this layer">${esc(L.title)}</div>`+
      `<div class="s">${esc(L.subtitle)}</div>`+
      `<div class="n" data-n>${names.length} pkgs</div>`+
      (i>0?`<div class="arrow">depends on layer(s) below ↓</div>`:``);
    rail.querySelector('.t').addEventListener('click',()=>band.classList.toggle('collapsed'));
    const boxes = document.createElement('div'); boxes.className='boxes';
    for(const name of names){ boxes.appendChild(makeBox(name)); }
    band.appendChild(rail); band.appendChild(boxes);
    map.appendChild(band);
  }
}

// re-sort boxes in place without losing the current filter/selection state
function resort(){
  const byLayer = DATA.layers.map(()=>[]);
  for(const name of ORDER){ byLayer[C[name].layer].push(name); }
  for(let i=0;i<DATA.layers.length;i++){
    byLayer[i].sort(SORTS[sortMode]);
    const band = document.getElementById('layer-'+i);
    if(!band) continue;
    const boxes = band.querySelector('.boxes');
    for(const name of byLayer[i]){
      const el = boxes.querySelector(`.box[data-name="${cssEsc(name)}"]`);
      if(el) boxes.appendChild(el); // appendChild moves it to the new position
    }
  }
  applyFilters();
  if(selected) highlight(selected);
}

// scroll a given layer into view (used by the Jump control + number keys)
function jumpToLayer(i){
  const band = document.getElementById('layer-'+i);
  if(band){ band.classList.remove('collapsed');
    if(band.scrollIntoView) band.scrollIntoView({behavior:'smooth',block:'start'}); }
}

function makeBox(name){
  const c = C[name];
  const color = CATCOLORS[c.category] || "#8892a0";
  const b = document.createElement('div');
  b.className='box'; b.dataset.name=name; b.style.setProperty('--bc',color);
  const mark = EDITION_MARK[c.edition];
  const markColor = c.edition==="azzio" ? "var(--cyan)" : "var(--text)";
  b.innerHTML = `<div class="bn">${esc(name)}</div>`+
    `<div class="bv">${esc(c.version.split('-')[0])}</div>`+
    (mark?`<div class="mark" style="color:${markColor}">${mark}</div>`:``);
  b.addEventListener('click',()=>select(name));
  return b;
}

// ---- filtering ----
function matches(name){
  const c = C[name];
  if(filterCat && c.category!==filterCat) return false;
  if(filterEd && c.edition!==filterEd) return false;
  if(query && !name.toLowerCase().includes(query)) return false;
  return true;
}
function applyFilters(){
  let shown=0;
  for(const b of map.querySelectorAll('.box')){
    const ok = matches(b.dataset.name);
    b.classList.toggle('hidden', !ok);
    if(ok) shown++;
  }
  // per-band counts + hide empty bands
  for(const band of map.querySelectorAll('.band')){
    const vis = band.querySelectorAll('.box:not(.hidden)').length;
    band.classList.toggle('empty', vis===0);
    const n = band.querySelector('[data-n]');
    const total = band.querySelectorAll('.box').length;
    n.textContent = (vis===total? `${total} pkgs` : `${vis} / ${total} pkgs`);
  }
  document.getElementById('count').textContent = `${shown} / ${ORDER.length} shown`;
  if(selected) highlight(selected); // keep highlight consistent
}

// ---- selection + dependency highlight ----
// reveal=false (a direct click on a box): keep the map exactly where it is --
// just dim the others and open the side panel. reveal=true (following a pill in
// the panel): scroll the newly-selected box into view since it may be off-screen.
function select(name, reveal){
  selected = name;
  if(reveal) ensureBoxVisible(name);   // clear filters / expand band BEFORE painting
  openPanel(name);
  highlight(name);
  if(reveal){
    const el = map.querySelector(`.box[data-name="${cssEsc(name)}"]`);
    if(el && el.scrollIntoView) el.scrollIntoView({block:'nearest',behavior:'smooth'});
  }
}
// A jump target (a pill click) may be filtered out (.box.hidden) or inside a
// collapsed/empty band -- both display:none, so scrollIntoView would land on
// nothing. If the active filters hide it, drop them (and sync the controls); then
// expand its band so the selection is actually visible.
function ensureBoxVisible(name){
  if(!matches(name)){
    filterCat=filterEd=query="";
    q.value=""; document.getElementById('fcat').value=""; document.getElementById('fed').value="";
    applyFilters();
  }
  const el = map.querySelector(`.box[data-name="${cssEsc(name)}"]`);
  const band = el && el.closest ? el.closest('.band') : null;
  if(band) band.classList.remove('collapsed');
}
function cssEsc(s){ return (window.CSS && CSS.escape)? CSS.escape(s) : s.replace(/["\\]/g,'\\$&'); }

function highlight(name){
  const c = C[name];
  const req = new Set(c.requires), reqby = new Set(c.requiredBy);
  for(const b of map.querySelectorAll('.box')){
    const n = b.dataset.name;
    b.classList.remove('selected','req','reqby','dim');
    if(n===name){ b.classList.add('selected'); }
    else if(req.has(n)){ b.classList.add('req'); }
    else if(reqby.has(n)){ b.classList.add('reqby'); }
    else { b.classList.add('dim'); }
  }
}
function clearHighlight(){
  selected=null;
  for(const b of map.querySelectorAll('.box'))
    b.classList.remove('selected','req','reqby','dim');
  clearEntryHighlight();
}

// ---- detail panel ----
function openPanel(name){
  const c = C[name];
  const edClass = c.edition==="azzio"?"ed-az":"ed-sel";
  const layer = DATA.layers[c.layer];
  const reqPills = pills(c.requires, "req");
  const reqbyPills = pills(c.requiredBy, "reqby");
  const opt = c.optdepends.length? `<div class="dl"><div class="h">Optional dependencies (${c.optdepends.length})</div>`+
      `<div class="opt">`+c.optdepends.map(o=>{
        const i=o.indexOf(':'); return i>0? `<div><b>${esc(o.slice(0,i))}</b>${esc(o.slice(i))}</div>`:`<div>${esc(o)}</div>`;
      }).join("")+`</div></div>` : "";
  panel.classList.remove('hidden');
  panel.innerHTML =
    `<div class="ph"><span class="close" title="close (Esc)">×</span>`+
      `<h2>${esc(name)}</h2><div class="ver">${esc(c.version)} &#183; ${esc(c.repo)} &#183; ${esc(c.size)}</div></div>`+
    `<div class="body">`+
      `<p class="purpose">${esc(c.desc)}</p>`+
      `<div class="chips">`+
        `<span class="chip cat" style="border-color:${CATCOLORS[c.category]};color:${CATCOLORS[c.category]}">${esc(c.category)}</span>`+
        `<span class="chip ${edClass}">${EDITION_MARK[c.edition]||''} ${esc(c.editionLabel)}</span>`+
        `<span class="chip">Layer ${dispNum(c.layer)}: ${esc(layer.title)}</span>`+
        (c.removed?`<span class="chip" style="border-color:#f85149;color:#f85149">removed from ISO</span>`:``)+
      `</div>`+
      (c.azzio? `<div class="azbox"><div class="h">What Azzio changes</div>${esc(c.azzio)}</div>`:``)+
      `<div class="kv">`+
        `<span class="k">Upstream</span><span>${c.url?`<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.url)}</a>`:'&mdash;'}</span>`+
        `<span class="k">License</span><span>${esc(c.license||'—')}</span>`+
        `<span class="k">Pulls in below</span><span>${c.transDeps} packages (transitively)</span>`+
        `<span class="k">Relied on by above</span><span>${c.transDependents} packages (transitively)</span>`+
      `</div>`+
      broughtBySection(c)+
      `<div class="dl"><div class="h req">Requires ↓ (${c.requires.length}) &mdash; direct dependencies</div>${reqPills}</div>`+
      `<div class="dl"><div class="h reqby">Required by ↑ (${c.requiredBy.length}) &mdash; things that need it</div>${reqbyPills}</div>`+
      opt+
    `</div>`;
  panel.querySelector('.close').addEventListener('click',closePanel);
  for(const p of panel.querySelectorAll('.pill[data-go]'))
    p.addEventListener('click',()=>select(p.dataset.go, true));
  for(const p of panel.querySelectorAll('.pill[data-goentry]'))
    p.addEventListener('click',()=>{ showEntries(); selectEntry(p.dataset.goentry, true); });
}

// "Brought into the set by": the manifest ent(y/ies) responsible for this package
// being present. Exactly one -> exclusive (cut that entry and this leaves too);
// several -> shared. A manifest root is its own entry. Clicking a pill jumps to
// that entry on the Entries page.
function broughtBySection(c){
  const bb = c.broughtBy || [];
  const excl = c.exclusiveTo || "";
  const pill = t => `<span class="pill${t===excl?' ex':''}" data-goentry="${esc(t)}" title="${t===excl?'this package is exclusive to this entry':'one of the entries that pull this package in'}">${esc(t)}</span>`;
  let lead;
  if(bb.length===0){
    lead = `Not reachable from any manifest entry (should not happen).`;
  } else if(excl){
    lead = `<b>Exclusive</b> to a single entry &mdash; remove <b>${esc(excl)}</b> from packages.x86_64 and this package leaves the set.`;
  } else {
    lead = `Pulled in by <b>${bb.length}</b> manifest entries &mdash; it stays as long as <b>any one</b> of them is present.`;
  }
  return `<div class="dl"><div class="h" style="color:#f0883e">Brought into the set by (${bb.length})</div>`+
    `<div class="brk"><div class="lead" style="margin-bottom:7px">${lead}</div>`+
    `<div class="pill-wrap">${bb.map(pill).join("")}</div></div></div>`;
}
function pills(names, kind){
  if(!names.length) return `<div class="pill-wrap"><span class="pill none">none &mdash; ${kind==='req'?'a base / sink package':'a top / leaf package'}</span></div>`;
  return `<div class="pill-wrap">`+names.map(n=>`<span class="pill" data-go="${esc(n)}">${esc(n)}</span>`).join("")+`</div>`;
}
function closePanel(){ panel.classList.add('hidden'); panel.innerHTML=""; clearHighlight(); }

// ---- controls wiring ----
const q = document.getElementById('q');
const jumpSel = document.getElementById('jump');
const sortSel = document.getElementById('sort');
const collapseBtn = document.getElementById('collapse');
const legendEl = document.getElementById('legend');
const legendBtn = document.getElementById('legendToggle');
let allCollapsed = false;

q.addEventListener('input',()=>{ query=q.value.trim().toLowerCase(); applyFilters(); });
document.getElementById('fcat').addEventListener('change',e=>{ filterCat=e.target.value; applyFilters(); });
document.getElementById('fed').addEventListener('change',e=>{ filterEd=e.target.value; applyFilters(); });
jumpSel.addEventListener('change',e=>{ if(e.target.value!=="") jumpToLayer(+e.target.value); e.target.value=""; });
sortSel.addEventListener('change',e=>{ sortMode=e.target.value; resort(); });

function setCollapsed(state){
  allCollapsed = state;
  for(const band of map.querySelectorAll('.band')) band.classList.toggle('collapsed', state);
  collapseBtn.textContent = state ? 'Expand all' : 'Collapse all';
}
collapseBtn.addEventListener('click',()=>setCollapsed(!allCollapsed));

function setLegend(show){
  legendEl.classList.toggle('hidden', !show);
  legendBtn.textContent = show ? 'Hide legend ▾' : 'Show legend ▴';
  // Park the button ABOVE the legend when it's shown (so it never covers the
  // legend text), or drop it to the bottom-right corner when the legend is hidden.
  legendBtn.classList.toggle('down', !show);
  if(show){
    // pin the button just above the actual rendered legend height
    document.documentElement.style.setProperty('--legend-h', legendEl.offsetHeight + 'px');
  }
}
legendBtn.addEventListener('click',()=>setLegend(legendEl.classList.contains('hidden')));

document.getElementById('reset').addEventListener('click',()=>{
  filterCat=filterEd=query=""; q.value="";
  document.getElementById('fcat').value=""; document.getElementById('fed').value="";
  sortMode="name"; sortSel.value="name"; resort();
  setCollapsed(false);
  applyFilters(); closePanel();
});

/* ======================================================================== */
/* SECOND PAGE -- Entries: what each packages.x86_64 line pulls into the set. */
/* ======================================================================== */
const ENTRIES = DATA.entries;                       // manifest order
const EBY = {};                                     // token -> entry record
for(const e of ENTRIES) EBY[e.token] = e;
const emap = document.getElementById('emap');
const erail = document.getElementById('erail');
const qe = document.getElementById('qe');
let entrySelected = null;
let eView = "flat";       // "flat" (manifest order) | "hier" (dependency layers)
let eBlock = "";          // "" | "azzio" | "stock"
let eSort = "manifest";
let eQuery = "";

const ESORTS = {
  manifest:(a,b)=> EBY[a].__i - EBY[b].__i,
  excl:    (a,b)=> EBY[b].exclusiveCount-EBY[a].exclusiveCount || (a<b?-1:1),
  brings:  (a,b)=> EBY[b].bringsCount-EBY[a].bringsCount || (a<b?-1:1),
  exclsize:(a,b)=> EBY[b].exclIsize-EBY[a].exclIsize || (a<b?-1:1),
  name:    (a,b)=> a<b?-1:(a>b?1:0),
};
ENTRIES.forEach((e,i)=>{ e.__i=i; });               // stable manifest index

function makeEnt(tok){
  const e = EBY[tok];
  const color = CATCOLORS[e.category] || "#8892a0";
  const b = document.createElement('div');
  b.className='ent'; b.dataset.token=tok; b.style.setProperty('--bc',color);
  const mark = EDITION_MARK[e.edition];
  const markColor = e.edition==="azzio" ? "var(--cyan)" : "var(--text)";
  const grp = e.kind==="group" ? `<span class="grp-badge" title="an Arch package group; expands to ${e.roots.length} members">group</span>` : "";
  b.innerHTML =
    `<div class="en">${esc(tok)}${grp}</div>`+
    `<div class="em"><span>brings <b>${e.bringsCount}</b></span>`+
      `<span class="exl">${e.exclusiveCount} exclusive</span>`+
      `<span>${e.exclSize}</span></div>`+
    (mark?`<div class="mark" style="color:${markColor}">${mark}</div>`:``);
  b.addEventListener('click',()=>selectEntry(tok));
  return b;
}

// ---- build the entries view (rebuilt on view/sort change) ----
function buildEntries(){
  emap.innerHTML="";
  const toks = ENTRIES.map(e=>e.token).slice().sort(ESORTS[eSort]);
  if(eView==="flat"){
    // Two manifest sections: Stock baseline, then Azzio additions. Within each,
    // honour the chosen sort (default = manifest order).
    const secs = [
      ["stock","Stock Arch baseline","archiso releng packages Azzio inherits"],
      ["azzio","Azzio additions","lines Azzio adds on top of the baseline"],
    ];
    for(const [ed,title,sub] of secs){
      const list = toks.filter(t=>EBY[t].edition===ed);
      if(!list.length) continue;
      const sec = document.createElement('div'); sec.className='flatsec'; sec.dataset.block=ed;
      const head = document.createElement('div'); head.className='flathead';
      head.innerHTML = `<span class="ft">${esc(title)}</span><span class="fs">${esc(sub)}</span>`+
        `<span class="fn" data-en>${list.length} entries</span>`;
      const bx = document.createElement('div'); bx.className='flatboxes';
      for(const t of list) bx.appendChild(makeEnt(t));
      sec.appendChild(head); sec.appendChild(bx); emap.appendChild(sec);
    }
  } else {
    // Hierarchy: reuse the component layer bands, placing each entry in the band
    // of its anchor package (deepest root for a group). Top layer first.
    const byLayer = DATA.layers.map(()=>[]);
    for(const t of toks) byLayer[EBY[t].layer].push(t);
    for(let i=DATA.layers.length-1;i>=0;i--){
      const L=DATA.layers[i], names=byLayer[i];
      const band=document.createElement('div'); band.className='band'; band.dataset.elayer=i;
      const rail=document.createElement('div'); rail.className='rail';
      rail.innerHTML=`<div class="t" title="click to collapse / expand">${esc(L.title)}</div>`+
        `<div class="s">${esc(L.subtitle)}</div>`+
        `<div class="n" data-en>${names.length} entries</div>`+
        (i>0?`<div class="arrow">anchored deeper than layer(s) below ↓</div>`:``);
      rail.querySelector('.t').addEventListener('click',()=>band.classList.toggle('collapsed'));
      const boxes=document.createElement('div'); boxes.className='boxes';
      for(const t of names) boxes.appendChild(makeEnt(t));
      band.appendChild(rail); band.appendChild(boxes); emap.appendChild(band);
    }
  }
  applyEntryFilters();
  if(entrySelected) highlightEntry(entrySelected);
}

function entMatches(tok){
  const e=EBY[tok];
  if(eBlock && e.edition!==eBlock) return false;
  if(eQuery && !tok.toLowerCase().includes(eQuery)) return false;
  return true;
}
function applyEntryFilters(){
  let shown=0;
  for(const b of emap.querySelectorAll('.ent')){
    const ok=entMatches(b.dataset.token);
    b.classList.toggle('hidden',!ok);
    if(ok) shown++;
  }
  // per-section / per-band counts + hide empties
  for(const sec of emap.querySelectorAll('.flatsec')){
    const vis=sec.querySelectorAll('.ent:not(.hidden)').length;
    sec.style.display = vis? "" : "none";
    const n=sec.querySelector('[data-en]'); if(n) n.textContent=`${vis} entries`;
  }
  for(const band of emap.querySelectorAll('.band[data-elayer]')){
    const vis=band.querySelectorAll('.ent:not(.hidden)').length;
    band.classList.toggle('empty',vis===0);
    const n=band.querySelector('[data-en]');
    const total=band.querySelectorAll('.ent').length;
    if(n) n.textContent = vis===total? `${total} entries` : `${vis} / ${total} entries`;
  }
  document.getElementById('ecount').textContent=`${shown} / ${ENTRIES.length} entries`;
  buildERail();   // keep the overview rail in sync with what is currently visible
}

// ---- entries overview rail ----------------------------------------------- */
// Fills the strip .flatboxes reserves on the right. Summarises the entries that
// are CURRENTLY visible (after the block filter + search), so it always reflects
// what is on screen, and lists the heaviest manifest lines as shortcuts.
function fmtSize(n){
  if(n>=1024**3) return (n/1024**3).toFixed(2)+" GiB";
  if(n>=1024**2) return (n/1024**2).toFixed(1)+" MiB";
  if(n>=1024)    return Math.round(n/1024)+" KiB";
  return n+" B";
}
function buildERail(){
  // The reserved right strip only exists in flat view; the hierarchy view uses
  // full-width bands with their own rails, so hide the overview there.
  if(currentPage!=="entries" || eView!=="flat"){ erail.classList.add('hidden'); return; }
  erail.classList.remove('hidden');
  const vis = ENTRIES.filter(e=>entMatches(e.token));
  if(!vis.length){
    erail.innerHTML =
      `<h3>Entries overview</h3>`+
      `<div class="rempty">No entries match the current block filter / search.</div>`;
    return;
  }
  const az = vis.filter(e=>e.edition==="azzio").length;
  const stock = vis.length - az;
  const brings = vis.reduce((s,e)=>s+e.bringsCount,0);
  const exclPkgs = vis.reduce((s,e)=>s+e.exclusiveCount,0);
  const exclBytes = vis.reduce((s,e)=>s+e.exclIsize,0);
  // heaviest lines: those whose removal drops the most packages exclusively.
  const top = vis.filter(e=>e.exclusiveCount>0)
                 .sort((a,b)=> b.exclusiveCount-a.exclusiveCount || b.exclIsize-a.exclIsize)
                 .slice(0,10);
  const scope = eBlock==="azzio" ? "Azzio additions"
              : eBlock==="stock"   ? "Stock baseline"
              : eQuery             ? "matching entries"
              : "all manifest entries";
  const topRows = top.length
    ? top.map(e=>
        `<li data-goent="${esc(e.token)}" title="click to inspect ${esc(e.token)}">`+
          `<span class="tn">${esc(e.token)}</span>`+
          `<span class="tc">${e.exclusiveCount}</span>`+
          `<span class="ts">${esc(e.exclSize)}</span></li>`).join("")
    : `<li class="rempty" style="cursor:default">None of the visible entries pull anything exclusively.</li>`;
  erail.innerHTML =
    `<h3>Entries overview</h3>`+
    `<div class="rsub">${scope} &mdash; what each line in packages.x86_64 pulls into the system.</div>`+
    `<div class="stats">`+
      `<div class="stat"><div class="v">${vis.length}</div><div class="l">entries shown</div></div>`+
      `<div class="stat"><div class="v az">${az}</div><div class="l">Azzio &middot; ${stock} stock</div></div>`+
      `<div class="stat"><div class="v">${brings}</div><div class="l">package pulls (with overlap)</div></div>`+
      `<div class="stat"><div class="v exl">${fmtSize(exclBytes)}</div><div class="l">${exclPkgs} exclusive pkgs</div></div>`+
    `</div>`+
    `<div class="rh">Most load-bearing lines</div>`+
    `<ul class="top">${topRows}</ul>`+
    `<div class="rkey">The <b style="color:#f0883e">orange</b> number is how many packages are <b>exclusive</b> to that `+
      `line &mdash; drop it from packages.x86_64 and they leave the set. Click any line to inspect it.</div>`;
  for(const li of erail.querySelectorAll('.top li[data-goent]'))
    li.addEventListener('click',()=>selectEntry(li.dataset.goent, true));
}

// ---- entry selection + highlight ----
function selectEntry(tok, reveal){
  if(!EBY[tok]) return;
  entrySelected=tok;
  if(reveal) ensureEntVisible(tok);    // clear filters / expand band BEFORE painting
  openEntryPanel(tok);
  highlightEntry(tok);
  if(reveal){
    const el=emap.querySelector(`.ent[data-token="${cssEsc(tok)}"]`);
    if(el && el.scrollIntoView) el.scrollIntoView({block:'nearest',behavior:'smooth'});
  }
}
// Mirror of ensureBoxVisible for the Entries page: an entry reached from a
// component's "Brought into the set by" pill may be filtered out by the block
// filter / search, or (hierarchy view) inside a collapsed band.
function ensureEntVisible(tok){
  if(!entMatches(tok)){
    eBlock=eQuery="";
    qe.value=""; document.getElementById('eblock').value="";
    applyEntryFilters();
  }
  const el = emap.querySelector(`.ent[data-token="${cssEsc(tok)}"]`);
  const band = el && el.closest ? el.closest('.band') : null;
  if(band) band.classList.remove('collapsed');
}
function highlightEntry(tok){
  // dim every entry except the selected one (entries don't depend on each other,
  // so there's no requires/required-by graph to paint -- the panel carries that).
  for(const b of emap.querySelectorAll('.ent')){
    b.classList.remove('selected','dim');
    b.classList.toggle('selected', b.dataset.token===tok);
    if(b.dataset.token!==tok) b.classList.add('dim');
  }
}
function clearEntryHighlight(){
  entrySelected=null;
  for(const b of emap.querySelectorAll('.ent')) b.classList.remove('selected','dim','brings');
}

// ---- entry detail panel: exclusive vs shared breakdown ----
function openEntryPanel(tok){
  const e=EBY[tok];
  const edClass = e.edition==="azzio"?"ed-az":"ed-sel";
  const rootLine = e.kind==="group"
    ? `group of ${e.roots.length}: ${e.roots.slice(0,6).map(esc).join(", ")}${e.roots.length>6?" &hellip;":""}`
    : (e.resolved && e.resolved!==tok ? `resolves to ${esc(e.resolved)}` : `package`);
  const exclPills = e.exclusive.length
    ? `<div class="pill-wrap">`+e.exclusive.map(n=>`<span class="pill ex" data-go="${esc(n)}">${esc(n)}</span>`).join("")+`</div>`
    : `<div class="pill-wrap"><span class="pill none">none &mdash; everything it brings is also brought by another entry</span></div>`;
  const sharedPills = e.shared.length
    ? `<div class="pill-wrap">`+e.shared.map(n=>`<span class="pill" data-go="${esc(n)}">${esc(n)}</span>`).join("")+`</div>`
    : `<div class="pill-wrap"><span class="pill none">none</span></div>`;
  const exclLead = e.exclusiveCount
    ? `Removing <b>${esc(tok)}</b> from packages.x86_64 would drop these <b>${e.exclusiveCount}</b> package(s) (${e.exclSize}) from the set &mdash; nothing else pulls them in.`
    : `Removing <b>${esc(tok)}</b> would drop nothing new: every package it brings is also brought by another entry.`;
  panel.classList.remove('hidden');
  // No layout reservation needed on open: .flatboxes permanently reserves the
  // panel's width on the right, so the panel overlays empty space, not boxes.
  panel.innerHTML =
    `<div class="ph"><span class="close" title="close (Esc)">×</span>`+
      `<h2>${esc(tok)}</h2><div class="ver">manifest entry &#183; ${rootLine}</div></div>`+
    `<div class="body">`+
      `<div class="chips">`+
        `<span class="chip cat" style="border-color:${CATCOLORS[e.category]};color:${CATCOLORS[e.category]}">${esc(e.category)}</span>`+
        `<span class="chip ${edClass}">${EDITION_MARK[e.edition]||''} ${esc(e.editionLabel)}</span>`+
        `<span class="chip">${e.kind==="group"?"group":"package"}</span>`+
      `</div>`+
      `<div class="entkv">`+
        `<span class="k">Brings into the set</span><span><b>${e.bringsCount}</b> packages &#183; ${e.bringsSize} installed</span>`+
        `<span class="k">Exclusive to this entry</span><span><b style="color:#f0883e">${e.exclusiveCount}</b> packages &#183; ${e.exclSize}</span>`+
        `<span class="k">Shared with others</span><span>${e.sharedCount} packages</span>`+
      `</div>`+
      `<div class="brk"><div class="h excl">Exclusive &mdash; lost if removed (${e.exclusiveCount})</div>`+
        `<div class="lead">${exclLead}</div>${exclPills}</div>`+
      `<div class="brk"><div class="h shar">Shared &mdash; also brought by other entries (${e.sharedCount})</div>`+
        `<div class="lead">These stay even if <b>${esc(tok)}</b> is removed, because another entry also reaches them.</div>${sharedPills}</div>`+
    `</div>`;
  panel.querySelector('.close').addEventListener('click',closeEntryPanel);
  // clicking a brought-in package jumps to it on the Components page
  for(const p of panel.querySelectorAll('.pill[data-go]'))
    p.addEventListener('click',()=>{ showComponents(); select(p.dataset.go, true); });
  // the entry's own box is marked by highlightEntry's cyan '.selected' outline --
  // no separate '.brings' anchor (it would clash with the legend's yellow
  // "requires" colour and linger on the previously-selected box).
}
function closeEntryPanel(){ panel.classList.add('hidden'); panel.innerHTML=""; clearEntryHighlight(); }

// ---- entries controls ----
qe.addEventListener('input',()=>{ eQuery=qe.value.trim().toLowerCase(); applyEntryFilters(); });
document.getElementById('eview').addEventListener('change',e=>{ eView=e.target.value; buildEntries(); });
document.getElementById('eblock').addEventListener('change',e=>{ eBlock=e.target.value; applyEntryFilters(); });
document.getElementById('esort').addEventListener('change',e=>{ eSort=e.target.value; buildEntries(); });
document.getElementById('ereset').addEventListener('click',()=>{
  eQuery=""; qe.value=""; eBlock=""; document.getElementById('eblock').value="";
  eSort="manifest"; document.getElementById('esort').value="manifest";
  eView="flat"; document.getElementById('eview').value="flat";
  buildEntries(); closeEntryPanel();
});

/* ======================================================================== */
/* PAGE SWITCHING (Components <-> Entries)                                   */
/* ======================================================================== */
let currentPage = "components";
const tabC = document.getElementById('tabComponents');
const tabE = document.getElementById('tabEntries');
function showComponents(){
  if(currentPage==="components") return;
  currentPage="components";
  tabC.classList.add('active'); tabE.classList.remove('active');
  document.getElementById('map').classList.remove('hidden');
  document.getElementById('emap').classList.add('hidden');
  document.getElementById('ctlComponents').classList.remove('hidden');
  document.getElementById('ctlEntries').classList.add('hidden');
  erail.classList.add('hidden');     // overview rail belongs to the entries page
  closeEntryPanel();                 // leaving entries clears its selection/panel
}
function showEntries(){
  if(currentPage==="entries") return;
  currentPage="entries";
  tabE.classList.add('active'); tabC.classList.remove('active');
  document.getElementById('emap').classList.remove('hidden');
  document.getElementById('map').classList.add('hidden');
  document.getElementById('ctlEntries').classList.remove('hidden');
  document.getElementById('ctlComponents').classList.add('hidden');
  closePanel();                      // leaving components clears its selection/panel
  // buildERail() (via buildEntries/applyEntryFilters, or directly) shows the rail
  // and fills the reserved right strip; it self-gates on page + flat view.
  if(!emap.childElementCount) buildEntries();   // lazy first build (also builds the rail)
  else buildERail();                 // refresh rail stats on re-entry
}
tabC.addEventListener('click',showComponents);
tabE.addEventListener('click',showEntries);

document.addEventListener('keydown',e=>{
  // Let the browser handle any modified combo (Ctrl+C/Cmd+C to copy, Ctrl+E, etc.)
  // Bare-letter hotkeys below must never steal these.
  if(e.ctrlKey || e.metaKey || e.altKey) return;
  const typing = document.activeElement===q || document.activeElement===qe;
  if(e.key==='/' && !typing){ e.preventDefault(); (currentPage==="entries"?qe:q).focus(); }
  else if(e.key==='Escape'){
    if(!panel.classList.contains('hidden')){ currentPage==="entries"?closeEntryPanel():closePanel(); }
    else if(typing){ document.activeElement.blur(); }
  }
  else if(typing){ /* let the search field keep other keys */ }
  else if(e.key==='c'){ showComponents(); }
  else if(e.key==='e'){ showEntries(); }
  // 'l' (legend) is global; check it BEFORE the entries short-circuit so it works
  // on both pages. 'z' (collapse-all) only means anything on the Components map.
  else if(e.key==='l'){ setLegend(legendEl.classList.contains('hidden')); }
  else if(e.key==='z'){ if(currentPage!=="entries") setCollapsed(!allCollapsed); }
  else if(currentPage==="entries"){ /* entries page has no layer-jump (1-7) keys */ }
  else if(e.key>='1' && e.key<=String(NLAYERS)){ jumpToLayer(idxFromDisp(+e.key)); }
});

buildMap();
applyFilters();
setLegend(false);           // legend hidden by default; toggle sits bottom-right (press l / click to show)
window.addEventListener('resize',()=>{ if(!legendEl.classList.contains('hidden')) setLegend(true); });
</script>
</body>
</html>
"""
