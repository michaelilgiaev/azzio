"""Tests for the `azzio gpu` guest command: PCI vendor detection, the driver map,
and the offline-repo install argv. gpu.py is bundled into /usr/local/bin/azzio
(see packages/azzio/bundle.py), so like the other guest-CLI modules it is loaded
here from the emitted bundle to test the SHIPPED behavior, and unit-tested directly
for the pure detection/mapping helpers."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_GPU_PATH = Path(__file__).resolve().parent.parent / "libraries/packages/azzio/gpu.py"


def _load_gpu():
    """Load gpu.py as a standalone module. It calls _err/_have/_sudo by bare name at
    RUNTIME (they come from common.py in the real bundle); we inject no-op stand-ins so
    import and the pure helpers work in isolation."""
    spec = importlib.util.spec_from_file_location("azzio_gpu", _GPU_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Inject the bundle-provided bare-name helpers the module expects at call time.
    mod.__dict__["_err"] = lambda *a, **k: None
    mod.__dict__["_have"] = lambda p: False
    mod.__dict__["_sudo"] = lambda *a, **k: 0
    spec.loader.exec_module(mod)
    return mod


def _fake_pci(tmp_path, devices):
    """Build a fake /sys/bus/pci/devices tree. `devices` is a list of (class, vendor)
    hex strings, e.g. ("0x030000", "0x10de"). Returns the devices-root path."""
    root = tmp_path / "devices"
    root.mkdir(exist_ok=True)
    for i, (klass, vendor) in enumerate(devices):
        d = root / f"0000:00:{i:02d}.0"
        d.mkdir(exist_ok=True)
        (d / "class").write_text(klass + "\n")
        (d / "vendor").write_text(vendor + "\n")
    return str(root)


def test_detect_nvidia_from_sysfs(tmp_path):
    gpu = _load_gpu()
    root = _fake_pci(tmp_path, [("0x030000", "0x10de")])
    assert gpu.detect_vendors(root) == ["nvidia"]


def test_detect_ignores_non_display_class(tmp_path):
    # 0x020000 is a network controller, not display (0x03xxxx) -- must be ignored.
    gpu = _load_gpu()
    root = _fake_pci(tmp_path, [("0x020000", "0x10de")])
    assert gpu.detect_vendors(root) == []


def test_detect_multi_vendor_sorted_unique(tmp_path):
    gpu = _load_gpu()
    root = _fake_pci(tmp_path, [
        ("0x030000", "0x8086"),   # intel iGPU
        ("0x030000", "0x10de"),   # nvidia dGPU
        ("0x038000", "0x10de"),   # second nvidia display device -> deduped
    ])
    assert gpu.detect_vendors(root) == ["intel", "nvidia"]


def test_detect_amd_both_ids(tmp_path):
    gpu = _load_gpu()
    root = _fake_pci(tmp_path, [("0x030000", "0x1002")])
    assert gpu.detect_vendors(root) == ["amd"]
    root2 = _fake_pci(tmp_path, [("0x030000", "0x1022")])
    assert gpu.detect_vendors(root2) == ["amd"]


def test_detect_generic_vm_returns_empty(tmp_path):
    # A QXL/virtio/VMware display device (unknown vendor) -> no vendor driver.
    gpu = _load_gpu()
    root = _fake_pci(tmp_path, [("0x030000", "0x1234")])
    assert gpu.detect_vendors(root) == []


def test_detect_missing_sysfs_returns_empty():
    # An unreadable / nonexistent sysfs root must degrade to [] (no crash), matching the
    # docstring contract ("Unreadable tree => empty list").
    gpu = _load_gpu()
    assert gpu.detect_vendors("/this/path/does/not/exist") == []


def test_driver_map_has_all_vendors_and_common():
    gpu = _load_gpu()
    for v in ("nvidia", "amd", "intel"):
        assert v in gpu.DRIVER_MAP
        assert gpu.DRIVER_MAP[v]["base"], v
        assert "dev" in gpu.DRIVER_MAP[v]
    assert gpu.DRIVER_MAP["common"]["base"]


def test_wanted_packages_dedup_and_common(tmp_path):
    gpu = _load_gpu()
    pkgs = gpu.wanted_packages(["intel"])
    # intel base+dev present, plus the common base+dev, no duplicates.
    assert "vulkan-intel" in pkgs
    assert "intel-compute-runtime" in pkgs
    assert "vulkan-icd-loader" in pkgs          # from common
    assert "vulkan-tools" in pkgs               # from common dev
    assert len(pkgs) == len(set(pkgs))


def test_wanted_packages_empty_for_no_vendor(tmp_path):
    gpu = _load_gpu()
    # Generic/VM: no vendor -> still nothing to resolve (common alone is not installed
    # for a generic GPU; mesa already covers it). Empty vendors -> empty list.
    assert gpu.wanted_packages([]) == []


def test_pacman_install_argv_uses_offline_file_repo(tmp_path):
    gpu = _load_gpu()
    argv = gpu.pacman_install_argv(["nvidia-open-dkms", "cuda"],
                                   "/root/azzio/pacstrap-azzio-repo")
    joined = " ".join(argv)
    assert "pacman" in joined
    assert "--needed" in argv                   # idempotent
    assert "--noconfirm" in argv                # non-interactive
    assert "nvidia-open-dkms" in argv and "cuda" in argv
    # Offline: a transient pacman.conf pointing at the file:// repo (never the system conf).
    assert "--config" in argv
    assert "/etc/pacman.conf" not in joined


def test_cmd_gpu_list_prints_map(capsys, tmp_path):
    gpu = _load_gpu()
    rc = gpu.cmd_gpu(["--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nvidia" in out and "amd" in out and "intel" in out
    assert "cuda" in out                        # a dev package is shown


def test_cmd_gpu_resolve_generic_is_noop_success(monkeypatch, capsys, tmp_path):
    gpu = _load_gpu()
    # No vendor GPU detected -> report generic, exit 0, install nothing.
    monkeypatch.setattr(gpu, "detect_vendors", lambda *a, **k: [])
    called = {"install": False}
    monkeypatch.setattr(gpu, "_run_install",
                        lambda pkgs: called.__setitem__("install", True) or 0)
    rc = gpu.cmd_gpu(["--resolve"])
    out = capsys.readouterr().out.lower()
    assert rc == 0
    assert called["install"] is False
    assert "generic" in out or "no vendor" in out


def test_cmd_gpu_resolve_installs_only_missing(monkeypatch, tmp_path):
    gpu = _load_gpu()
    monkeypatch.setattr(gpu, "detect_vendors", lambda *a, **k: ["nvidia"])
    # Pretend the open module + nvidia-utils are already installed; the rest are missing.
    monkeypatch.setattr(gpu, "installed_packages",
                        lambda pkgs: {"nvidia-open-dkms", "nvidia-utils"})
    captured = {}
    monkeypatch.setattr(gpu, "_run_install",
                        lambda pkgs: captured.__setitem__("pkgs", list(pkgs)) or 0)
    rc = gpu.cmd_gpu(["--resolve"])
    assert rc == 0
    assert "nvidia-open-dkms" not in captured["pkgs"]  # already installed, filtered out
    assert "cuda" in captured["pkgs"]                  # missing -> installed
    assert "vulkan-icd-loader" in captured["pkgs"]     # common, missing -> installed


def test_gpu_module_is_bundled_before_cli():
    # gpu.py must be in the bundle, and BEFORE command_line_interface.py (which calls cmd_gpu
    # by bare name). It has no dependency on later modules, so placing it near machine.py is fine.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libraries"))
    from packages.azzio import bundle
    order = bundle.MODULE_ORDER
    assert "gpu.py" in order
    assert order.index("gpu.py") < order.index("command_line_interface.py")


def test_emitted_cli_defines_cmd_gpu():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libraries"))
    from packages import openbox
    out = openbox.azzio_command_line_interface()
    assert "def cmd_gpu(" in out
    assert "def detect_vendors(" in out


def _load_cli():
    """Load the emitted, bundled /usr/local/bin/azzio as a module (matches how
    test_configuration_openbox loads it) so dispatch is tested end-to-end."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libraries"))
    from packages import openbox
    src = openbox.azzio_command_line_interface()
    ns: dict = {}
    exec(compile(src, "azzio_cli", "exec"), ns)
    return ns


