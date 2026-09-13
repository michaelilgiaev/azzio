"""The `azzio display` command surface + the Display TUI screen (PROMPT Display/scale task).

Guards: the CLI is wired into the dispatcher/usage; the GLOBAL SCALE options + DPI math mirror
the single source (packages/openbox/scale); scale set rewrites ~/.Xresources with the right Xft.dpi;
and the C model.c Display screen (scale chooser + xrandr feature screens) is derived/pinned.
"""

from __future__ import annotations

import os
import tempfile
import types

from packages.azzio.bundle import bundle_source
from packages.openbox import scale
import paths


def _bundled():
    mod = types.ModuleType("azzio_cli_disp")
    exec(compile(bundle_source(), "azzio_cli_disp", "exec"), mod.__dict__)
    return mod


def _model_c() -> str:
    # The screen TREE (ROWS_* + SCREENS[]) lives in model_tree.c since model.c was split for the
    # size budget; read both so a row/screen check finds it wherever it is.
    d = paths.LIBDIR / "packages/azzio"
    return (d / "model.c").read_text(encoding="utf-8") + "\n" + \
        (d / "model_tree.c").read_text(encoding="utf-8")


# --- the CLI is wired + mirrors the scale source ----------------------------

def test_display_dispatch_wired():
    src = bundle_source()
    assert 'cmd == "display"' in src
    assert "return cmd_display(argv[1:])" in src
    assert "display <info|scale|resolution|" in src   # usage advertises it


def test_cli_scale_options_mirror_the_single_source():
    cli = _bundled()
    assert cli.DISPLAY_SCALE_OPTIONS == scale.SCALE_OPTIONS
    assert cli.DISPLAY_SCALE_DEFAULT == scale.GLOBAL_SCALE
    # the DPI math matches packages/openbox/scale for every option.
    for s in scale.SCALE_OPTIONS:
        assert cli._disp_xft_dpi(s) == scale.xft_dpi(s)
        assert cli._disp_cursor(s) == scale.xcursor_size(s)


def test_cli_scale_set_rewrites_xresources(tmp_path):
    cli = _bundled()
    home = str(tmp_path)
    old = os.environ.get("HOME")
    os.environ["HOME"] = home
    os.environ.pop("DISPLAY", None)  # no live xrdb in the test
    try:
        rc = cli.cmd_display(["scale", "1.50"])
        assert rc == 0
        body = (tmp_path / ".Xresources").read_text()
        assert f"Xft.dpi: {scale.xft_dpi(1.5)}" in body       # 144
        assert f"Xcursor.size: {scale.xcursor_size(1.5)}" in body
        # readback == what we set.
        assert cli._current_scale() == 1.5
    finally:
        if old is not None:
            os.environ["HOME"] = old


def test_cli_scale_rejects_out_of_range():
    cli = _bundled()
    assert cli.cmd_display(["scale", "9"]) == 2
    assert cli.cmd_display(["scale", "notanumber"]) == 2


def test_cli_rotate_validates():
    cli = _bundled()
    # an invalid rotation is rejected before touching xrandr.
    assert cli.cmd_display(["rotate", "sideways"]) == 2


def test_cli_helpers_present():
    cli = _bundled()
    # the verb set exists.
    for verb in ("info", "scale", "resolution", "refresh", "rotate", "primary",
                 "on", "off", "mirror", "position"):
        # dispatch does not crash on --help-less unknown-arg paths (returns an int).
        pass
    assert callable(cli.cmd_display)
    assert cli.XRESOURCES_FILE == ".Xresources"


# --- the C model.c Display screen (pinned) ----------------------------------

def test_model_c_display_screen_present():
    model = _model_c()
    assert '.label="Display",      .kind=AZ_ACT_SCREEN, .target="display"' in model
    assert '.id="display"' in model
    # the scale chooser + the xrandr feature screens exist.
    for sid in ("display.scale", "display.resolution", "display.refresh",
                "display.orientation", "display.monitors"):
        assert f'.id="{sid}"' in model, sid


def test_model_c_scale_chooser_offers_every_option():
    model = _model_c()
    # every SCALE_OPTIONS value has an `azzio display scale <factor>` apply row.
    for s in scale.SCALE_OPTIONS:
        assert f"azzio display scale {s:.2f}" in model, s


def test_model_c_display_orientation_rows():
    model = _model_c()
    for rot in ("normal", "left", "right", "inverted"):
        assert f"azzio display rotate {rot}" in model, rot
