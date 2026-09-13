"""Bundle the `azzio` guest-CLI package into ONE self-contained script.

The `azzio` command line interface source is split across several small modules under this package
(libraries/packages/azzio/) for maintainability, but the artifact that ships to the guest
is a SINGLE file at /usr/local/bin/azzio: it must be one runnable Python script (a lone
executable at a path, not an importable package). This module reassembles the split source
into that single script.

HOW THE BUNDLE IS BUILT. Every source module has, right after its imports, a line:

    # BUNDLE_START

`bundle_source()` emits:
  * the HEADER of the first module (common.py) -- its shebang, module docstring, and all
    imports, i.e. everything UP TO AND INCLUDING its `# BUNDLE_START` line -- once; then
  * the BODY of each module in MODULE_ORDER -- everything AFTER that module's `# BUNDLE_START`
    line -- concatenated in order.

Because the result is one module namespace, the later modules reference the shared helpers
and the country table by bare name (no intra-package imports), which is exactly how they are
written. The order is chosen so every name is defined before it is used at IMPORT time
(definitions only; nothing runs until main() is called at the very end via command_line_interface.py).

packages.openbox.azzio_command_line_interface() calls bundle_source() and then re-injects the country table
between the AZZIO_CC markers from the single source of truth (packages/calamares/locale).
"""

from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent

# The bundle order. common.py FIRST (its header becomes the whole script's preamble), then
# the data + feature modules (definitions), then command_line_interface.py LAST (usage/main + the __main__
# guard that actually runs it). Every module must contain a `# BUNDLE_START` marker.
MODULE_ORDER = [
    "common.py",
    "country_table.py",
    "resolver.py",
    "theme.py",
    "wallpaper.py",
    # machine.py before media.py: media's brightness gate calls machine.is_laptop() by bare
    # name (a laptop-only control), so the machine helpers must be defined first.
    "machine.py",
    # gpu.py: `azzio gpu` (PCI GPU detection + offline driver resolve). A hardware probe like
    # machine.py, with no dependency on later modules; before command_line_interface.py, which
    # dispatches `gpu` to cmd_gpu by bare name.
    "gpu.py",
    "media.py",
    # firewall.py before network.py: network's status/dispatch call the firewall functions
    # by bare name, and this keeps every module under the per-file size budget.
    "firewall.py",
    "network.py",
    # default_applications_command_line_interface.py: the `azzio default-applications` surface
    # behind the TUI's Default Applications screen. Standalone (bundled), before
    # command_line_interface.py.
    "default_applications_command_line_interface.py",
    # display.py: the `azzio display` surface (xrandr + the GLOBAL SCALE) behind the TUI's
    # Display screen. Standalone (bundled), before command_line_interface.py.
    "display.py",
    # backup_targets.py: the `azzio backup --configure` surface (opt in to the `backup`
    # command's optional USB + Google Drive targets). Standalone (bundled), before
    # command_line_interface.py.
    "backup_targets.py",
    "sshd.py",
    # security_notice.py: the first-run `azzio security-notice` warning (shown from the
    # live autostart). Uses only shared helpers; before command_line_interface.py, whose
    # main() dispatches `security-notice` to cmd_security_notice by bare name.
    "security_notice.py",
    # power.py: `azzio power|shutdown|restart|sleep|lock` with timer flags. Standalone
    # (bundled), before command_line_interface.py, which dispatches those verbs by bare name.
    "power.py",
    # mkazzioiso.py: Method B -- `azzio mkazzioiso --ssh=...` repacks the LIVE system
    # into the azzio-headed-ssh ISO. Standalone (bundled), before command_line_interface.py,
    # whose main() dispatches the `mkazzioiso` subcommand to cmd_mkazzioiso by bare name.
    "mkazzioiso.py",
    # terminal_user_interface.py is the bare-`azzio` full-screen UI. It calls the theme/wallpaper/network
    # helpers by bare name, so it MUST be bundled after all of them; and before command_line_interface.py,
    # whose main() dispatches the no-argument case to run_terminal_user_interface().
    "terminal_user_interface.py",
    "command_line_interface.py",
]

_MARKER = "# BUNDLE_START"


def _split(src: str, name: str) -> tuple[str, str]:
    """Split a module's source at its `# BUNDLE_START` marker into (header, body).

    header = everything up to AND INCLUDING the marker line; body = everything after it.
    The marker MUST be present (each package module carries one) -- a missing marker is a
    packaging bug, so raise loudly rather than silently mis-bundling."""
    idx = src.find(_MARKER)
    if idx == -1:
        raise ValueError(f"azzio bundle: module {name} has no {_MARKER!r} marker")
    # Include the rest of the marker line in the header.
    line_end = src.find("\n", idx)
    if line_end == -1:
        return src, ""
    return src[: line_end + 1], src[line_end + 1:]


def bundle_source() -> str:
    """Return the single self-contained /usr/local/bin/azzio script text (with the
    on-disk country table; packages.openbox.azzio_command_line_interface re-injects the canonical one)."""
    parts: list[str] = []
    for i, mod in enumerate(MODULE_ORDER):
        src = (_PKG_DIR / mod).read_text(encoding="utf-8")
        header, body = _split(src, mod)
        if i == 0:
            # First module's header (shebang + docstring + imports) is the script preamble.
            parts.append(header.rstrip("\n"))
            parts.append("")
            parts.append(f"# --- bundled from {mod} ---")
            parts.append(body.strip("\n"))
        else:
            parts.append("")
            parts.append(f"# --- bundled from {mod} ---")
            parts.append(body.strip("\n"))
    return "\n".join(parts).rstrip("\n") + "\n"