def test_timedate_resolve_sets_timezone_only():
    ns = _load_cli()
    calls = []
    # resolve_via_server now takes an optional non-interactive `choice`; a bare
    # `--resolve` (no --server) must pass choice=None (the shuffle+prompt path).
    ns["resolve_via_server"] = lambda choice=None: (
        calls.append(("choice", choice)) or ("SV", "America/El_Salvador"))
    ns["apply_timezone"] = lambda tz: calls.append(("tz", tz)) or 0
    ns["apply_language"] = lambda cc: calls.append(("lang", cc)) or 0
    rc = ns["main"](["timedate", "--resolve"])
    assert rc == 0
    assert ("tz", "America/El_Salvador") in calls
    assert ("choice", None) in calls
    assert not any(k == "lang" for k, _ in calls)


def test_language_resolve_sets_language_only():
    ns = _load_cli()
    calls = []
    ns["resolve_via_server"] = lambda choice=None: (
        calls.append(("choice", choice)) or ("SV", "America/El_Salvador"))
    ns["apply_timezone"] = lambda tz: calls.append(("tz", tz)) or 0
    ns["apply_language"] = lambda cc: calls.append(("lang", cc)) or 0
    rc = ns["main"](["language", "--resolve"])
    assert rc == 0
    assert ("lang", "SV") in calls
    assert ("choice", None) in calls
    assert not any(k == "tz" for k, _ in calls)


