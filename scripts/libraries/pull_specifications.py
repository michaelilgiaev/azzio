"""
pull_specifications -- orchestrator for the Azzio distribution specification.

Generates documentations/SPECIFICATIONS.md: a technical specification of the OS that ships
on the ISO, centred on the real package dependency graph (base kernel/libs at the
bottom, leaf applications at the top).

Pipeline:
  specification_db      fetch + parse the official Arch core/extra/multilib databases
  specification_resolve expand the manifest, walk the full transitive closure, tier it
  specification_render  emit the Markdown from the resolved data + editorial content

Invoked by scripts/pull_specifications.sh (which sets up paths), or directly:
  python3 scripts/libraries/pull_specifications.py [options]
"""

import argparse
import os
import re
import sys

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

import specification_db
import specification_resolve
import specification_classify
import specification_render
import specification_html
import specification_stock_baseline

# scripts/libraries/ -> repo root is two levels up.
REPO_ROOT = os.path.abspath(os.path.join(_SELF_DIR, "..", ".."))
DEFAULT_MANIFEST = os.path.join(REPO_ROOT, "libraries", "packages", "packages.x86_64")
# The stock archiso `releng` baseline (the ground truth for the "Stock Arch"
# edition) lives in code as specification_stock_baseline.STOCK_PACKAGES, not as a data
# file, so it is not mistaken for an editable manifest. --stock-manifest can
# still point at a file to override it; None means "use the module".
DEFAULT_OUTPUT = os.path.join(REPO_ROOT, "documentations", "SPECIFICATIONS_GENERAL.md")
DEFAULT_HTML = os.path.join(REPO_ROOT, "documentations",
                            "SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html")
DEFAULT_CACHE = os.path.join(REPO_ROOT, "cache", "specification-db")
# These four modules are read as SOURCE TEXT for the regex extraction below (not
# imported). They no longer share one parent: profile/pacman/installer are compiler config
# flat in libraries/, while locale is the Calamares install-time locale block, which lives with
# the calamares package (libraries/packages/calamares/). So each path is built explicitly --
# there is no single base dir any more.
LIBRARIES_DIR = os.path.join(REPO_ROOT, "libraries")
PROFILE_PY = os.path.join(LIBRARIES_DIR, "profile.py")
PACMAN_PY = os.path.join(LIBRARIES_DIR, "pacman.py")
INSTALLER_PY = os.path.join(LIBRARIES_DIR, "installer.py")
LOCALE_PY = os.path.join(LIBRARIES_DIR, "packages", "calamares", "locale.py")


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def read_cow_spacesize():
    """Read the live-session writable-RAM size (cow_spacesize) from the real build
    configuration, so the specification reports the actual configured value, not a hardcoded one."""
    m = re.search(r'cow_spacesize\s*=\s*["\']([^"\']+)["\']', _read(PROFILE_PY))
    return m.group(1) if m else "unknown"


