"""
specification_svg -- render the Azzio dependency graph as a single self-contained SVG.

The SVG is the navigable, at-a-glance view the Markdown tables cannot be: seven
horizontal layers stacked from the kernel at the bottom to the leaf applications
at the top. Each layer is a labelled band; inside it sit the most load-bearing
packages of that layer as boxes, coloured by their human-language category and
marked with their edition -- a star marks an Azzio Component (in the set only
because Azzio added it), while Stock Arch packages (already on the stock
archiso releng medium) are left unmarked.

No external tooling (no Graphviz, no rsvg): we emit plain SVG text, so it renders
in any browser/IDE and diffs in git. The full 1300-package detail lives in the
Markdown appendix; here we deliberately draw a readable subset per layer plus the
per-layer counts, so the shape of the distribution is legible instead of a wall
of 1300 boxes.

Layer assignment is by real dependency depth (longest chain down to a sink), not
by category -- so a package's height in the image is its true position in the
dependency stack. Category only drives colour.
"""

import html

import specification_classify as K

# Azzio brand gradient (sampled from the fastfetch logo asset): cyan -> blue.
BRAND_CYAN = "#03a5fc"
BRAND_BLUE = "#0065f9"
INK = "#0d1117"
PANEL = "#161b22"
PANEL_LINE = "#30363d"
TEXT = "#e6edf3"
TEXT_DIM = "#9da7b3"

# The seven depth bands, bottom (0) to top (6): (title, subtitle).
#
# The band is DEPENDENCY DEPTH ONLY (the longest chain of dependencies below a
# package). Domain is a SEPARATE axis carried by colour (the package's category),
# never by the band -- every band is empirically a category mix (e.g. "Shared
# library" packages appear at every depth), so a domain title like "Core
# libraries" would mislabel the axis. The titles therefore name position in the
# dependency graph, and the subtitles give example packages as a hint. Bottom =
# nothing below it (a true sink); top = nothing depends on it (a leaf).
LAYER_DEFS = [
    ("Foundation",   "depth 0 -- nothing below them; true sinks of the graph"),
    ("Depth 1-3",    "rest on the foundation only; libc, filesystem, early libs"),
    ("Depth 4-8",    "a few layers deep; shared libraries and base tools"),
    ("Depth 9-14",   "mid stack; libraries and services with real chains below"),
    ("Depth 15-23",  "deep stack; kernel, coreutils, and heavier subsystems"),
    ("Depth 24-33",  "near the top; systemd, mesa, X, Qt, toolkits"),
    ("Leaves (34+)", "top; the deepest chains -- nothing depends on these"),
]

# Edition marker glyphs drawn on each box corner. Two editions only: an Azzio
# Component (in the set only because Azzio added it) gets a star; Stock Arch
# (already on the stock archiso releng medium) is unmarked.
EDITION_MARK = {
    "azzio": ("★", BRAND_CYAN),   # Azzio Component
    "stock":   ("",  None),          # Stock Arch (baseline archiso)
}


def layer_of(pkg, category, height):
    """Vertical band = dependency DEPTH only (the longest chain of dependencies
    below the package). There is NO category override: `category` is the colour
    axis, not the vertical one, and every band is a category mix, so `pkg` and
    `category` are unused here (kept in the signature for call-site stability).

    height 0 (nothing below it) is the most foundational and sits at the bottom;
    the deepest chains sit at the top. Because `height` is a true partial order
    (if A depends on B then height(B) < height(A)), this can never place a
    dependency above its dependent. Thresholds come from the real height
    histogram (max 44) to keep every band populated (roughly 5-22% each)."""
    if height == 0:
        return 0    # foundation: true sinks, nothing below them
    if height <= 3:
        return 1
    if height <= 8:
        return 2
    if height <= 14:
        return 3
    if height <= 23:
        return 4    # kernel (h=22) and coreutils (h=21) land here
    if height <= 33:
        return 5
    return 6         # leaves (h 34-44)


def _esc(s):
    return html.escape(str(s), quote=True)


# Approximate width of a string at a given font size for the SVG sans stack.
# 0.55 is a safe average glyph-width factor for mixed-case Inter/Segoe text; we
# use it to keep left-rail labels inside the rail so they never run onto the
# component boxes.
def _text_w(s, size, factor=0.55):
    return len(s) * size * factor


def _wrap(text, max_w, size, factor=0.55):
    """Greedy word-wrap `text` into lines that each fit within max_w px."""
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        trial = w if not cur else cur + " " + w
        if _text_w(trial, size, factor) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _representatives(pkgs, layer_idx, tiers, per_layer):
    """Pick which packages to actually draw in a layer.

    Top layer (apps): show the leaves themselves, by how much they pull in.
    Every other layer: show the most load-bearing (highest transitive dependents),
    because those are the packages the rest of the layer above rests on.
    """
    trans_dep = tiers["trans_dependents"]
    trans_deps = tiers["trans_deps"]
    indeg = tiers["indeg"]
    if layer_idx == 6:
        leaves = [p for p in pkgs if indeg.get(p, 0) == 0] or pkgs
        return sorted(leaves, key=lambda p: (-trans_deps[p], p))[:per_layer]
    return sorted(pkgs, key=lambda p: (-trans_dep[p], p))[:per_layer]