def test_timedate_resolve_propagates_resolver_failure():
    ns = _load_cli()
    ns["resolve_via_server"] = lambda choice=None: None   # no network / bad response
    rc = ns["main"](["timedate", "--resolve"])
    assert rc == 1


def test_gpu_dispatch_reaches_cmd_gpu():
    ns = _load_cli()
    seen = {}
    ns["cmd_gpu"] = lambda args: seen.__setitem__("args", args) or 0
    rc = ns["main"](["gpu", "--resolve"])
    assert rc == 0
    assert seen["args"] == ["--resolve"]


def test_old_resolve_flags_are_removed():
    ns = _load_cli()
    # The three old flags must no longer be recognised (unknown-command exit code 2)...
    for flag in ("--resolve-date-time", "--resolve-language", "--resolve-region"):
        assert ns["main"]([flag]) == 2, flag
    # ...and must not appear in usage text.
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ns["usage"]()
    help_text = buf.getvalue()
    for flag in ("--resolve-date-time", "--resolve-language", "--resolve-region"):
        assert flag not in help_text, flag
    # The new positional commands ARE advertised (usage shows them in bracketed form,
    # e.g. "timedate [--resolve]"), so assert the command word + the --resolve option appear.
    assert "timedate [--resolve]" in help_text
    assert "language [--resolve]" in help_text
    assert "gpu [--resolve" in help_text


def test_all_driver_packages_are_baked_into_manifest():
    # Every package azzio gpu can install MUST ship on the ISO, so --resolve is an OFFLINE
    # install (the whole point). Parse packages.x86_64 the way mkarchiso does (strip comments
    # + blanks) and assert it is a superset of the full driver map.
    gpu = _load_gpu()
    manifest = (Path(__file__).resolve().parent.parent
                / "libraries/packages/packages.x86_64")
    names = set()
    for line in manifest.read_text().splitlines():
        tok = line.split("#", 1)[0].strip()
        if tok:
            names.add(tok)
    wanted = set()
    for spec in gpu.DRIVER_MAP.values():
        wanted.update(spec.get("base", []))
        wanted.update(spec.get("dev", []))
    missing = sorted(wanted - names)
    assert not missing, f"driver packages absent from packages.x86_64: {missing}"


def _manifest_names():
    manifest = (Path(__file__).resolve().parent.parent
                / "libraries/packages/packages.x86_64")
    names = set()
    for line in manifest.read_text().splitlines():
        tok = line.split("#", 1)[0].strip()
        if tok:
            names.add(tok)
    return names


def test_nvidia_open_module_is_the_baked_default():
    # The OPEN module is the default and MUST be baked into the ISO manifest; bare `nvidia`
    # (which is not a real Arch package -- it broke the build) must be gone.
    gpu = _load_gpu()
    assert gpu.NVIDIA_OPEN == "nvidia-open-dkms"
    assert gpu.DRIVER_MAP["nvidia"]["base"][0] == "nvidia-open-dkms"
    names = _manifest_names()
    assert "nvidia-open-dkms" in names
    assert "nvidia" not in names                      # the build-breaking bare package is gone


def test_no_phantom_proprietary_nvidia_package_anywhere():
    # Arch's current driver series ships ONLY the open module; the closed/proprietary kernel
    # module (nvidia / nvidia-dkms / nvidia-lts) no longer exists in the repos, so it must
    # not be referenced by the driver map or baked into the manifest (a phantom target that
    # only resolves via a Provides-alias back to the open module -- which broke the build /
    # the offline cache when tried). Guard against it creeping back.
    gpu = _load_gpu()
    all_map_pkgs = set()
    for spec in gpu.DRIVER_MAP.values():
        all_map_pkgs.update(spec.get("base", []))
        all_map_pkgs.update(spec.get("dev", []))
    for phantom in ("nvidia", "nvidia-dkms", "nvidia-lts"):
        assert phantom not in all_map_pkgs, f"{phantom} is not a real repo package"
        assert phantom not in _manifest_names(), f"{phantom} must not be pacstrapped"


def test_run_install_single_pacman_call(monkeypatch):
    # --resolve does one offline install of the resolved packages (open NVIDIA module + the
    # shared utils/cuda); there is no proprietary swap to perform.
    gpu = _load_gpu()
    monkeypatch.setattr(gpu.os.path, "isdir", lambda p: False)   # force system-repo argv path
    calls = []
    monkeypatch.setattr(gpu, "_sudo", lambda *a, **k: calls.append(list(a)) or 0)
    rc = gpu._run_install([gpu.NVIDIA_OPEN, "nvidia-utils", "cuda"])
    assert rc == 0
    assert len(calls) == 1                                   # a single install, no swap
    flat = calls[0]
    assert gpu.NVIDIA_OPEN in flat and "cuda" in flat
    assert "-R" not in flat                                  # nothing removed
