"""`compile.sh --estimate*`: predict how long a build would take on THIS machine
WITHOUT building anything -- both the COMPUTE time (compiling on this CPU/RAM) and
the NETWORK time (downloading the components over this connection).

There are two tiers and three modes, giving six flags:

  --estimate                            default tier, compute AND network
  --estimate-only-compute               default tier, compute only
  --estimate-only-network               default tier, network only
  --estimate-full-compile               full tier,    compute AND network
  --estimate-full-compile-only-compute  full tier,    compute only
  --estimate-full-compile-only-network  full tier,    network only

TIERS differ in what actually gets compiled/downloaded:
  * default -- calamares is compiled from source (a moderate C++/CMake build,
               order of minutes) and librewolf is REPACKAGED from a verified
               ~85 MB prebuilt tarball (no compile).
  * full    -- calamares from source PLUS librewolf compiled from Firefox source
               (a huge C++/Rust build: 1.5-3+ hours, wants ~16 GB RAM). That
               compile dominates the wall-clock and is almost perfectly parallel,
               so it scales inversely with usable core count, with RAM as a hard
               floor (Firefox linking swaps on a RAM-starved machine).

COMPUTE is a HEURISTIC, not a benchmark: it reads core count, CPU model/clock and
total RAM and prints a rough range. NETWORK runs a REAL, timeout-bounded bandwidth
probe against an Arch package mirror (the same CDN the real build pulls from) and
divides the tier's estimated download size by the measured throughput. Neither
touches sudo, resets the workspace, nor starts a build. The only I/O the network
mode does is a few-second, size-capped HTTP GET that can never hang.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import paths

# --- flag -> (tier, mode) dispatch -----------------------------------------
# tier in {"default", "full"}; mode in {"compute", "network", "both"}.
FLAG_MAP: dict[str, tuple[str, str]] = {
    "--estimate":                           ("default", "both"),
    "--estimate-only-compute":              ("default", "compute"),
    "--estimate-only-network":              ("default", "network"),
    "--estimate-full-compile":              ("full",    "both"),
    "--estimate-full-compile-only-compute": ("full",    "compute"),
    "--estimate-full-compile-only-network": ("full",    "network"),
}


def parse_estimate_flag(argv: list[str]) -> tuple[str, str] | None:
    """First recognized estimate flag in argv -> (tier, mode), else None. Pure.

    The six flags are DISTINCT exact strings, but "--estimate" is a substring of
    "--estimate-full-compile", so we match by exact `in FLAG_MAP` membership (not
    startswith) -- an exact match cannot mistake one flag for another. If argv
    holds some other "--estimate..." string that is NOT one of the six, fall back
    to ("default", "both"): compile.sh has already committed to the no-sudo/no-PTY
    query path for anything starting "--estimate", so a real build is impossible
    here; a best-effort estimate is the only safe landing.
    """
    for a in argv:
        if a in FLAG_MAP:
            return FLAG_MAP[a]
    for a in argv:
        if a.startswith("--estimate"):
            return ("default", "both")
    return None


# --- compute heuristic ------------------------------------------------------
# Anchors are total single-core-equivalent hours of compile work for the tier.
# full: a from-source LibreWolf/Firefox build (plus calamares) is ~16 core-hours,
# so a fast 8-core machine lands around ~2h. default: ONLY calamares is compiled
# (a Qt6/KF6 CMake project -- configure + compile + link of a moderate C++
# codebase); ~0.35 core-hours = 21 core-minutes, so the same 8-core box lands
# around ~2.6 min. librewolf is repackaged in the default tier, so it adds no
# compile time. We spread the anchor across effective cores, then widen to a
# range for machine-specific factors (clock, memory bandwidth, disk, thermal).
BASE_CORE_HOURS_FULL = 16.0     # calamares + librewolf/Firefox from source
BASE_CORE_HOURS_DEFAULT = 0.35  # calamares only (librewolf is repackaged)
MIN_RAM_GB_COMFORTABLE = 16.0   # below this, the Firefox link/codegen swaps


def _base_core_hours(tier: str) -> float:
    return BASE_CORE_HOURS_FULL if tier == "full" else BASE_CORE_HOURS_DEFAULT


def _cores() -> int:
    # Prefer the count actually usable by this process (respects cgroup/affinity
    # limits, e.g. inside Docker), fall back to the machine total.
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def _cpu_model_and_mhz() -> tuple[str, float]:
    model, mhz = "unknown CPU", 0.0
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name") and model == "unknown CPU":
                model = line.split(":", 1)[1].strip()
            elif line.startswith("cpu MHz") and mhz == 0.0:
                try:
                    mhz = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
    except OSError:
        pass
    return model, mhz


def _total_ram_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                kb = float(line.split()[1])
                return kb / (1024.0 * 1024.0)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def estimate_compute(tier: str) -> dict:
    cores = _cores()
    model, mhz = _cpu_model_and_mhz()
    ram = _total_ram_gb()

    # Effective parallelism: compilation scales well but not perfectly (I/O, link
    # serialization, Amdahl). Model diminishing returns past ~8 cores.
    eff_cores = cores if cores <= 8 else 8 + (cores - 8) * 0.6
    wall_hours = _base_core_hours(tier) / max(eff_cores, 1.0)

    # RAM penalty: ONLY the full tier is memory-bound (Firefox links under memory
    # pressure and swaps below the comfortable floor). The default tier compiles
    # only calamares, which does not pressure memory, so it is RAM-independent and
    # never carries the penalty or the swap warning.
    ram_low = tier == "full" and bool(ram) and ram < MIN_RAM_GB_COMFORTABLE
    if ram_low:
        wall_hours *= 1.0 + (MIN_RAM_GB_COMFORTABLE - ram) / MIN_RAM_GB_COMFORTABLE

    low = wall_hours * 0.7
    high = wall_hours * 1.5
    return {
        "tier": tier, "cores": cores, "model": model, "mhz": mhz, "ram_gb": ram,
        "low_hours": low, "high_hours": high, "ram_warning": bool(ram_low),
    }


def estimate() -> dict:
    """Backward-compatible shim: the pre-6-flag estimate() was full-tier compute.
    Kept so existing callers/tests that call estimate() with no args still work."""
    return estimate_compute("full")


# --- network: download-size model -------------------------------------------
# packages.x86_64 lists the EXPLICIT top-level packages; `pacman -Sw` downloads
# the full transitive dependency CLOSURE, which is what actually transfers. The
# manifest carries ~224 explicit entries; the closure a full headed ISO resolves
# is ~1200, so we scale the explicit count by TRANSITIVE_FACTOR (floored so an
# edited/short manifest never under-estimates), times an average compressed
# package size. Both tiers download the same Arch package closure plus the
# calamares source tarball; the tier-specific extra is librewolf (default: an
# ~85 MB prebuilt binary; full: the bsys6 git tree + the Firefox source that
# `make fetch` pulls, ~750 MB).
TRANSITIVE_FACTOR = 5.0          # 224 explicit -> ~1120 closure (~1200 ballpark)
MIN_PKG_COUNT = 1000             # floor: a trimmed manifest never underestimates
AVG_PKG_BYTES = 3_500_000        # ~3.5 MB avg compressed .pkg.tar.zst (desktop mix)
CALAMARES_SRC_BYTES = 8_000_000  # calamares source tarball, both tiers
LIBREWOLF_BIN_BYTES = 85_000_000   # verified upstream librewolf binary tarball + sig
FIREFOX_SRC_BYTES = 750_000_000    # bsys6 git + Firefox source pulled by `make fetch`


def _manifest_pkg_count(packages_file: Path = paths.PACKAGES_FILE) -> int:
    """Count real package entries, parsed IDENTICALLY to packages.py's download
    step (line.split('#', 1)[0].strip()) so the two never diverge."""
    try:
        return sum(1 for line in packages_file.read_text().splitlines()
                   if line.split("#", 1)[0].strip())
    except OSError:
        return 0


def estimated_pkg_count(packages_file: Path = paths.PACKAGES_FILE) -> int:
    return max(round(_manifest_pkg_count(packages_file) * TRANSITIVE_FACTOR),
               MIN_PKG_COUNT)


def download_size_bytes(tier: str, packages_file: Path = paths.PACKAGES_FILE) -> int:
    """Estimated total bytes downloaded for a first (online) build of this tier."""
    shared = estimated_pkg_count(packages_file) * AVG_PKG_BYTES + CALAMARES_SRC_BYTES
    extra = FIREFOX_SRC_BYTES if tier == "full" else LIBREWOLF_BIN_BYTES
    return shared + extra


# --- network: the bandwidth probe (the ONLY networked function) -------------
# Probe the SAME infrastructure the real build pulls from: an Arch package mirror.
# core.db is a few-MB file present on every mirror with a stable name (unlike an
# ISO artifact whose path rots each release). A Range GET caps the transfer.
PROBE_URLS = (
    "https://geo.mirror.pkgbuild.com/core/os/x86_64/core.db",
    "https://mirror.rackspace.com/archlinux/core/os/x86_64/core.db",
)
PROBE_BYTES = 4 * 1024 * 1024   # read at most 4 MiB
PROBE_MIN = 256 * 1024          # need >= 256 KiB for a valid sample
PROBE_TIMEOUT = 5.0             # seconds, per URL attempt


def measure_mbps() -> float | None:
    """The ONLY function that touches the network. Returns measured megabits/sec,
    or None on any failure/offline. It CANNOT hang and NEVER raises:

      * urlopen(timeout=PROBE_TIMEOUT) arms the socket connect/recv timeout, so a
        dead host or stalled TLS handshake fails within ~5s.
      * the `monotonic() - t0 > PROBE_TIMEOUT` check inside the loop is a second,
        independent wall-clock cap: a server dribbling bytes just fast enough to
        keep resetting the per-recv timer still cannot hold the loop past 5s.
      * the Range header + the `got < PROBE_BYTES` guard cap the transfer at 4 MiB
        even if the server ignores Range and streams the whole file.
      * monotonic (not time.time) so an NTP step mid-probe can't yield a bad dt.
      * every failure (DNS/URLError, refused/OSError, timeout, bad Range/ValueError)
        is caught per URL -> next URL -> finally None. Worst-case total wall time
        is len(PROBE_URLS) * PROBE_TIMEOUT = 10s.

    Tests NEVER call this (they monkeypatch it or inject mbps) so the suite stays
    fully offline.
    """
    import socket
    import time
    import urllib.error
    import urllib.request

    for url in PROBE_URLS:
        try:
            req = urllib.request.Request(
                url, headers={"Range": f"bytes=0-{PROBE_BYTES - 1}",
                              "User-Agent": "azzio-estimate/1"})
            t0 = time.monotonic()
            got = 0
            with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
                while got < PROBE_BYTES:
                    if time.monotonic() - t0 > PROBE_TIMEOUT:
                        break
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    got += len(chunk)
            dt = time.monotonic() - t0
            if got >= PROBE_MIN and dt > 0:
                return got * 8 / (dt * 1_000_000)
        except (urllib.error.URLError, socket.timeout, OSError, ValueError):
            continue
    return None


def download_seconds(size_bytes: int, mbps: float) -> float:
    """Wall-clock seconds to transfer size_bytes at mbps megabits/sec."""
    return float("inf") if mbps <= 0 else size_bytes * 8 / (mbps * 1_000_000)


def estimate_network(tier: str, mbps: float | None = None) -> dict:
    """Estimate the download for this tier. If mbps is None, run the real probe;
    tests inject a value to stay offline.

    A single warm HTTP stream is an OPTIMISTIC read of what pacman's multi-mirror,
    multi-connection download sustains over a big fetch, so we band the measured
    throughput: high time = measured * 0.5 (a pessimistic sustained rate), low
    time = measured * 0.9 (close to the warm-stream peak)."""
    size = download_size_bytes(tier)
    if mbps is None:
        mbps = measure_mbps()
    if mbps is None:
        return {"tier": tier, "mbps": None, "size_bytes": size,
                "low_seconds": None, "high_seconds": None, "offline": True}
    return {"tier": tier, "mbps": mbps, "size_bytes": size,
            "low_seconds": download_seconds(size, mbps * 0.9),
            "high_seconds": download_seconds(size, mbps * 0.5),
            "offline": False}


# --- formatters -------------------------------------------------------------
def _fmt_hours(h: float) -> str:
    if h < 1.0:
        return f"{round(h * 60)} min"
    hh = int(h)
    mm = round((h - hh) * 60)
    if mm == 0:
        return f"{hh}h"
    return f"{hh}h {mm}m"


def _fmt_seconds(s: float) -> str:
    if s >= 3600:
        return _fmt_hours(s / 3600.0)   # one hours formatter, reused
    if s >= 60:
        return f"{round(s / 60)} min"
    return f"{round(s)} s"


def _fmt_bytes(n: int) -> str:
    # base-1000 to line up with megabits/sec (both decimal).
    gb = n / 1_000_000_000
    if gb >= 1.0:
        return f"{gb:.1f} GB"
    return f"{round(n / 1_000_000)} MB"


def _fmt_mbps(mbps: float) -> str:
    return f"{round(mbps)} Mbit/s"


# --- orchestrator -----------------------------------------------------------
def _print_compute(c: dict) -> None:
    clock = f" @ {c['mhz'] / 1000:.1f} GHz" if c["mhz"] else ""
    ram = f"{c['ram_gb']:.1f} GB" if c["ram_gb"] else "unknown"
    print(f"    CPU  : {c['model']}{clock}  ({c['cores']} usable cores)")
    print(f"    RAM  : {ram}")
    print(f"    Compile time: roughly {_fmt_hours(c['low_hours'])} to "
          f"{_fmt_hours(c['high_hours'])}.")
    if c["ram_warning"]:
        print(f"    [!] Under {MIN_RAM_GB_COMFORTABLE:.0f} GB RAM: the Firefox "
              "compile may swap and run notably slower than the range above.")


def _print_network(n: dict) -> None:
    size = _fmt_bytes(n["size_bytes"])
    if n["offline"]:
        print(f"    Network : no reachable Arch mirror -- download time not estimated.")
        print(f"    Download size: ~{size} (compute-only total shown below).")
        return
    print(f"    Network : ~{_fmt_mbps(n['mbps'])} measured to an Arch mirror.")
    print(f"    Download time: roughly {_fmt_seconds(n['low_seconds'])} to "
          f"{_fmt_seconds(n['high_seconds'])} for ~{size}.")


def run(argv: list[str] | None = None) -> int:
    """Print the requested estimate and return 0. No build, no sudo, no workspace
    reset. Network modes run the bandwidth probe (the only I/O); compute-only
    modes never touch the network, network-only modes never read /proc."""
    parsed = parse_estimate_flag(argv if argv is not None else sys.argv[1:])
    tier, mode = parsed if parsed is not None else ("full", "both")
    do_compute = mode in ("compute", "both")
    do_network = mode in ("network", "both")

    # The full/both header keeps the literal "estimate-full-compile" substring so
    # a caller can grep the output for the flag it passed.
    flag = ("--estimate-full-compile" if tier == "full" else "--estimate")
    if mode != "both":
        flag += "-only-" + mode
    tier_desc = ("FULL from-source build (calamares + librewolf/Firefox)"
                 if tier == "full"
                 else "DEFAULT build (calamares from source, librewolf repackaged)")
    print(f"[*] {flag}: estimating a {tier_desc}. Nothing is built or downloaded.")

    compute = estimate_compute(tier) if do_compute else None
    network = estimate_network(tier) if do_network else None
    if compute is not None:
        _print_compute(compute)
    if network is not None:
        _print_network(network)

    # Combined total (both mode only): compute + download, when the probe worked.
    if mode == "both" and compute is not None and network is not None:
        if network["offline"]:
            print(f"    TOTAL: roughly {_fmt_hours(compute['low_hours'])} to "
                  f"{_fmt_hours(compute['high_hours'])} (compile only; add "
                  "download time once a mirror is reachable).")
        else:
            tlow = compute["low_hours"] + network["low_seconds"] / 3600.0
            thigh = compute["high_hours"] + network["high_seconds"] / 3600.0
            print(f"    TOTAL (download + compile): roughly {_fmt_hours(tlow)} "
                  f"to {_fmt_hours(thigh)}.")

    if tier == "default":
        print("    (The default `compile.sh` grabs verified binaries and finishes "
              "in minutes of compute, not hours.)")
    return 0
