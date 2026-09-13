"""estimate -- the --estimate* heuristics (six flags, two tiers, three modes).

Everything here is PURE: the one function that touches the network (measure_mbps)
is NEVER called live -- tests inject a bandwidth or monkeypatch measure_mbps, so
the suite stays fully offline (the tests.sh contract). We assert:
  * the flag -> (tier, mode) parse table,
  * the per-tier compute anchors (default << full, non-overlapping on any machine),
  * the download-size model (monotonic full > default, scales with the manifest),
  * the size/bandwidth -> time math and the graceful offline degradation,
  * the formatters, and
  * run() returns 0 for every mode and prints only what the mode asks for.
estimate_compute()/estimate() read /proc for the real machine, so we assert their
structural invariants (a valid range, all keys present) rather than exact numbers.
"""

from __future__ import annotations

import pytest

import estimate


# --- _fmt_hours (unchanged, kept as regression guards) ----------------------
@pytest.mark.parametrize("hours,expected", [
    (0.25, "15 min"),
    (0.5, "30 min"),
    (0.99, "59 min"),
    (1.0, "1h"),
    (2.0, "2h"),
    (2.5, "2h 30m"),
    (1.75, "1h 45m"),
])
def test_fmt_hours(hours, expected):
    assert estimate._fmt_hours(hours) == expected


def test_fmt_hours_sub_hour_uses_minutes():
    assert estimate._fmt_hours(0.1).endswith("min")


def test_fmt_hours_whole_hour_has_no_minutes():
    assert estimate._fmt_hours(3.0) == "3h"


# --- estimate() backward-compat shim + estimate_compute() -------------------
def test_estimate_returns_a_valid_range():
    e = estimate.estimate()
    assert e["low_hours"] < e["high_hours"]
    assert e["low_hours"] > 0


def test_estimate_has_all_keys():
    e = estimate.estimate()
    for k in ("cores", "model", "mhz", "ram_gb", "low_hours", "high_hours", "ram_warning"):
        assert k in e


def test_estimate_cores_is_positive():
    assert estimate.estimate()["cores"] >= 1


def test_estimate_shim_equals_full_structurally():
    # The pre-6-flag estimate() was full-tier compute; keep it that way.
    assert set(estimate.estimate().keys()) == set(estimate.estimate_compute("full").keys())


def test_estimate_compute_has_tier_key():
    assert estimate.estimate_compute("full")["tier"] == "full"
    assert estimate.estimate_compute("default")["tier"] == "default"


# --- flag -> (tier, mode) parser --------------------------------------------
@pytest.mark.parametrize("flag,expected", [
    ("--estimate", ("default", "both")),
    ("--estimate-only-compute", ("default", "compute")),
    ("--estimate-only-network", ("default", "network")),
    ("--estimate-full-compile", ("full", "both")),
    ("--estimate-full-compile-only-compute", ("full", "compute")),
    ("--estimate-full-compile-only-network", ("full", "network")),
])
def test_parse_flag(flag, expected):
    assert estimate.parse_estimate_flag([flag]) == expected


def test_parse_flag_none_when_absent():
    assert estimate.parse_estimate_flag(["--full-compile"]) is None
    assert estimate.parse_estimate_flag([]) is None


def test_parse_flag_longest_match_not_shadowed():
    # --estimate is a substring of --estimate-full-compile-only-compute; the exact
    # match must win, not the shorter flag.
    assert estimate.parse_estimate_flag(
        ["--estimate-full-compile-only-compute"]) == ("full", "compute")


def test_parse_flag_unknown_estimate_falls_back():
    assert estimate.parse_estimate_flag(["--estimate-xyz"]) == ("default", "both")


def test_parse_flag_first_recognized_wins():
    assert estimate.parse_estimate_flag(
        ["--estimate-only-network", "--estimate"]) == ("default", "network")


def test_parse_flag_ignores_surrounding_args():
    assert estimate.parse_estimate_flag(["-x", "--estimate", "-q"]) == ("default", "both")


def test_flag_map_has_six():
    assert len(estimate.FLAG_MAP) == 6


# --- compute anchors per tier -----------------------------------------------
def test_base_core_hours_ordering():
    assert estimate.BASE_CORE_HOURS_DEFAULT < estimate.BASE_CORE_HOURS_FULL


def test_default_compute_ranges_below_full_same_machine():
    # The default tier compiles only calamares (minutes); the full tier compiles
    # librewolf/Firefox (hours). Same eff_cores divides both anchors, so the ranges
    # never overlap on ANY machine.
    d = estimate.estimate_compute("default")
    f = estimate.estimate_compute("full")
    assert d["high_hours"] < f["low_hours"]


def test_default_tier_no_ram_warning(monkeypatch):
    # Firefox (full tier) is memory-bound; calamares (default tier) is not. A RAM-
    # starved machine warns only for full.
    monkeypatch.setattr(estimate, "_total_ram_gb", lambda: 4.0)
    assert estimate.estimate_compute("default")["ram_warning"] is False
    assert estimate.estimate_compute("full")["ram_warning"] is True


