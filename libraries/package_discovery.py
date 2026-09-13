"""package_discovery -- find the Azzio packages the compiler should emit.

``packages`` is a NAMESPACE package (PEP 420): it is just the directory
``libraries/packages/`` with NO ``__init__.py`` of its own. Each Azzio package is its
OWN sub-directory with an ``__init__.py`` (kitty/, openbox/, gedit/, file_manager/, librewolf/,
calamares/, application_menu/, ...), and it is those sub-packages that are importable as
``from packages import kitty`` etc. A directory WITHOUT an ``__init__.py`` is not a package,
so it is simply skipped -- which is exactly the "add or remove a package by creating/deleting
its directory, and a half-finished/data-only directory never breaks the build" contract. The
lone non-directory entry, the ``packages.x86_64`` manifest file, is skipped too.

Because ``packages`` has no ``__init__.py``, the discovery helpers cannot live inside it;
they live here, as a flat sibling module in ``libraries/`` (imported bare, like
``compiler``/``paths``/``emit``). They scan the ``packages/`` directory ON DISK for
sub-directories that contain an ``__init__.py`` and import those:

  * ``names()``            -- the package names present, WITHOUT importing any.
  * ``discover()``         -- import each and return {name: module}, optionally filtered.
  * ``with_emit_plan()``   -- just the ones exposing an ``emit_plan()`` (the builder/dest/mode
    contract ``compiler.py`` iterates in ``_emit_apps``), minus an optional exclusion set.

The compiler uses these so dropping a new ``packages/<app>/__init__.py`` with an
``emit_plan()`` ships it with no edit to the compiler's import list, and removing one does
not leave a dangling ``import`` that aborts the build.

Note: only the per-application TWEAK packages are meant to be auto-emitted this way. The
packages the compiler drives BY NAME -- because they expose more than ``emit_plan()``, feed
the desktop step, or need explicit ordering (openbox, librewolf, application_menu, passwords,
calamares, and the azzio guest command line interface) -- are handed to ``with_emit_plan()``
as ``exclude`` so they are not emitted twice.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable

# The packages directory lives right next to this module (both under libraries/). This is the
# single place packages are scanned from -- the namespace package's one directory.
_PACKAGES_DIR = Path(__file__).resolve().parent / "packages"
# Sub-packages import as ``packages.<name>`` (the namespace package's name), regardless of how
# this flat helper module itself is imported.
_PKG_NAME = "packages"


def names() -> list[str]:
    """Every package present, as a sorted list of directory names, WITHOUT importing any.

    A directory counts as a package only if it contains an ``__init__.py`` (so it is a real
    package that can be imported). Directories without one -- and ``__pycache__``, any stray
    dunder dirs, and the ``packages.x86_64`` manifest FILE -- are skipped. This is the
    "add/remove a directory freely" contract: the list simply reflects whatever
    directories-with-__init__.py are on disk right now."""
    found = []
    for child in _PACKAGES_DIR.iterdir():
        if not child.is_dir():
            continue                            # packages.x86_64 (a data file) and the like
        if child.name.startswith("__"):         # __pycache__ and the like
            continue
        if not (child / "__init__.py").is_file():
            continue                            # no __init__.py -> skip (not a package)
        found.append(child.name)
    return sorted(found)


def discover(predicate: Callable[[ModuleType], bool] | None = None) -> dict[str, ModuleType]:
    """Import every package and return {name: module}, skipping any directory without an
    ``__init__.py``.

    A directory that lacks ``__init__.py`` is silently skipped (see the module docstring):
    this is what lets a package be added or removed just by creating/deleting its directory,
    with the compiler never tripping over a missing or extra one.

    predicate: optional filter run on each imported module; only modules for which it returns
    True are included (e.g. ``lambda m: hasattr(m, "emit_plan")`` to get just the builders)."""
    modules: dict[str, ModuleType] = {}
    for name in names():
        module = importlib.import_module(f"{_PKG_NAME}.{name}")
        if predicate is None or predicate(module):
            modules[name] = module
    return modules


def with_emit_plan(exclude: Iterable[str] = ()) -> dict[str, ModuleType]:
    """The packages that expose an ``emit_plan()`` (the builder/dest/mode contract the
    compiler iterates to write configuration files), MINUS any name in ``exclude``.

    ``exclude`` is how the compiler keeps the packages it drives BY NAME (openbox, librewolf,
    application_menu, passwords, calamares, azzio) out of the auto-discovered app loop so they
    are not emitted twice -- see the module docstring. A convenience wrapper over discover()."""
    skip = set(exclude)
    plans = discover(lambda m: callable(getattr(m, "emit_plan", None)))
    return {name: module for name, module in plans.items() if name not in skip}