def render_svg(packages, resolved, tiers, tags, glance):
    """Return the SVG document text.

    packages : name -> record
    resolved : closure/roots/edges (from specification_resolve)
    tiers    : graph metrics (from specification_resolve.compute_tiers)
    tags     : name -> {edition, category, ...} (from specification_classify.classify)
    glance   : dict of at-a-glance facts (kernel, ram, closure size, ...)
    """
    closure = resolved["closure"]
    heights = tiers["heights"]

    # bucket packages into layers
    layers = [[] for _ in LAYER_DEFS]
    for p in closure:
        layers[layer_of(p, tags[p]["category"], heights[p])].append(p)

    # categories actually present, for the legend, in canonical order
    present_cats = [c for c in K.CATEGORY_ORDER
                    if any(tags[p]["category"] == c for p in closure)]

    # ---- geometry -------------------------------------------------------- #
    W = 1600
    margin = 32
    header_h = 210
    legend_h = 150
    band_gap = 14
    band_h = 150
    inner_w = W - 2 * margin
    # Left rail: holds each layer's title, subtitle and pkg count. Made wide
    # enough (and the labels wrapped to fit) so the text never overlaps the
    # component boxes, which begin at margin + RAIL_W.
    rail_w = 250
    rail_pad = 16            # inset of rail text from the band's left edge
    rail_text_w = rail_w - rail_pad - 12   # usable width for wrapped rail text
    per_layer = 30            # boxes drawn per band
    box_w, box_h, box_gap = 128, 40, 8
    boxes_x0 = margin + rail_w
    cols = max(1, (W - margin - boxes_x0) // (box_w + box_gap))

    total_h = header_h + len(LAYER_DEFS) * (band_h + band_gap) + legend_h + margin
    H = total_h

    s = []
    a = s.append
    # Opened as a standalone .svg document on GitHub Pages the <svg> IS the page
    # body. width:100%;height:auto makes it span the full browser width and take
    # its height from the viewBox ratio, so it fills the width and scrolls down
    # instead of being letterboxed with white side bars. A background rect below
    # covers the page behind it.
    a(f'<svg xmlns="http://www.w3.org/2000/svg" '
      f'viewBox="0 0 {W} {H}" '
      f'style="display:block;width:100%;height:auto;background:{INK}" '
      f'font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">')

    # defs: brand gradient
    a('<defs>')
    a(f'<linearGradient id="brand" x1="0" y1="0" x2="1" y2="0">'
      f'<stop offset="0" stop-color="{BRAND_CYAN}"/>'
      f'<stop offset="1" stop-color="{BRAND_BLUE}"/></linearGradient>')
    a('</defs>')

    a(f'<rect width="{W}" height="{H}" fill="{INK}"/>')

    # ---- header / at-a-glance ------------------------------------------- #
    a(f'<text x="{margin}" y="52" font-size="34" font-weight="700" '
      f'fill="url(#brand)">Azzio</text>')
    a(f'<text x="{margin+168}" y="52" font-size="26" font-weight="600" '
      f'fill="{TEXT}">Distribution dependency graph</text>')
    a(f'<text x="{margin}" y="78" font-size="13" fill="{TEXT_DIM}">'
      f'sinks (nothing below, bottom) to leaves (top) &#183; layer = dependency '
      f'depth &#183; colour = category &#183; mark = edition</text>')

    facts = [
        ("Base", glance["base"]),
        ("Desktop", glance["desktop"]),
        ("Kernel", f'linux {glance["kernel"]}'),
        ("Init", f'systemd {glance["init"]}'),
        ("ISO version scheme", glance["iso_version"]),
        ("Live-session RAM (cow_spacesize)", glance["ram"]),
        ("Packages (full closure)", str(glance["closure"])),
        ("Azzio Component / Stock Arch", f'{glance["azzio"]} / {glance["stock"]}'),
        ("Deepest chain (leaf -> base)", f'{glance["max_height"]} hops'),
        ("Installed size", glance["size"]),
    ]
    fx = margin
    fy = 108
    col_w = (inner_w) // 2
    for i, (k, v) in enumerate(facts):
        cx = fx + (i % 2) * col_w
        cy = fy + (i // 2) * 20
        a(f'<text x="{cx}" y="{cy}" font-size="13" fill="{TEXT_DIM}">{_esc(k)}:</text>')
        a(f'<text x="{cx+260}" y="{cy}" font-size="13" font-weight="600" '
          f'fill="{TEXT}">{_esc(v)}</text>')

    # ---- layer bands (top layer drawn first = at the top of the image) --- #
    y = header_h
    for idx in reversed(range(len(LAYER_DEFS))):
        title, subtitle = LAYER_DEFS[idx]
        band_pkgs = layers[idx]
        # band background
        a(f'<rect x="{margin}" y="{y}" width="{inner_w}" height="{band_h}" '
          f'rx="10" fill="{PANEL}" stroke="{PANEL_LINE}"/>')
        # vertical divider separating the left rail from the boxes area
        a(f'<line x1="{margin+rail_w-8}" y1="{y+10}" x2="{margin+rail_w-8}" '
          f'y2="{y+band_h-10}" stroke="{PANEL_LINE}"/>')
        # layer title on the left rail; shrink if it would exceed the rail
        rail_x = margin + rail_pad
        title_size = 18
        if _text_w(title, title_size) > rail_text_w:
            title_size = 15
        a(f'<text x="{rail_x}" y="{y+32}" font-size="{title_size}" '
          f'font-weight="700" fill="{TEXT}">{_esc(title)}</text>')
        # subtitle: word-wrapped so it stays inside the rail
        sy = y + 52
        for line in _wrap(subtitle, rail_text_w, 11)[:3]:
            a(f'<text x="{rail_x}" y="{sy}" font-size="11" '
              f'fill="{TEXT_DIM}">{_esc(line)}</text>')
            sy += 14
        a(f'<text x="{rail_x}" y="{y+band_h-30}" font-size="13" '
          f'font-weight="700" fill="url(#brand)">{len(band_pkgs)} pkgs</text>')
        # arrow hint (deps flow downward)
        if idx > 0:
            a(f'<text x="{rail_x}" y="{y+band_h-14}" font-size="10" '
              f'fill="{TEXT_DIM}">depends on layer(s) below ↓</text>')

        # boxes
        reps = _representatives(band_pkgs, idx, tiers, per_layer)
        bx0 = boxes_x0
        by0 = y + 16
        for i, p in enumerate(reps):
            r = i // cols
            c = i % cols
            bx = bx0 + c * (box_w + box_gap)
            by = by0 + r * (box_h + box_gap)
            if by + box_h > y + band_h - 6:
                break
            cat = tags[p]["category"]
            fill = K.CATEGORY_COLORS.get(cat, "#555")
            a(f'<rect x="{bx}" y="{by}" width="{box_w}" height="{box_h}" rx="6" '
              f'fill="{fill}" fill-opacity="0.22" stroke="{fill}" stroke-width="1.4"/>')
            name = p if len(p) <= 17 else p[:16] + "…"
            a(f'<text x="{bx+8}" y="{by+17}" font-size="11.5" font-weight="600" '
              f'fill="{TEXT}">{_esc(name)}</text>')
            ver = packages[p]["version"].split("-")[0]
            ver = ver if len(ver) <= 14 else ver[:13] + "…"
            a(f'<text x="{bx+8}" y="{by+31}" font-size="9" fill="{TEXT_DIM}">'
              f'{_esc(ver)}</text>')
            glyph, gcol = EDITION_MARK[tags[p]["edition"]]
            if glyph:
                a(f'<text x="{bx+box_w-13}" y="{by+15}" font-size="11" '
                  f'fill="{gcol}">{glyph}</text>')
        # "+N more" hint if the band has more than we drew
        drawn = min(len(reps), (cols * ((band_h - 22) // (box_h + box_gap))))
        if len(band_pkgs) > drawn:
            a(f'<text x="{W-margin-12}" y="{y+band_h-12}" font-size="11" '
              f'text-anchor="end" fill="{TEXT_DIM}">+{len(band_pkgs)-drawn} more '
              f'in this layer</text>')
        y += band_h + band_gap

    # ---- legend ---------------------------------------------------------- #
    ly = y + 4
    a(f'<text x="{margin}" y="{ly+4}" font-size="14" font-weight="700" '
      f'fill="{TEXT}">Category</text>')
    lx = margin
    lyy = ly + 24
    per_row = 6
    cw = inner_w // per_row
    for i, cat in enumerate(present_cats):
        col = i % per_row
        row = i // per_row
        px = margin + col * cw
        py = lyy + row * 22
        color = K.CATEGORY_COLORS.get(cat, "#555")
        a(f'<rect x="{px}" y="{py-11}" width="12" height="12" rx="3" '
          f'fill="{color}" fill-opacity="0.35" stroke="{color}"/>')
        a(f'<text x="{px+18}" y="{py}" font-size="11" fill="{TEXT}">{_esc(cat)}</text>')

    # edition legend on the far right of the first legend row
    ex = margin
    ey = lyy + ((len(present_cats) + per_row - 1) // per_row) * 22 + 10
    a(f'<text x="{ex}" y="{ey+4}" font-size="14" font-weight="700" '
      f'fill="{TEXT}">Edition</text>')
    items = [
        (f'{EDITION_MARK["azzio"][0]} Azzio Component',
         "in the set only because Azzio added it (a chosen app or its support)", BRAND_CYAN),
        ("Stock Arch",
         "already on the stock archiso releng medium; Azzio inherits it", TEXT_DIM),
    ]
    exx = margin + 90
    for label, desc, col in items:
        a(f'<text x="{exx}" y="{ey+4}" font-size="11.5" font-weight="700" '
          f'fill="{col}">{_esc(label)}</text>')
        a(f'<text x="{exx+120}" y="{ey+4}" font-size="11" fill="{TEXT_DIM}">'
          f'{_esc(desc)}</text>')
        ey += 20

    a('</svg>')
    return "\n".join(s) + "\n"
