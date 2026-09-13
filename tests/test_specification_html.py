r"""specification_html -- the single self-contained interactive HTML page generator.

The Python half of this module is small but load-bearing: it turns the resolved
dependency graph into the JSON payload the page's JavaScript reads, and it splices
that JSON into a static HTML template. Three behaviours here can silently corrupt
the page and nothing downstream would catch it:

  * ``_fmt_size`` -- the human-readable installed-size string shown on every box
    and in the glance strip. It has three magnitude branches with DIFFERENT
    precision (GiB=2dp, MiB=1dp, KiB=0dp, bytes as-is). A wrong branch boundary or
    precision changes every size on the page. The branch cutoffs are exact powers
    of 1024 and the ``:.0f``/``:.1f`` rounding means values just under a boundary
    still print in the LOWER unit (e.g. a hair under 1 MiB prints as "1024 KiB",
    a hair under 1 GiB as "1024.0 MiB"), so the boundaries matter to the byte.

  * ``</...>`` escaping in ``render_html`` -- the payload is embedded inside a
    ``<script type="application/json">`` element. A literal ``</`` sequence in any
    package description or URL would end that element early and break the whole
    page. ``render_html`` rewrites every ``</`` in the data to ``<\/`` so the byte
    stream can never close the tag; the JS un-does it at parse time. We assert the
    real ``</script>`` a browser stops at is the template's own closing tag, never
    one smuggled in through the data.

  * glance camelCase re-key -- ``_build_payload`` copies the caller's snake_case
    ``glance`` dict into a fixed camelCase-keyed object the JS indexes by literal
    name (``byRepo``, ``maxHeight``, ``isoVersion`` ...). A dropped or misspelled
    key here shows blank facts in the header with no error. We pin the exact key
    set and that each value is carried through verbatim.

Everything here is pure: we hand-build tiny ``packages``/``resolved``/``tiers``/
``tags``/``glance`` dicts (the same shapes the real pipeline passes) so there is
no network, no filesystem, and no dependency on the real package database.
"""

from __future__ import annotations

import json

import specification_resolve as R
import specification_html


# --------------------------------------------------------------------------- #
# _fmt_size: branch boundaries + per-branch precision
# --------------------------------------------------------------------------- #

def test_fmt_size_bytes_below_kib_are_verbatim():
    # Under 1024: raw integer + " B", no scaling, no decimals.
    assert specification_html._fmt_size(0) == "0 B"
    assert specification_html._fmt_size(1) == "1 B"
    assert specification_html._fmt_size(500) == "500 B"


def test_fmt_size_last_byte_before_kib_boundary():
    # 1023 is the largest value still in the bytes branch (>= 1024 flips to KiB).
    assert specification_html._fmt_size(1023) == "1023 B"


def test_fmt_size_kib_uses_zero_decimals():
    # KiB branch formats with :.0f -- no decimal point at all.
    assert specification_html._fmt_size(1024) == "1 KiB"


def test_fmt_size_kib_rounds_down_within_branch():
    # 1500 B = 1.46 KiB; :.0f rounds to 1, so it prints "1 KiB", not "1.5 KiB".
    assert specification_html._fmt_size(1500) == "1 KiB"


def test_fmt_size_just_below_mib_stays_in_kib_as_1024():
    # 1 MiB - 1 byte is still < 1024**2, so it takes the KiB branch and, with
    # :.0f, rounds to the full 1024 KiB rather than promoting to "1.0 MiB".
    assert specification_html._fmt_size(1024 ** 2 - 1) == "1024 KiB"


def test_fmt_size_mib_uses_one_decimal():
    # Exactly 1 MiB: MiB branch, :.1f => one decimal place.
    assert specification_html._fmt_size(1024 ** 2) == "1.0 MiB"


def test_fmt_size_mib_one_decimal_value():
    assert specification_html._fmt_size(1024 ** 2 + 512 * 1024) == "1.5 MiB"


def test_fmt_size_just_below_gib_stays_in_mib_as_1024():
    # 1 GiB - 1 byte is still < 1024**3, so MiB branch with :.1f => "1024.0 MiB",
    # NOT "1.00 GiB". This is the boundary the GiB branch's >= guards.
    assert specification_html._fmt_size(1024 ** 3 - 1) == "1024.0 MiB"


def test_fmt_size_gib_uses_two_decimals():
    # Exactly 1 GiB: GiB branch, :.2f => two decimal places.
    assert specification_html._fmt_size(1024 ** 3) == "1.00 GiB"


def test_fmt_size_gib_two_decimal_value():
    assert specification_html._fmt_size(int(1.5 * 1024 ** 3)) == "1.50 GiB"
    assert specification_html._fmt_size(5 * 1024 ** 3) == "5.00 GiB"


# --------------------------------------------------------------------------- #
# Small end-to-end fixture. app depends on lib; one manifest entry ("app").
# --------------------------------------------------------------------------- #