def test_full_tier_no_warning_with_ample_ram(monkeypatch):
    monkeypatch.setattr(estimate, "_total_ram_gb", lambda: 64.0)
    assert estimate.estimate_compute("full")["ram_warning"] is False


# --- download-size model ----------------------------------------------------
def test_download_size_full_exceeds_default():
    assert estimate.download_size_bytes("full") > estimate.download_size_bytes("default")


def test_download_size_diff_is_firefox_minus_librewolf():
    assert (estimate.download_size_bytes("full") - estimate.download_size_bytes("default")
            == estimate.FIREFOX_SRC_BYTES - estimate.LIBREWOLF_BIN_BYTES)


def test_manifest_count_matches_packages_parse():
    # The size model must parse packages.x86_64 the SAME way packages.py's download
    # step does, or the two silently diverge.
    n = sum(1 for line in estimate.paths.PACKAGES_FILE.read_text().splitlines()
            if line.split("#", 1)[0].strip())
    assert estimate._manifest_pkg_count() == n


def test_estimated_pkg_count_has_floor(tmp_path):
    tiny = tmp_path / "p"
    tiny.write_text("base\nlinux\n")
    assert estimate.estimated_pkg_count(tiny) == estimate.MIN_PKG_COUNT


def test_download_size_scales_with_manifest(tmp_path):
    small = tmp_path / "s"
    small.write_text("\n".join(f"p{i}" for i in range(500)))
    big = tmp_path / "b"
    big.write_text("\n".join(f"p{i}" for i in range(5000)))
    assert (estimate.download_size_bytes("default", big)
            > estimate.download_size_bytes("default", small))


def test_real_manifest_closure_in_ballpark():
    # ~224 explicit entries * 5.0 transitive factor, floored -> ~1120 (~1200).
    assert 1000 <= estimate.estimated_pkg_count() <= 1500


# --- download-time + network estimate (injected bandwidth, no socket) -------
def test_download_seconds_math():
    assert estimate.download_seconds(1_000_000_000, 8.0) == pytest.approx(1000.0)


def test_download_seconds_zero_bw_is_inf():
    assert estimate.download_seconds(1, 0.0) == float("inf")


def test_estimate_network_injected_ranges():
    n = estimate.estimate_network("full", mbps=100.0)
    assert n["offline"] is False
    assert n["low_seconds"] < n["high_seconds"]   # 0.9x vs 0.5x band
    assert n["size_bytes"] == estimate.download_size_bytes("full")


def test_estimate_network_offline_when_probe_none(monkeypatch):
    monkeypatch.setattr(estimate, "measure_mbps", lambda: None)
    n = estimate.estimate_network("default")
    assert n["offline"] is True
    assert n["low_seconds"] is None and n["high_seconds"] is None


# --- formatters -------------------------------------------------------------
def test_fmt_bytes_units():
    assert estimate._fmt_bytes(85_000_000).endswith("MB")
    assert estimate._fmt_bytes(4_700_000_000).endswith("GB")


@pytest.mark.parametrize("secs,frag", [(5, "s"), (120, "min"), (3600, "h")])
def test_fmt_seconds(secs, frag):
    assert frag in estimate._fmt_seconds(secs)


def test_fmt_seconds_delegates_hours():
    assert estimate._fmt_seconds(3600) == estimate._fmt_hours(1.0)


def test_fmt_mbps():
    assert "Mbit/s" in estimate._fmt_mbps(241.0)


# --- run(): one path per mode, always exit 0, print only what mode asks ------
def test_run_full_both_offline(monkeypatch, capsys):
    monkeypatch.setattr(estimate, "measure_mbps", lambda: None)
    assert estimate.run(["--estimate-full-compile"]) == 0
    out = capsys.readouterr().out.lower()
    assert "estimate-full-compile" in out
    assert "no reachable" in out or "no network" in out


def test_run_compute_only_never_probes(monkeypatch):
    # A compute-only mode must not open a socket: make measure_mbps explode if called.
    def boom():
        raise AssertionError("compute-only mode probed the network")
    monkeypatch.setattr(estimate, "measure_mbps", boom)
    assert estimate.run(["--estimate-only-compute"]) == 0
    assert estimate.run(["--estimate-full-compile-only-compute"]) == 0


def test_run_network_only_omits_cpu(monkeypatch, capsys):
    monkeypatch.setattr(estimate, "measure_mbps", lambda: 300.0)
    assert estimate.run(["--estimate-only-network"]) == 0
    out = capsys.readouterr().out
    assert "Mbit/s" in out
    assert "CPU" not in out   # network-only mode reads no /proc, prints no CPU line


def test_run_all_six_flags_return_zero(monkeypatch):
    monkeypatch.setattr(estimate, "measure_mbps", lambda: 50.0)
    for flag in estimate.FLAG_MAP:
        assert estimate.run([flag]) == 0


def test_run_both_prints_total(monkeypatch, capsys):
    monkeypatch.setattr(estimate, "measure_mbps", lambda: 100.0)
    assert estimate.run(["--estimate"]) == 0
    assert "total" in capsys.readouterr().out.lower()
