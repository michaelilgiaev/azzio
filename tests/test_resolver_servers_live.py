"""LIVE-NETWORK contract test for the five IP-geolocation servers azzio offers.

Unlike the rest of the suite (pure, offline -- see tests.sh), this file actually
PINGS each server in RESOLVER_SERVERS and proves that the (url, country_path,
timezone_path) tuple still matches the server's real JSON shape. This is the exact
class of regression that shipped once: ipwho.is changed `timezone` from a string to
an object ({"id": ...}), so the old "timezone" path dug out a dict and apply_timezone
died with "unknown timezone {...}". A path that no longer resolves to a flat string
FAILS here, loudly, before a user ever hits it.

Marked `network`; it SKIPS itself when the host is offline so the default offline
`bash tests.sh` stays green. Run the network tier explicitly with:
    bash tests.sh -m network
or exclude it with `-m 'not network'`.
"""

from __future__ import annotations

import json
import re
import socket
import types
import urllib.request

import pytest

pytestmark = pytest.mark.network

# A plausible IANA zone: "Area/Location" (optionally "Area/Sub/Location"), or the
# bare special zone "UTC". This is a shape check, not an exhaustive tz database.
_IANA_RE = re.compile(r"^(UTC|[A-Za-z]+(?:/[A-Za-z0-9._+-]+)+)$")

# How long to wait per server before treating it as unreachable (skip, not fail --
# a flaky/rate-limited host is not a code bug).
_TIMEOUT = 15


def _load_cli() -> types.ModuleType:
    """Exec the shipped, bundled azzio guest CLI so we test the REAL RESOLVER_SERVERS
    list + _dig the guest runs -- identical to _load_azzio_command_line_interface in
    test_configuration_openbox.py, duplicated here to keep this network tier standalone."""
    from packages.azzio.bundle import bundle_source

    mod = types.ModuleType("azzio_guest_command_line_interface_live")
    exec(compile(bundle_source(), "azzio_guest_command_line_interface_live", "exec"),
         mod.__dict__)
    return mod


def _online() -> bool:
    """Cheap reachability probe so this whole module skips fast when offline (no per-
    server 15s timeouts on a plane). A single TCP connect to a public resolver:53."""
    try:
        socket.setdefaulttimeout(5)
        socket.create_connection(("1.1.1.1", 53)).close()
        return True
    except OSError:
        return False


def _servers():
    return list(_load_cli().RESOLVER_SERVERS)


def _fetch(url: str):
    """GET + parse JSON, or None if the host is unreachable/slow/non-JSON (skip-worthy,
    not a code failure). A real request so we validate the LIVE response shape."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "azzio-test/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


@pytest.mark.parametrize("server", _servers(), ids=lambda s: s[0])
def test_live_server_country_and_timezone_paths_resolve(server):
    """For each configured server: ping it, then prove its own country_path and
    timezone_path _dig out a non-empty country string and a FLAT IANA timezone string.
    This is what catches a server silently changing its JSON layout."""
    if not _online():
        pytest.skip("offline: live resolver-server contract test needs network")

    label, url, cpath, tpath = server
    cli = _load_cli()

    payload = _fetch(url)
    if payload is None:
        pytest.skip(f"{label} unreachable / not JSON right now (network flake, not a code bug)")
    assert isinstance(payload, dict), f"{label}: response is not a JSON object: {type(payload)}"

    # Country: the configured path must dig out a non-empty scalar (str) -- what
    # resolve_via_server uppercases into the country code.
    country = cli._dig(payload, cpath)
    assert country is not None, f"{label}: country_path {cpath!r} missing from response {payload!r}"
    assert isinstance(country, str) and country.strip(), \
        f"{label}: country_path {cpath!r} is not a non-empty string: {country!r}"

    # Timezone: the configured path must dig out a FLAT string, never a dict/list. THIS
    # is the ipwho.is-shape guard: if a server nests its zone under an object, the path
    # in RESOLVER_SERVERS must point at the string leaf (e.g. "timezone.id").
    tz = cli._dig(payload, tpath)
    assert tz is not None, f"{label}: timezone_path {tpath!r} missing from response {payload!r}"
    assert isinstance(tz, str), \
        (f"{label}: timezone_path {tpath!r} resolved to a {type(tz).__name__}, not a string "
         f"({tz!r}); the path must point at the flat IANA zone leaf")
    assert _IANA_RE.match(tz), f"{label}: timezone {tz!r} is not a plausible IANA zone"


def test_live_resolve_via_server_returns_flat_tuple_for_each_server(monkeypatch):
    """End-to-end: drive the REAL resolve_via_server(choice=N) against each live server
    (non-interactive pick) and assert it returns (COUNTRY, TIMEZONE) as two flat strings.
    Exercises the whole path -- fetch, _dig both fields, uppercase, flatten -- the way a
    user's `azzio timedate --resolve` does, for every server, over the real network."""
    if not _online():
        pytest.skip("offline: live resolver-server contract test needs network")

    cli = _load_cli()
    # Non-interactive pick must never read stdin.
    monkeypatch.setattr("builtins.input",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no stdin in --server mode")))

    # Call resolve_via_server directly (its ONE fetch) -- no separate pre-probe, which
    # would double each host's rate-limit exposure and flake. A None here means that
    # server was unreachable/rate-limited on THIS run (skip it); a non-None result must
    # satisfy the flat (UPPER country, IANA tz) contract. At least one must succeed.
    checked = 0
    for i, (label, _url, _cp, _tp) in enumerate(cli.RESOLVER_SERVERS, 1):
        result = cli.resolve_via_server(choice=str(i))
        if result is None:
            continue  # rate-limited / down right now -> the other servers still prove the contract
        country, tz = result
        assert isinstance(country, str) and country.isupper() and country, \
            f"{label}: country not an uppercase string: {country!r}"
        assert isinstance(tz, str) and _IANA_RE.match(tz), \
            f"{label}: timezone not a flat IANA string: {tz!r}"
        checked += 1

    if checked == 0:
        pytest.skip("no resolver server reachable right now (all rate-limited / down)")