def read_endpoints():
    """Extract the external network endpoints the distro contacts, live from the
    real configuration modules. Returns a list of (endpoint, purpose, context) rows so the
    specification stays honest if a mirror / service URL is changed.

    Everything here is read from the real configuration modules (pacman.py,
    installer.py in libraries/; locale.py in libraries/packages/calamares/) -- the
    same strings baked into the ISO -- so this is not a hand-maintained list that
    can drift.
    """
    rows = []

    # Package mirrors: any 'Server = http(s)://...' in the pacman configuration.
    pac = _read(PACMAN_PY)
    seen = set()
    for url in re.findall(r'Server\s*=\s*(https?://[^\s"\'\\]+)', pac):
        host = re.sub(r'^https?://', '', url).split('/')[0]
        if host in seen:
            continue
        seen.add(host)
        rows.append((host, "Package download mirror (build-time, hard-coded)",
                     "pacman.py -- used by the cache/download step; host-independent"))
    if "Include = /etc/pacman.d/mirrorlist" in pac:
        rows.append(("/etc/pacman.d/mirrorlist",
                     "Package download mirrors (installed system + live ISO)",
                     "pacman.py -- the standard Arch mirrorlist on the running OS"))
    file_seen = set()
    for m in re.findall(r'Server\s*=\s*(file://[^\s"\'\\]+)', pac):
        # Skip the commented example and unformatted f-string templates ({...}).
        if "custompkgs" in m or "{" in m or m in file_seen:
            continue
        file_seen.add(m)
        rows.append((m, "Offline package install from the baked-in local repo",
                     "pacman.py -- the fully-offline install path"))

    # Geo-IP locale/timezone service: curl https://host/... in locale.py.
    loc = _read(LOCALE_PY)
    geo_hosts = set()
    for url in re.findall(r'curl[^\n]*?(https?://[^\s"\'\\)]+)', loc):
        host = re.sub(r'^https?://', '', url).split('/')[0]
        geo_hosts.add(host)
    for host in sorted(geo_hosts):
        rows.append((host, "Geo-IP lookup -> timezone, country, locale, keyboard",
                     "locale.py -- queried on first boot to auto-detect region"))

    # Connectivity probe + NTP in the installer.
    inst = _read(INSTALLER_PY)
    for host in re.findall(r'ping\s+-c\s*\d+\s+([A-Za-z0-9.\-]+)', inst):
        rows.append((host, "Connectivity probe before enabling time sync",
                     "installer.py -- pinged for up to 15s on first boot"))
    if "set-ntp true" in inst:
        rows.append(("systemd-timesyncd (NTP)",
                     "Network time sync once connectivity is confirmed",
                     "installer.py -- `timedatectl set-ntp true`; uses systemd's "
                     "default NTP servers"))

    return rows


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="pull_specifications",
        description="Generate the Azzio distribution specification.",
    )
    p.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                   help=f"output Markdown file (default: {DEFAULT_OUTPUT})")
    p.add_argument("--html", default=DEFAULT_HTML,
                   help=f"output interactive HTML component map "
                        f"(default: {DEFAULT_HTML}); the SVG map, but navigable")
    p.add_argument("-m", "--manifest", default=DEFAULT_MANIFEST,
                   help=f"package manifest (default: {DEFAULT_MANIFEST})")
    p.add_argument("--stock-manifest", default=None,
                   help="optional file to override the built-in stock archiso "
                        "releng baseline (specification_stock_baseline.STOCK_PACKAGES) "
                        "used to split the two editions")
    p.add_argument("--db-cache", default=DEFAULT_CACHE,
                   help=f"where to store the fetched Arch .db files "
                        f"(default: {DEFAULT_CACHE})")
    p.add_argument("--mirror", default=specification_db.DEFAULT_MIRROR,
                   help=f"Arch mirror base URL (default: {specification_db.DEFAULT_MIRROR})")
    p.add_argument("--offline", action="store_true",
                   help="do not download; reuse the .db files already in --db-cache")
    p.add_argument("--stdout", action="store_true",
                   help="print the report to stdout instead of writing the file")
    return p.parse_args(argv)


def _build_glance(packages, resolved, tiers, tags):
    """Assemble the at-a-glance facts shared by the Markdown and the SVG."""
    closure = resolved["closure"]
    roots = resolved["roots"]
    by_repo = {r: sum(1 for p in closure if packages[p]["repo"] == r)
               for r in specification_db.REPOS}
    total_isize = sum(packages[p]["isize"] for p in closure)
    from collections import Counter
    ed_counts = Counter(t["edition"] for t in tags.values())

    # Derive the desktop/display-manager facts from the resolved closure so they
    # stay correct as components change. We detect a display manager by looking
    # for any known DM package in the closure; the live session is KDE Plasma when
    # 'plasma-desktop' is present (Openbox when 'openbox' is, for older builds).
    # NOTE: Calamares is built from a local/AUR source and is NOT in the Arch DBs,
    # so it never appears in the closure -- do not detect the session from it. We
    # still mention it unconditionally because the build always ships and
    # auto-launches it.
    known_dms = ("sddm", "gdm", "lightdm", "lxdm", "ly")
    present_dm = next((dm for dm in known_dms if dm in closure), None)
    if "plasma-desktop" in closure:
        desktop = ("KDE Plasma (X11) live session; getty autologin -> startx -> "
                   "startplasma-x11, auto-launches Calamares")
        if present_dm:
            dm = f"{present_dm} display manager"
        else:
            dm = "None -- getty autologin to startx (no display manager)"
    elif "openbox" in closure:
        desktop = ("Openbox live session; getty autologin -> startx -> "
                   "openbox-session, auto-launches Calamares")
        if present_dm:
            dm = f"{present_dm} display manager"
        else:
            dm = "None -- getty autologin to startx (no display manager)"
    elif present_dm:
        # Some other desktop paired with a display manager (future stripped/reworked build).
        desktop = f"graphical session (via {present_dm})"
        dm = f"{present_dm} display manager"
    else:
        # No known desktop, no display manager: a console-only build.
        desktop = "None (console only)"
        dm = "None -- getty autologin, no display manager"

    return {
        "base": "Arch Linux (rolling), x86_64",
        "desktop": desktop,
        "kernel": specification_render._pkg_version(packages, "linux"),
        "init": specification_render._pkg_version(packages, "systemd"),
        "dm": dm,
        "iso_version": "date-based, YYYY.MM.DD (no semver)",
        "ram": f"{read_cow_spacesize()} writable overlay held in RAM",
        "purpose": "live / rescue / installer medium (stripped-down Arch base)",
        "tokens": len(resolved["manifest_map"]),
        "raw_lines": resolved["raw_lines"],
        "by_repo": by_repo,
        "closure": len(closure),
        # Two editions only: Stock Arch (already on the stock archiso releng
        # medium) vs Azzio Component (in the set only because Azzio added it).
        "stock": ed_counts.get("stock", 0),
        "azzio": ed_counts.get("azzio", 0),
        "max_height": tiers["max_height"],
        "size": f"{total_isize / 1024 ** 3:.2f} GiB",
        "endpoints": read_endpoints(),
    }


