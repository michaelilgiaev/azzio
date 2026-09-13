"""Shared test helper for the `packages.hypervisor` suite -- a Config factory.

The hypervisor tests (test_configuration_hypervisor*.py) all build a
packages.hypervisor.configuration.Config rooted at a tmp dir with an overridable
HypervisorCfg. This one factory lives here (beside the tests, importable via the
tests/-dir-on-path set up in conftest.py) so a schema change is made in ONE place
rather than chased through every per-file copy. Ported from the source project's
tests/conftest.make_cfg.
"""

from __future__ import annotations

import os

from packages.hypervisor.configuration import Config, HypervisorCfg

# The coerced defaults every test Config starts from (matches _CFG_DEFAULTS' types:
# bools, ints, usb list, shared union). One place so a schema change does not have to
# be chased through the per-file factories.
_HCFG_DEFAULTS = {
    "share_host_gpu": True,
    "network": "user",
    "shared": False,
    "ssh": False,
    "ssh_guest_to_host_port_forward": 49155,
    "usb": [],
    "fullscreen": False,
    "ask_before_quitting_hypervisor": False,
    "disk_size": "200G",
    "ram": 16384,
    "cpus": 16,
    "audio": "on",
}


def make_cfg(directory: str, *, vm: str = "testvm", **hcfg_overrides) -> Config:
    """A Config rooted at `directory` with an overridable HypervisorCfg.

    hcfg_overrides take COERCED values (ssh=True, ram=8192, usb=["/dev/..."],
    shared="/path" or True/False) -- the same types coerce_all yields.
    """
    vals = dict(_HCFG_DEFAULTS)
    vals.update(hcfg_overrides)
    return Config(
        dir=directory, vm=vm, proc=f"{vm}-vm"[:15],
        disk=os.path.join(directory, f"{vm}.qcow2"),
        vars=os.path.join(directory, "OVMF_VARS.4m.fd"),
        shared=os.path.join(directory, "Shared"),
        spice_sock=os.path.join(directory, ".spice.sock"),
        hypervisor_cfg_path=os.path.join(directory, "hypervisor.cfg"),
        hcfg=HypervisorCfg(**vals),
        code="/usr/share/edk2/x64/OVMF_CODE.4m.fd",
        vars_tmpl="/usr/share/edk2/x64/OVMF_VARS.4m.fd",
    )
