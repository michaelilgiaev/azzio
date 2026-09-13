"""Azzio calamares source patch -- networkcfg writes a static NetworkManager profile.

The FIFTH Azzio source patch applied to the pinned calamares-3.4.2 tarball in the
recipe's prepare() (see pkgbuild_calamares). Kept in its own module so each patch is a
focused, independently-editable unit; pkgbuild_calamares re-exports the name-constant and
builder below, and pkgbuild.py re-exports them in turn.

This patch EDITS the stock target-side job src/modules/networkcfg/main.py. It is the
exec-time COMPANION to the networkq page patch: where networkq collects an "Automatic
(DHCP)" vs "Manual" choice and (for manual) five static-IPv4 fields and publishes them to
GlobalStorage, this patch teaches networkcfg's run() to READ those keys and write a fixed
NetworkManager keyfile on the installed target.

Why a source patch (not a module .conf / a shipped override dir): a module's SCRIPT is
resolved only from the modules-search paths (`local` = build dir, /usr/lib/calamares/
modules), verified in Settings.cpp/Module.cpp -- an /etc/calamares/modules/<name>.conf can
override a module's CONFIG but NOT its script. So changing the JOB's logic must be a source
patch, exactly like the other four Azzio calamares patches.

WHAT IT ADDS (all inside networkcfg/main.py, disjoint from the other patches' files):
  * _azzio_netmask_to_prefix(): dotted mask ("255.255.255.0") -> CIDR prefix (24),
    rejecting a non-contiguous/invalid mask (returns None) so no broken profile is written.
  * _azzio_write_static_connection(root): when GlobalStorage "networkMethod" == "manual"
    AND a non-empty IPv4 + valid mask are present, write
    <root>/etc/NetworkManager/system-connections/azzio-static.nmconnection (mode 0600 --
    NetworkManager IGNORES a world-readable system-connection) with method=manual,
    address1=<ip>/<prefix>[,<gateway>], dns=<dns1>;<dns2>;. A blank/partial manual entry
    (no IPv4, or a bad mask) falls back to DHCP rather than bricking connectivity.
  * a call to it at the end of run(), just before the final `return None`.
DHCP (the default) is untouched: the gate returns immediately when method != "manual".

Same authoring rule as the sibling patches: the diff is assembled from a line-by-line list
so every unified-diff CONTEXT line keeps its exact single leading space -- a triple-quoted
literal would let an editor strip that trailing space and silently break `patch`. A context
drift on a version bump makes `patch` fail LOUDLY in prepare(); regenerate via `diff -u`
against the new networkcfg/main.py then.
"""

from __future__ import annotations


CALAMARES_NETWORKCFG_STATIC_PATCH_NAME = "azzio-calamares-networkcfg-static.patch"


