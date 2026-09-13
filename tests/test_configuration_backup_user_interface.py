"""packages.backup -- the shared CLI presentation helpers (`user_interface.py`) and the
polished `backup`/`unpack` output (step three of the distribution PROMPT).

The PROMPT's step three asked for CLEAN, readable output for both commands: aligned field
rows, ``  - item`` bullets, clear success/warning lines, and sensible spacing, matching the
tone of `passwords` -- presentation only, without regressing the two-archive/vault behaviour
(that stays pinned by the other backup/unpack tests). These tests pin the presentation
CONTRACT so it cannot silently regress:

  * user_interface.header() prints the title with a rule under it;
  * user_interface.field() prints aligned ``Label: value`` rows;
  * user_interface.bullet()/note()/warn() use the documented prefixes;
  * the rule is encoding-safe (falls back to ASCII '-' on an ASCII-only stream);

They are stdlib-only and never shell out (user_interface.py is pure ``print``), so they run
anywhere.

The module is FULLY NAMED ``user_interface.py`` (step five's rename -- no abbreviated ``ui.py``
filename remains), matching the repo convention of spelled-out module names
(command_line_interface.py / terminal_user_interface.py / default_applications.py).
"""

from __future__ import annotations

import io

import paths
from packages.backup import user_interface


class _Stream(io.StringIO):
    """A StringIO whose ``encoding`` is settable (the real attribute is read-only), so a
    test can pin how user_interface.py chooses its rule glyph for a UTF-8 vs. an ASCII-only
    console."""

    def __init__(self, encoding="utf-8"):
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self):
        return self._encoding


# --- user_interface.rule(): encoding-safe horizontal rule -------------------
def test_rule_uses_box_dash_on_utf8_and_ascii_dash_otherwise():
    """The rule is a light box-drawing dash on a UTF-8 stream (tidy in the kitty
    terminal) but degrades to a plain ASCII '-' on an ASCII-only stream, so a redirected
    or C-locale console never raises on the print."""
    assert user_interface.rule(width=10, stream=_Stream("utf-8")) == "─" * 10
    assert user_interface.rule(width=10, stream=_Stream("ascii")) == "-" * 10


# --- user_interface.header(): title + rule ----------------------------------
def test_header_prints_title_then_a_rule():
    """header() prints the title on its own line followed by a rule of the same glyph."""
    buf = _Stream("utf-8")
    user_interface.header("Azzio backup", stream=buf)
    lines = buf.getvalue().splitlines()
    assert lines[0] == "Azzio backup"
    assert set(lines[1]) == {"─"} and len(lines[1]) >= 10


# --- user_interface.field(): aligned Label: value rows ----------------------
def test_field_rows_align_on_the_value_column():
    """field() left-pads the (label + colon) to a fixed width so a block of rows lines up
    on the value. Two rows with different-length labels must start their values at the
    same column."""
    buf = _Stream("utf-8")
    user_interface.field("Home", "/home/main", stream=buf)
    user_interface.field("Items", "2 to archive", stream=buf)
    a, b = buf.getvalue().splitlines()
    assert a.startswith("Home:")
    assert b.startswith("Items:")
    # The value starts at the same column in both rows (aligned block).
    assert a.index("/home/main") == b.index("2 to archive")


# --- user_interface.bullet()/note()/warn(): the documented prefixes ---------
def test_bullet_note_and_warn_prefixes():
    """The list/aside prefixes are fixed so the output reads consistently."""
    for fn, prefix, text in ((user_interface.bullet, "  - ", "Documents"),
                             (user_interface.note, "  note: ", "no store"),
                             (user_interface.warn, "  warning: ", "did not unlock")):
        buf = _Stream("utf-8")
        fn(text, stream=buf)
        assert buf.getvalue() == f"{prefix}{text}\n"


# --- user_interface.result_line(): aligned "  label  ->  dest  (size)" ------
def test_result_line_shows_arrow_dest_and_size():
    """A written-archive / restored-destination line shows the aligned label, an arrow,
    the destination, and (when given) the size in parentheses."""
    buf = _Stream("utf-8")
    user_interface.result_line("home archive", "~/backup.tar.gz.gpg", "12.34 MB",
                               stream=buf)
    out = buf.getvalue()
    assert "home archive" in out and "->" in out
    assert "~/backup.tar.gz.gpg" in out and "(12.34 MB)" in out


# --- the module ships under its FULL name (module discovery) ----------------
def test_user_interface_module_ships_with_the_package():
    """user_interface.py is a real source in the backup package, so packaging.py's module
    discovery ships it to LIB_DIR alongside the other flat modules (no packaging edit
    needed)."""
    from packages.backup import packaging as bk
    shipped = {e["dest"] for e in bk.emit_plan()}
    assert f"{bk.LIB_DIR}/user_interface.py" in shipped
    src = (paths.BACKUP_DIR / "user_interface.py").read_text(encoding="utf-8")
    assert "def header(" in src and "def field(" in src


def test_no_abbreviated_ui_module_ships_or_exists():
    """The abbreviated ``ui.py`` filename is GONE (step five's rename): it neither exists on
    disk in the package nor rides along in the emit plan. Only the fully-named
    user_interface.py ships."""
    from packages.backup import packaging as bk
    assert not (paths.BACKUP_DIR / "ui.py").exists()
    shipped = {e["dest"] for e in bk.emit_plan()}
    assert f"{bk.LIB_DIR}/ui.py" not in shipped
    # And nothing in the plan is a half-named "ui.py" at any path.
    assert not any(e["dest"].endswith("/ui.py") for e in bk.emit_plan())
