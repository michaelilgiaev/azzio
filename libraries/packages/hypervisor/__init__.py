"""packages.hypervisor -- Azzio's per-directory QEMU/KVM VM runner (the `hypervisor` command).

Run `hypervisor` INSIDE a directory and it spins up a QEMU/KVM virtual machine whose
whole identity is derived from that directory: its name, disk (``<dir>.qcow2``), UEFI
NVRAM, shared folder and forwarded SSH port all come from the folder you are in, so
each directory is an independent VM. All VM settings live in a per-directory
``hypervisor.cfg`` (typed keys -- bool/int/path/device-list); a running VM watches
that file and applies edits live where it can, reverting an invalid save so a bad
edit never bricks the VM.

This is a HOST-side tool. It is distinct from the guest-side ``azzio
--sshd-hypervisor`` (packages/azzio/sshd.py), which runs INSIDE a VM to wire its
sshd up -- no overlap.

This package is one flat directory (like packages/backup and packages/passwords):

Entry points:
    hypervisor                      the `hypervisor` command (install/run/share/status/stop)

Subcommands (see command_line_interface.py usage): install / run / share / status / stop /
--configure (manage the global install defaults) / help.

Modules:
    command_line_interface          THE ENTRY -- arg parsing, usage text, dispatch
                                    (the launcher execs this; carries the sys.path bootstrap)
    configuration                   CWD-derived VM identity, paths, config (env > cfg > default)
    configuration_schema            the typed hypervisor.cfg schema, coercion, validation (pure)
    configuration_watcher           live hypervisor.cfg reload with validate + safe revert
    configuration_defaults          user-wide default overrides (~/.config/azzio-hypervisor)
    graphics                        DRM render-node selection for the GPU 3D offload
    checks                          precondition checks + die()/HypervisorError
    qemu_command                    the pure QEMU argv assembler (no launch, no I/O)
    virtual_machine                 the subcommand logic: install / run / share / status / stop

Also here (not part of the runtime import graph):
    packaging                       ISO build wiring (install paths, launcher, emit_plan)

The app is Python standard library only; the external binaries it shells out to are
``qemu-system-x86_64`` / ``qemu-img`` (qemu-full), ``remote-viewer`` (virt-viewer),
the OVMF firmware (edk2-ovmf) and ``pgrep`` -- all named in the manifest. packaging.py
ships every module flat to /usr/local/lib/azzio-hypervisor/ and installs the
/usr/local/bin/hypervisor launcher. This ``__init__.py`` makes the same directory
importable as the ``packages.hypervisor`` package for the test suite.
"""

from __future__ import annotations
