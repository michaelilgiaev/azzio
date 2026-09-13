"""packages.fastfetch -- the fastfetch configuration + Az' logo.

The configuration's `source` MUST be an absolute path: fastfetch resolves a relative
source against the CWD, so a bare filename silently falls back to the stock Arch
logo (the documented bug this module exists to prevent).
"""

from __future__ import annotations

import json

from packages import fastfetch


def test_config_jsonc_is_valid_json():
    # ".jsonc" but this file carries no comments/trailing commas -> parseable as JSON.
    data = json.loads(fastfetch.config_jsonc())
    assert data["logo"]["type"] == "file-raw"


def test_logo_source_is_absolute_path():
    data = json.loads(fastfetch.config_jsonc())
    src = data["logo"]["source"]
    assert src.startswith("/"), src
    assert src == fastfetch.LOGO_PATH


def test_logo_path_matches_filename_constant():
    assert fastfetch.LOGO_PATH.endswith("/" + fastfetch.LOGO_FILENAME)


def test_logo_txt_reads_the_repo_asset():
    # Verbatim read of the pre-colored .ansi asset; it exists and is non-empty.
    art = fastfetch.logo_txt()
    assert art.strip() != ""


def _module_types(data):
    # Modules are either a bare string ("title") or an object ({"type": "os", ...}).
    return [m if isinstance(m, str) else m["type"] for m in data["modules"]]


def test_config_includes_expected_modules():
    data = json.loads(fastfetch.config_jsonc())
    types = _module_types(data)
    for mod in ("title", "os", "kernel", "packages"):
        assert mod in types


def test_os_module_prints_azzio_without_linux_suffix():
    # os-release NAME is "Azzio Linux"; the fastfetch os line hard-codes "Azzio"
    # so the display reads "OS: Azzio x86_64" (not "Azzio Linux x86_64"), while the
    # {arch} placeholder still reports the real architecture live.
    data = json.loads(fastfetch.config_jsonc())
    os_mod = next(m for m in data["modules"] if isinstance(m, dict) and m["type"] == "os")
    assert os_mod["format"] == "Azzio {arch}"
    assert "Linux" not in os_mod["format"]
