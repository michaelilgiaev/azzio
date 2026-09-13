"""The azzio TUI's "Default Applications" screen + the `azzio default-applications` CLI, and
the LOCK-STEP that keeps them derived from packages/azzio/default_applications.py (PROMPT: the
TUI must derive its rows/labels from that single source, no second hardcoded copy -- mirror the
wallpaper.py <-> model.c pattern where a test pins the strings).

Three drift traps are guarded here:
  * the bundled CLI's DA_CATEGORIES mirror == default_applications.CATEGORIES / CANDIDATES,
  * the C model.c category screens (labels, keys, set-commands) match that same source,
  * the CLI is wired into the dispatcher + usage.
"""

from __future__ import annotations

import types

from packages.azzio import default_applications as da
from packages.azzio.bundle import bundle_source
import paths


def _bundled():
    mod = types.ModuleType("azzio_cli_da")
    exec(compile(bundle_source(), "azzio_cli_da", "exec"), mod.__dict__)
    return mod


def _model_c() -> str:
    # The C model is split across three TUs for the per-file size budget: model.c (infra +
    # probes), model_tree.c (static screen TREE: ROWS_* + SCREENS[]), and model_default_applications.c
    # (the runtime Default Applications screens + the AZ_DA_CATS descriptor table). Read all
    # three so a row/screen/table check finds it wherever it lives.
    d = paths.LIBDIR / "packages/azzio"
    return "\n".join((d / f).read_text(encoding="utf-8")
                     for f in ("model.c", "model_tree.c", "model_default_applications.c"))


# --- single source of truth: the CLI mirror matches default_applications.py -----

def test_cli_da_categories_mirror_the_single_source():
    cli = _bundled()
    # keys + labels + groups + mimes + candidates must match default_applications exactly.
    src_rows = da.terminal_user_interface_categories()   # (group, label, key, default_id), in order (Mail included)
    # the CLI table omits Mail? No -- it includes every CATEGORIES row EXCEPT Mail is present in
    # default_applications but the TUI list omits it. The CLI mirrors the FULL set incl. Mail
    # (so `get mail` works), so compare against CATEGORIES directly.
    cli_by_key = {row[0]: row for row in cli.DA_CATEGORIES}
    for group, label, desktop_id, mimes in da.CATEGORIES:
        key = da.CATEGORY_KEYS[label]
        assert key in cli_by_key, f"CLI missing category {key}"
        ck, clabel, cgroup, cmimes, ccands = cli_by_key[key]
        assert clabel == label, key
        assert cgroup == group, key
        assert tuple(cmimes) == tuple(mimes), key
        assert tuple(ccands) == tuple(da.CANDIDATES[label]), key
    # no extra categories in the CLI beyond the source.
    assert set(cli_by_key) == {da.CATEGORY_KEYS[label] for _g, label, _d, _m in da.CATEGORIES}


def test_candidate_first_is_the_shipped_default():
    # The first candidate of each category must be that category's CATEGORIES default handler
    # (so re-picking the top option restores the shipped default). Mail has no default/candidate.
    for _group, label, desktop_id, _mimes in da.CATEGORIES:
        cands = da.CANDIDATES[label]
        if desktop_id:
            assert cands and cands[0] == desktop_id, label
        else:
            assert cands == (), label  # Mail: no handler, no candidates


def test_representative_mime_is_first_mime():
    for _group, label, _desktop_id, mimes in da.CATEGORIES:
        assert da.representative_mime(label) == (mimes[0] if mimes else "")


# --- the CLI is wired + behaves ---------------------------------------------

def test_default_applications_dispatch_wired():
    src = bundle_source()
    assert 'cmd == "default-applications"' in src
    assert "return cmd_default_applications(argv[1:])" in src
    # advertised in usage()
    assert "default-applications" in src


def test_cli_categories_verb_lists_keys():
    cli = _bundled()
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.cmd_default_applications(["categories"])
    assert rc == 0
    keys = buf.getvalue().split()
    assert keys == [da.CATEGORY_KEYS[label] for _g, label, _d, _m in da.CATEGORIES]


def test_cli_set_rejects_non_candidate():
    cli = _bundled()
    rc = cli.cmd_default_applications(["set", "web", "not-a-real-app.desktop"])
    assert rc == 2  # not offered for that category


def test_cli_set_rejects_unknown_category():
    cli = _bundled()
    rc = cli.cmd_default_applications(["set", "bogus", "librewolf.desktop"])
    assert rc == 2


def test_cli_helpers_rc_path_matches_emitter():
    # The CLI writes the exo TerminalEmulator selection to the SAME helpers.rc the emitter ships.
    cli = _bundled()
    assert da.HELPERS_RC_PATH.endswith("/.config/" + cli.DA_HELPERS_RC)


# --- the C model.c derives from the same source (pinning, wallpaper.py pattern) ---
# NOTE: the per-category candidate ROWS are now BUILT AT RUNTIME in C (az_da_screen), resolving
# against the installed .desktop files, so they are NOT static text in model.c anymore. What the
# C model DOES carry statically is the AZ_DA_CATS descriptor table (key + full MIME list + curated
# seed) that the runtime builder resolves from; the tests below pin THAT table to the single
# source (default_applications.py), plus the category-list screen and the dynamic-build wiring.