def calamares_networkcfg_static_patch() -> str:
    r"""Unified diff (-p1) applied to the extracted calamares-3.4.2 source in the
    recipe's prepare(): teach src/modules/networkcfg/main.py to write a 0600 static-IPv4
    NetworkManager profile on the target when the networkq page chose a manual
    configuration (see the module docstring). Two hunks: a helper block inserted before
    run(), and the call inserted before run()'s final `return None`. Verified to apply
    with `patch -p1 --fuzz=0` against the pinned tarball.

    Assembled line-by-line so every diff line keeps its exact leading character;
    regenerate via `diff -u` on a version bump.
    """
    lines = [
        "--- a/src/modules/networkcfg/main.py",
        "+++ b/src/modules/networkcfg/main.py",
        "@@ -93,6 +93,102 @@",
        "     return (\"/\" + relative_path, os.path.join(root_mount_point, relative_path))",
        " ",
        " ",
        "+def _azzio_netmask_to_prefix(netmask):",
        "+    \"\"\"Convert a dotted IPv4 subnet mask (\"255.255.255.0\") to a CIDR prefix (24).",
        "+",
        "+    Returns an int 0..32, or None if the mask is not a valid, CONTIGUOUS netmask.",
        "+    A non-contiguous mask (e.g. 255.0.255.0) is rejected rather than guessed at --",
        "+    the caller then skips writing a broken profile.",
        "+    \"\"\"",
        "+    parts = netmask.split(\".\")",
        "+    if len(parts) != 4:",
        "+        return None",
        "+    try:",
        "+        octets = [int(p) for p in parts]",
        "+    except ValueError:",
        "+        return None",
        "+    if any(o < 0 or o > 255 for o in octets):",
        "+        return None",
        "+    value = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]",
        "+    # A valid netmask is a run of 1s followed by a run of 0s: ~value + 1 is a power of 2.",
        "+    if value == 0:",
        "+        return 0",
        "+    inverted = (~value) & 0xFFFFFFFF",
        "+    if inverted & (inverted + 1) != 0:",
        "+        return None  # not contiguous",
        "+    prefix = 0",
        "+    while value & 0x80000000:",
        "+        prefix += 1",
        "+        value = (value << 1) & 0xFFFFFFFF",
        "+    return prefix",
        "+",
        "+",
        "+def _azzio_write_static_connection(root_mount_point):",
        "+    \"\"\"Azzio: when the installer's Network page chose a MANUAL (static) IPv4, write a",
        "+    NetworkManager keyfile profile on the TARGET so the installed system boots with that",
        "+    fixed address. Reads the five fields the networkq page published to GlobalStorage",
        "+    (networkIpv4/networkSubnetMask/networkGateway/networkDns1/networkDns2, plus the",
        "+    networkMethod gate). No-op unless method == \"manual\" AND a non-empty IPv4 + a valid",
        "+    subnet mask are present -- a blank/partial manual entry falls back to DHCP rather than",
        "+    bricking connectivity. The keyfile is written 0600 (NetworkManager IGNORES a",
        "+    world-readable system-connection), matching the live `azzio network ip static` tool.",
        "+    \"\"\"",
        "+    gs = libcalamares.globalstorage",
        "+    if gs.value(\"networkMethod\") != \"manual\":",
        "+        return",
        "+",
        "+    ipv4 = (gs.value(\"networkIpv4\") or \"\").strip()",
        "+    netmask = (gs.value(\"networkSubnetMask\") or \"\").strip()",
        "+    gateway = (gs.value(\"networkGateway\") or \"\").strip()",
        "+    dns1 = (gs.value(\"networkDns1\") or \"\").strip()",
        "+    dns2 = (gs.value(\"networkDns2\") or \"\").strip()",
        "+",
        "+    if not ipv4:",
        "+        libcalamares.utils.debug(\"networkcfg: manual method but no IPv4 given; leaving DHCP\")",
        "+        return",
        "+    prefix = _azzio_netmask_to_prefix(netmask) if netmask else None",
        "+    if prefix is None:",
        "+        libcalamares.utils.warning(",
        "+            \"networkcfg: invalid/empty subnet mask {!r}; leaving DHCP\".format(netmask))",
        "+        return",
        "+",
        "+    conn_dir = os.path.join(root_mount_point, \"etc/NetworkManager/system-connections\")",
        "+    os.makedirs(conn_dir, exist_ok=True)",
        "+    conn_path = os.path.join(conn_dir, \"azzio-static.nmconnection\")",
        "+",
        "+    dns_values = [d for d in (dns1, dns2) if d]",
        "+    lines = [",
        "+        \"[connection]\",",
        "+        \"id=Azzio Static\",",
        "+        # A fixed UUID is fine for a single shipped profile; NetworkManager only needs it",
        "+        # unique on the box, and this profile is the only one this job writes.",
        "+        \"uuid=a0000000-a000-4000-8000-00000000c0de\",",
        "+        \"type=802-3-ethernet\",",
        "+        \"autoconnect=true\",",
        "+        \"\",",
        "+        \"[ipv4]\",",
        "+        \"method=manual\",",
        "+        # address1 = <ip>/<prefix>[,<gateway>]; the gateway is appended only when set",
        "+        # (a blank gateway must NOT leave a trailing comma, which NM rejects).",
        "+        \"address1=\" + ipv4 + \"/\" + str(prefix) + (\",\" + gateway if gateway else \"\"),",
        "+    ]",
        "+    if dns_values:",
        "+        lines.append(\"dns=\" + \";\".join(dns_values) + \";\")",
        "+    lines += [",
        "+        \"\",",
        "+        \"[ipv6]\",",
        "+        \"method=auto\",",
        "+        \"\",",
        "+    ]",
        "+    content = \"\\n\".join(lines)",
        "+",
        "+    with open(conn_path, \"w\", encoding=\"UTF-8\") as f:",
        "+        f.write(content)",
        "+    os.chmod(conn_path, 0o600)",
        "+    libcalamares.utils.debug(",
        "+        \"networkcfg: wrote static profile {} ({}/{} gw={})\".format(conn_path, ipv4, prefix, gateway or \"none\"))",
        "+",
        "+",
        " def run():",
        "     \"\"\"",
        "     Setup network configuration",
        "@@ -184,4 +280,8 @@",
        "                 \"Can't copy resolv.conf from {}: {}\".format(source_resolv, err)",
        "                 )",
        " ",
        "+    # Azzio: if the Network page chose a manual (static) IPv4, write a fixed-address",
        "+    # NetworkManager profile on the target (0600). No-op for DHCP / blank entry.",
        "+    _azzio_write_static_connection(root_mount_point)",
        "+",
        "     return None"
    ]
    # Trailing newline so the last line is terminated (patch/POSIX text file).
    return "\n".join(lines) + "\n"