def build(manifest_path, db_cache, mirror, offline,
          graph_rel="SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html",
          stock_manifest_path=None):
    """Run the full pipeline. Return (markdown, html).

    graph_rel is the relative link the prose points at for the dependency graph
    (the interactive HTML map). stock_manifest_path=None uses the built-in baseline
    (specification_stock_baseline.STOCK_PACKAGES); pass a path to override it from a file.
    """
    db_paths = specification_db.fetch_databases(db_cache, mirror=mirror, offline=offline)
    packages, provides, groups = specification_db.load_databases(db_paths)
    print(f"[specification] indexed {len(packages)} packages from core/extra/multilib",
          file=sys.stderr)

    tokens, raw_lines = specification_resolve.load_manifest(manifest_path)
    resolved = specification_resolve.resolve_closure(tokens, packages, provides, groups)
    resolved["raw_lines"] = raw_lines
    tiers = specification_resolve.compute_tiers(resolved)

    # Split the closure into the two editions by walking the STOCK archiso releng
    # baseline: anything that baseline already pulls in is "Stock Arch", the rest
    # is an "Azzio Component". The baseline is the built-in list in
    # specification_stock_baseline, unless a file override is supplied.
    if stock_manifest_path:
        stock_tokens, _ = specification_resolve.load_manifest(stock_manifest_path)
    else:
        stock_tokens = list(specification_stock_baseline.STOCK_PACKAGES)
    stock_reach = specification_resolve.stock_reachable(
        stock_tokens, resolved["closure"], packages, provides, groups)
    tags = specification_classify.classify(packages, resolved["closure"], stock_reach)

    unresolved_tokens = [t for t, v in resolved["manifest_map"].items()
                         if v["kind"] == "unresolved"]
    if unresolved_tokens:
        print(f"[specification] WARN: manifest tokens that did not resolve: "
              f"{unresolved_tokens}", file=sys.stderr)
    if resolved["unresolved"]:
        uniq = sorted({tok for _, tok in resolved["unresolved"]})
        print(f"[specification] WARN: {len(resolved['unresolved'])} unresolved dependency "
              f"edges ({len(uniq)} unique tokens)", file=sys.stderr)

    from collections import Counter
    ed_counts = Counter(t["edition"] for t in tags.values())
    print(f"[specification] closure: {len(resolved['closure'])} packages | "
          f"{len(tiers['bases'])} base / {len(tiers['leaves'])} top | "
          f"editions {dict(ed_counts)}", file=sys.stderr)

    glance = _build_glance(packages, resolved, tiers, tags)
    md = specification_render.render(packages, resolved, tiers, tags, glance, graph_rel)
    page = specification_html.render_html(packages, resolved, tiers, tags, glance)
    return md, page


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    # The prose points at the interactive HTML map as the dependency-graph artifact,
    # linked relative to the general Markdown's directory (both live in documentations/).
    graph_rel = os.path.relpath(args.html, os.path.dirname(args.output))
    md, page = build(args.manifest, args.db_cache, args.mirror,
                     args.offline, graph_rel=graph_rel,
                     stock_manifest_path=args.stock_manifest)

    if args.stdout:
        sys.stdout.write(md)
        return 0

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        f.write(md)
    print(f"[specification] wrote {args.output} "
          f"({md.count(chr(10)) + 1} lines, {len(md)} bytes)", file=sys.stderr)

    os.makedirs(os.path.dirname(args.html), exist_ok=True)
    with open(args.html, "w") as f:
        f.write(page)
    print(f"[specification] wrote {args.html} ({len(page)} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
