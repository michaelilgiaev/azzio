"""config -- CWD-derived VM identity, hypervisor.cfg parsing, and the forwarded
SSH port. These are the deterministic pieces the whole tool threads through every
subcommand, so a silent regression here (a mis-parsed toggle, a wrong port,
a mandatory file that slips through) mis-boots every VM.
"""

from __future__ import annotations

import pytest

from hypervisor_helpers import make_cfg

from packages.hypervisor import configuration as config
from packages.hypervisor.configuration import (
    Config, HypervisorCfg, DEFAULT_SSH_FORWARD_PORT, _slugify, select_ssh_port,
)
from packages.hypervisor.checks import HypervisorError


# --- _slugify ---------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("azzio", "azzio"),
    ("My VM", "my-vm"),
    ("Weird__Name!!", "weird-name"),
    ("...", "vm"),            # nothing usable -> the "vm" fallback
    ("a--b---c", "a-b-c"),    # runs of separators collapse
    ("-trim-", "trim"),       # leading/trailing separators stripped
])
def test_slugify(raw, expected):
    assert _slugify(raw) == expected


# --- select_ssh_port --------------------------------------------------------

def test_select_ssh_port_defaults_to_49155(monkeypatch):
    monkeypatch.setattr(config, "_port_in_use", lambda p: False)
    cfg = _make_cfg("testvm")
    assert select_ssh_port(cfg) == DEFAULT_SSH_FORWARD_PORT
    assert DEFAULT_SSH_FORWARD_PORT == 49155


def test_select_ssh_port_bumps_past_a_used_port(monkeypatch):
    busy = {DEFAULT_SSH_FORWARD_PORT}
    monkeypatch.setattr(config, "_port_in_use", lambda p: p in busy)
    cfg = _make_cfg("testvm")
    assert select_ssh_port(cfg) == DEFAULT_SSH_FORWARD_PORT + 1


def test_ssh_port_override_wins(monkeypatch):
    monkeypatch.setattr(config, "_port_in_use", lambda p: False)
    cfg = _make_cfg("testvm", ssh_port=2222)
    assert select_ssh_port(cfg) == 2222


def test_select_ssh_port_does_not_climb_past_max(monkeypatch):
    # If the bump loop reached 65536 it would crash socket.bind (OverflowError).
    # With everything up to the max busy, select_ssh_port must fail CLEANLY.
    monkeypatch.setattr(config, "_port_in_use", lambda p: True)  # every port busy
    cfg = _make_cfg("testvm", ssh_port=65535)
    with pytest.raises(HypervisorError):
        select_ssh_port(cfg)


def test_port_in_use_treats_out_of_range_as_unusable(monkeypatch):
    # _port_in_use must not leak OverflowError for a port socket.bind rejects.
    assert config._port_in_use(70000) is True


# --- HypervisorCfg parsing + env override -----------------------------------

def test_cfg_defaults_when_no_file(tmp_path):
    hcfg = HypervisorCfg.from_dir(str(tmp_path))
    assert hcfg.share_host_gpu is True
    assert hcfg.network == "user"
    assert hcfg.ram == 16384                       # int now, not "16384"
    assert hcfg.shared is False
    assert hcfg.usb == []                          # list now, not False
    assert hcfg.ssh is False                       # renamed from sshd
    assert hcfg.ssh_guest_to_host_port_forward == 49155


def test_cfg_parses_typed_values(tmp_path):
    (tmp_path / "hypervisor.cfg").write_text(
        "share_host_gpu = false\n"
        "network = none\n"
        "shared = /mnt/host/share\n"
        "usb = /dev/bus/usb/003/004 /dev/sdb\n"
        "ssh = true\n"
        "ram = 8192  # inline comment ignored\n"
        "# a full comment line\n"
    )
    hcfg = HypervisorCfg.from_dir(str(tmp_path))
    assert hcfg.share_host_gpu is False
    assert hcfg.network == "none"
    assert hcfg.shared == "/mnt/host/share"
    assert hcfg.usb == ["/dev/bus/usb/003/004", "/dev/sdb"]
    assert hcfg.ssh is True
    assert hcfg.ram == 8192


def test_cfg_rejects_invalid_file_value(tmp_path):
    # an out-of-model value must fail loudly at parse time (not silently default).
    (tmp_path / "hypervisor.cfg").write_text("cpus = lots\n")
    with pytest.raises(HypervisorError):
        HypervisorCfg.from_dir(str(tmp_path))


def test_legacy_pre_redesign_cfg_still_loads(tmp_path):
    # A hypervisor.cfg written before the redesign used `sshd` and a boolean
    # `usb`. It must still load: sshd -> ssh, and usb=false -> no passthrough.
    (tmp_path / "hypervisor.cfg").write_text(
        "shared = true\n"
        "sshd = true\n"
        "usb = false\n"
    )
    hcfg = HypervisorCfg.from_dir(str(tmp_path))
    assert hcfg.ssh is True       # migrated from the legacy 'sshd' key
    assert hcfg.usb == []         # legacy boolean 'false' -> empty passthrough
    assert hcfg.shared is True


