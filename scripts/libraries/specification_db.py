"""
specification_db -- fetch and parse the Arch Linux package databases.

The Azzio ISO is assembled against the official Arch `core`, `extra` and
`multilib` repositories (the build runs inside `archlinux:latest`). This module
provides the ground-truth package metadata by reading those repos' real `.db`
tarballs -- NOT the host's pacman databases, which on a non-Arch host (e.g.
Manjaro) carry different versions and package names and would produce a wrong
specification.

Each `.db` is a gzipped tar of `<pkg>-<ver>/desc` files; every `desc` is a flat
`%FIELD%` record. We parse the fields we need (name, version, deps, provides,
etc.) into a lookup table, plus a virtual-provides index and a group index so
dependency resolution can follow `provides` and expand groups.
"""

import io
import os
import re
import sys
import tarfile
import urllib.request
from collections import defaultdict

REPOS = ("core", "extra", "multilib")
DEFAULT_MIRROR = "https://geo.mirror.pkgbuild.com"
ARCH = "x86_64"


def _log(msg):
    print(msg, file=sys.stderr)


def db_path(cache_dir, repo):
    return os.path.join(cache_dir, f"{repo}.db")


def fetch_databases(cache_dir, mirror=DEFAULT_MIRROR, offline=False, timeout=120):
    """Ensure core/extra/multilib .db files exist in cache_dir.

    Online: download each from the mirror (matching the ISO build environment).
    Offline: require them to already be present in cache_dir; error if missing.
    Returns the list of db file paths.
    """
    os.makedirs(cache_dir, exist_ok=True)
    paths = []
    for repo in REPOS:
        dest = db_path(cache_dir, repo)
        if offline:
            if not os.path.isfile(dest):
                raise FileNotFoundError(
                    f"--offline set but {dest} is missing; run once online first"
                )
            _log(f"[db] using cached {repo}.db")
        else:
            url = f"{mirror}/{repo}/os/{ARCH}/{repo}.db"
            _log(f"[db] fetching {url}")
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read()
            with open(dest, "wb") as f:
                f.write(data)
        paths.append(dest)
    return paths


def _parse_desc(text):
    """Parse one `desc` record ("%KEY%\\nval\\nval\\n\\n...") into {KEY: [vals]}."""
    fields = {}
    key = None
    for line in text.splitlines():
        m = re.match(r"^%([A-Z0-9]+)%$", line)
        if m:
            key = m.group(1)
            fields[key] = []
        elif line == "":
            key = None
        elif key is not None:
            fields[key].append(line)
    return fields


def dep_name(specification):
    """Strip a dependency/provides token down to its bare package name.

    'foo>=1.2' -> 'foo'; 'libfoo.so=1-64' -> 'libfoo.so'; 'foo: desc' -> 'foo'.
    """
    specification = specification.split(":", 1)[0].strip()
    for op in (">=", "<=", "=", ">", "<"):
        idx = specification.find(op)
        if idx != -1:
            specification = specification[:idx]
    return specification.strip()


def load_databases(db_paths):
    """Read the .db tarballs and build the package universe.

    Returns (packages, provides, groups):
      packages: name -> record dict (repo, version, desc, url, isize, license,
                depends[], optdepends[], provides_raw[], groups[], replaces[])
      provides: virtual name -> [provider package names]
      groups:   group name -> [member package names]

    First repo in REPOS order wins on duplicate names (core > extra > multilib).
    """
    packages = {}
    provides = defaultdict(list)
    groups = defaultdict(list)

    for path in db_paths:
        repo = os.path.splitext(os.path.basename(path))[0]
        with open(path, "rb") as f:
            raw = f.read()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
            for member in tar.getmembers():
                if not member.name.endswith("/desc"):
                    continue
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                fields = _parse_desc(fh.read().decode("utf-8", "replace"))
                name = (fields.get("NAME", [None]) or [None])[0]
                if not name or name in packages:
                    continue
                rec = {
                    "name": name,
                    "repo": repo,
                    "version": (fields.get("VERSION", [""]) or [""])[0],
                    "desc": (fields.get("DESC", [""]) or [""])[0],
                    "url": (fields.get("URL", [""]) or [""])[0],
                    "isize": int((fields.get("ISIZE", ["0"]) or ["0"])[0] or 0),
                    "csize": int((fields.get("CSIZE", ["0"]) or ["0"])[0] or 0),
                    "license": " ".join(fields.get("LICENSE", [])),
                    "depends": [dep_name(d) for d in fields.get("DEPENDS", [])],
                    "optdepends": fields.get("OPTDEPENDS", []),
                    "provides_raw": fields.get("PROVIDES", []),
                    "groups": fields.get("GROUPS", []),
                    "replaces": [dep_name(d) for d in fields.get("REPLACES", [])],
                }
                packages[name] = rec
                for prov in rec["provides_raw"]:
                    provides[dep_name(prov)].append(name)
                for g in rec["groups"]:
                    groups[g].append(name)

    return packages, dict(provides), dict(groups)