def test_model_c_defaultapps_list_screen_present():
    model = _model_c()
    # ROWS_MAIN has the entry; the category LIST screen id is present (still static).
    assert '.label="Default Applications", .kind=AZ_ACT_SCREEN, .target="defaultapps"' in model
    assert '.id="defaultapps"' in model
    # every TUI category (Mail excluded) is a row on the list screen that descends into its
    # per-category screen id, using the EXACT key from default_applications.CATEGORY_KEYS.
    terminal_user_interface_labels = [label for _g, label, _d, _m in da.CATEGORIES if label != "Mail"]
    for label in terminal_user_interface_labels:
        key = da.CATEGORY_KEYS[label]
        assert f'.target="defaultapps.{key}"' in model, key
        # the category row label appears (as a ROWS_DEFAULTAPPS entry).
        assert f'.label="{label}",' in model, label
    # Mail is NOT surfaced in the C model.
    assert '"defaultapps.mail"' not in model


def test_model_c_builds_defaultapps_screens_at_runtime():
    model = _model_c()
    # The runtime builder exists and az_screen_find delegates the "defaultapps." prefix to it, so
    # the candidate rows resolve live (the whole point: the list self-resolves from installed apps).
    assert "az_da_screen" in model
    assert 'strncmp(id, "defaultapps."' in model
    # The rows are built as `azzio default-applications set <key> <id>` applies (label == the bare
    # .desktop id, NOT "Set to ..."). Pin the set-command shape + that the old default-app row
    # labels ("Set to LibreWolf" / "Set to Firefox" / "Set to gedit", ...) are gone. ("Set to X%"
    # still legitimately labels the Volume/Brightness rows, which are unrelated -- so pin the
    # specific app-name labels that used to exist on the Default Applications screens.)
    assert "azzio default-applications set %s %s" in model
    for gone in ("Set to LibreWolf", "Set to Firefox", "Set to gedit", "Set to VLC",
                 "Set to Thunar", "Set to kitty", "Set to Qalculate", "Set to GIMP"):
        assert gone not in model, f"old default-app label still present: {gone!r}"


def test_model_c_da_cats_table_mirrors_source():
    # The C AZ_DA_CATS descriptor table (key, space-joined MIME list, space-joined curated seed)
    # must mirror default_applications.py exactly -- the wallpaper.py <-> model.c lock-step, so the
    # runtime resolver in C offers the same seed / matches the same MIME types as the Python side.
    #
    # Pin the WHOLE row per category, not loose substrings: a row is written exactly as
    #   {"<key>", "<mime1 mime2 ...>", "<seed1 seed2 ...>"},
    # so we extract the three quoted fields that FOLLOW `{"<key>",` and compare each field to the
    # source. (A loose `seed_join in model` check would MISS a single-element seed drifting to a
    # value that happens to appear in another category's row -- e.g. web/pdf both seed
    # "librewolf.desktop" -- so we bind the assertion to THIS key's row.)
    import re
    model = _model_c()
    for group, label, desktop_id, mimes in da.CATEGORIES:
        if label == "Mail":
            continue  # Mail has no screen and no descriptor row
        key = da.CATEGORY_KEYS[label]
        mimes_join = " ".join(mimes)
        seed_join = " ".join(da.CANDIDATES[label])
        # match `{"key", "field2", "field3"}` -- the exact AZ_DA_CATS row for this key.
        m = re.search(
            r'\{\s*"' + re.escape(key) + r'"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\}',
            model)
        assert m is not None, f"AZ_DA_CATS row for key {key!r} not found in model"
        c_mimes, c_seed = m.group(1), m.group(2)
        assert c_mimes == mimes_join, \
            f"AZ_DA_CATS MIME list for {key}: C {c_mimes!r} != source {mimes_join!r}"
        assert c_seed == seed_join, \
            f"AZ_DA_CATS seed for {key}: C {c_seed!r} != source {seed_join!r}"


def test_model_c_discloses_single_desktop_dir_matching_source():
    # Both the list screen and the runtime per-category screen disclose the ONE place a user
    # drops their own .desktop (like the Wallpaper screen discloses its directory). The user
    # rejected the old three-path disclosure ("copy into three places"): it is now the SINGLE
    # user-writable dir, the AZ_DA_DIRS_LINE macro DEFINED in terminal_user_interface.h (shared by
    # model_tree.c's list screen and model_default_applications.c's per-category screens); its
    # literal must equal default_applications.DESKTOP_DIR_DISPLAY exactly.
    header = (paths.LIBDIR / "packages/azzio/terminal_user_interface.h").read_text(encoding="utf-8")
    assert f'#define AZ_DA_DIRS_LINE "{da.DESKTOP_DIR_DISPLAY}"' in header, \
        f"AZ_DA_DIRS_LINE (in the header) must be the single path {da.DESKTOP_DIR_DISPLAY!r}"
    # exactly one path -> the system dirs must NOT appear in the disclosed macro line.
    assert "/usr/share/applications" not in da.DESKTOP_DIR_DISPLAY
    assert "," not in da.DESKTOP_DIR_DISPLAY
    # the model USES the macro (both the list screen subtitle and the per-category disclosure).
    model = _model_c()
    assert "AZ_DA_DIRS_LINE" in model
    # the per-category runtime screen (model_default_applications.c) discloses the dir TERSELY --
    # the user pushed back on the wordy "To add or override an app, drop its .desktop into ...
    # (the list below resolves ...)" line: it is now just ".desktop directory: <dir>/" (trailing
    # slash, nothing else). Guard the exact terse literal AND that the old prose is gone from it.
    per_category = (paths.LIBDIR / "packages/azzio/model_default_applications.c").read_text(encoding="utf-8")
    assert '".desktop directory: " AZ_DA_DIRS_LINE "/"' in per_category, \
        "per-category screen must disclose the dir as terse '.desktop directory: <dir>/'"
    assert "drop its .desktop into" not in per_category, \
        "the wordy add/override prose must be gone from the per-category screen"
    assert "list below resolves" not in per_category