def test_env_overrides_file(tmp_path, monkeypatch):
    (tmp_path / "hypervisor.cfg").write_text("ram = 8192\nnetwork = user\n")
    monkeypatch.setenv("RAM", "4096")
    monkeypatch.setenv("NETWORK", "none")
    monkeypatch.setenv("SSH", "1")
    hcfg = HypervisorCfg.from_dir(str(tmp_path))
    assert hcfg.ram == 4096
    assert hcfg.network == "none"
    assert hcfg.ssh is True


def test_generated_cfg_has_light_comments(tmp_path):
    HypervisorCfg.write(str(tmp_path), config._render_defaults())
    text = (tmp_path / "hypervisor.cfg").read_text()
    # comments are now WANTED (the user reversed the earlier no-comment rule),
    # but minimal: at most one comment line per setting.
    comment_lines = [ln for ln in text.splitlines() if ln.strip().startswith("#")]
    setting_lines = [ln for ln in text.splitlines()
                     if ln.strip() and not ln.strip().startswith("#")]
    assert comment_lines, "generated cfg must carry high-level comments"
    assert len(comment_lines) <= len(setting_lines), "at most one comment per setting"
    # the dropped legacy keys stay dropped.
    assert "kiosk" not in text
    assert "gpu_outputs" not in text
    assert "sshd" not in text                       # renamed to ssh


def test_from_cwd_rejects_malformed_network(tmp_path, monkeypatch):
    # 'potato' is now a VALID interface name; a value with a space is not.
    (tmp_path / "hypervisor.cfg").write_text("network = bad iface\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(HypervisorError):
        Config.from_cwd()


def test_from_cwd_derives_identity_from_dir(tmp_path, monkeypatch):
    d = tmp_path / "My VM"
    d.mkdir()
    monkeypatch.chdir(d)
    cfg = Config.from_cwd()
    assert cfg.vm == "my-vm"
    assert cfg.proc == "my-vm-vm"
    assert cfg.disk.endswith("my-vm.qcow2")


# --- ISO discovery: REQUIRED, must be a .iso --------------------------------

def test_resolve_iso_requires_an_argument(tmp_path):
    cfg = _make_cfg("d", directory=str(tmp_path))
    with pytest.raises(HypervisorError):
        cfg.resolve_iso("")


def test_resolve_iso_rejects_non_iso_extension(tmp_path):
    (tmp_path / "thing.img").write_text("x")
    cfg = _make_cfg("d", directory=str(tmp_path))
    with pytest.raises(HypervisorError):
        cfg.resolve_iso("thing.img")


def test_resolve_iso_bare_filename_in_dir(tmp_path):
    (tmp_path / "azzio.iso").write_text("x")
    cfg = _make_cfg("d", directory=str(tmp_path))
    assert cfg.resolve_iso("azzio.iso") == str(tmp_path / "azzio.iso")


def test_resolve_iso_missing_named_file_raises(tmp_path):
    cfg = _make_cfg("d", directory=str(tmp_path))
    with pytest.raises(HypervisorError):
        cfg.resolve_iso("nope.iso")


# --- disk discovery for `run`: REQUIRED, must be a .qcow2 -------------------

def test_resolve_run_disk_requires_an_argument(tmp_path):
    cfg = _make_cfg("testvm", directory=str(tmp_path))
    with pytest.raises(HypervisorError):
        cfg.resolve_run_disk("")


def test_resolve_run_disk_rejects_non_qcow2(tmp_path):
    (tmp_path / "disk.raw").write_text("x")
    cfg = _make_cfg("testvm", directory=str(tmp_path))
    with pytest.raises(HypervisorError):
        cfg.resolve_run_disk("disk.raw")


def test_resolve_run_disk_bare_filename_in_dir(tmp_path):
    (tmp_path / "testvm.qcow2").write_text("x")
    cfg = _make_cfg("testvm", directory=str(tmp_path))
    assert cfg.resolve_run_disk("testvm.qcow2") == str(tmp_path / "testvm.qcow2")


def test_resolve_run_disk_missing_named_file_raises(tmp_path):
    cfg = _make_cfg("testvm", directory=str(tmp_path))
    with pytest.raises(HypervisorError):
        cfg.resolve_run_disk("gone.qcow2")


# --- helpers ----------------------------------------------------------------

def _make_cfg(vm: str, *, directory: str = "/d", ssh_port: int = 49155) -> Config:
    return make_cfg(directory, vm=vm, ssh_guest_to_host_port_forward=ssh_port)