def _fixture(*, app_desc="app desc", app_url="http://example/app"):
    resolved = {
        "manifest_map": {"app": {"kind": "package", "resolved": "app"}},
        "roots": {"app"},
        "closure": {"app", "lib"},
        "edges": {"app": {"lib"}, "lib": set()},
    }
    packages = {
        "app": {
            "version": "1.0-1",
            "repo": "extra",
            "desc": app_desc,
            "url": app_url,
            "isize": 2 * 1024 ** 3,
            "license": "GPL",
            "optdepends": [],
        },
        "lib": {
            "version": "2.0-1",
            "repo": "core",
            "desc": "lib desc",
            "url": "",
            "isize": 1024,
            "license": "",
            "optdepends": [],
        },
    }
    tiers = {
        "heights": {"app": 1, "lib": 0},
        "rev": {"lib": {"app"}},
        "trans_deps": {"app": 1, "lib": 0},
        "trans_dependents": {"app": 0, "lib": 1},
    }
    tags = {
        "app": {
            "edition": "azzio",
            "category": "System",
            "azzio_note": "note",
            "removed": False,
        },
        "lib": {"edition": "stock", "category": "Shared library"},
    }
    glance = {
        "base": "B",
        "desktop": "D",
        "kernel": "6.9",
        "init": "256",
        "ram": "2 GiB",
        "closure": 2,
        "by_repo": {"core": 1, "extra": 1},
        "azzio": 1,
        "stock": 1,
        "max_height": 1,
        "size": "2.00 GiB",
        "iso_version": "v1.2.3",
    }
    return packages, resolved, tiers, tags, glance


def _payload(**kw):
    packages, resolved, tiers, tags, glance = _fixture(**kw)
    attr = R.attribute_entries(resolved)
    return specification_html._build_payload(packages, resolved, tiers, tags, glance, attr)


# --------------------------------------------------------------------------- #
# glance camelCase re-key
# --------------------------------------------------------------------------- #

def test_glance_has_exact_camelcase_key_set():
    # The JS indexes glance by these literal names; a missing/extra key shows a
    # blank fact silently. The re-key drops the snake_case originals entirely.
    g = _payload()["glance"]
    assert set(g) == {
        "base", "desktop", "kernel", "init", "ram", "closure",
        "byRepo", "azzio", "stock", "maxHeight", "size", "isoVersion",
    }


def test_glance_snake_case_source_keys_are_renamed():
    # by_repo/max_height/iso_version must NOT survive under their source spelling.
    g = _payload()["glance"]
    assert "by_repo" not in g
    assert "max_height" not in g
    assert "iso_version" not in g


def test_glance_values_carried_through_verbatim():
    g = _payload()["glance"]
    assert g["byRepo"] == {"core": 1, "extra": 1}
    assert g["maxHeight"] == 1
    assert g["isoVersion"] == "v1.2.3"
    assert g["kernel"] == "6.9"
    assert g["init"] == "256"
    assert g["closure"] == 2
    assert g["azzio"] == 1
    assert g["stock"] == 1
    assert g["size"] == "2.00 GiB"


# --------------------------------------------------------------------------- #
# _build_payload: component sizes come through _fmt_size
# --------------------------------------------------------------------------- #

def test_component_size_strings_use_fmt_size():
    comps = _payload()["components"]
    # app.isize == 2 GiB, lib.isize == 1024 B == 1 KiB.
    assert comps["app"]["size"] == "2.00 GiB"
    assert comps["lib"]["size"] == "1 KiB"
    # raw isize preserved as an int alongside the formatted string.
    assert comps["app"]["isize"] == 2 * 1024 ** 3
    assert comps["lib"]["isize"] == 1024


def test_component_edition_label_lookup():
    comps = _payload()["components"]
    assert comps["app"]["editionLabel"] == "Azzio Component"
    assert comps["lib"]["editionLabel"] == "Stock Arch"


def test_missing_desc_gets_placeholder():
    # rec.get("desc") or <placeholder>: empty description falls back to the note.
    p = _payload(app_desc="")["components"]["app"]
    assert p["desc"] == "(no description in the Arch package DB)"


# --------------------------------------------------------------------------- #
# render_html: </...> escaping keeps the JSON inside its <script> element
# --------------------------------------------------------------------------- #

def _render(**kw):
    packages, resolved, tiers, tags, glance = _fixture(**kw)
    return specification_html.render_html(packages, resolved, tiers, tags, glance)


def _injected_data(html_out):
    """Return the raw bytes between the data <script> open tag and the FIRST
    following ``</script>`` -- i.e. exactly what a browser would treat as the
    element's text content."""
    open_tag = 'type="application/json">'
    start = html_out.index(open_tag) + len(open_tag)
    end = html_out.index("</script>", start)
    return html_out[start:end]


def test_data_marker_is_replaced():
    out = _render()
    # The literal template placeholder must be gone once data is spliced in.
    assert "/*__DATA__*/" not in out


def test_script_close_in_desc_is_escaped_not_raw():
    # A description containing </script> must not appear raw inside the data block;
    # it is rewritten to <\/script> so it cannot close the element early.
    out = _render(app_desc="danger </script> here")
    data = _injected_data(out)
    assert "</script>" not in data
    assert "<\\/script>" in data


def test_escaped_data_round_trips_back_to_original():
    # Undoing the <\/ -> </ substitution and JSON-parsing must recover the exact
    # original description, proving the escape is lossless (the JS does the same).
    out = _render(app_desc="danger </script> here")
    data = _injected_data(out)
    recovered = json.loads(data.replace("<\\/", "</"))
    assert recovered["components"]["app"]["desc"] == "danger </script> here"


def test_escaping_covers_any_close_sequence_in_urls():
    # The replace targets "</" generally, not just "</script>": a </b> smuggled
    # through a URL is escaped too, so no HTML close sequence survives in the data.
    out = _render(app_url="http://x/</b>y")
    data = _injected_data(out)
    assert "</b>" not in data
    assert "<\\/b>" in data


def test_data_json_parses_after_unescaping():
    # The whole injected block must be valid JSON once un-escaped: catches any
    # stray corruption from the string surgery.
    out = _render()
    data = _injected_data(out)
    obj = json.loads(data.replace("<\\/", "</"))
    assert set(obj) >= {"components", "order", "glance", "entries", "layers"}
    assert obj["order"] == ["app", "lib"]
