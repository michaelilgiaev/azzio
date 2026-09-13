"""The `azzio network` command -- one front-end over nmcli / rfkill / bluetoothctl / ufw.

Azzio wraps the confusing raw networking tools in a plain noun/verb command matching
`azzio theme` / `azzio wallpaper`. These tests pin, against the BUNDLED shipped script
(packages.azzio.bundle.bundle_source -- the exact /usr/local/bin/azzio artifact):

  * that `network` is a real top-level dispatch branch advertised in usage();
  * the noun surface (status/wifi/wired/bluetooth/airplane/firewall/ip) and that each
    noun dispatches to the right function and prints help;
  * that the ACTIONS build the correct backend argv (nmcli/ufw/rfkill/bluetoothctl)
    WITHOUT touching the host -- `_sudo` and the read helpers are stubbed;
  * the guard rails: unknown noun/verb -> rc 2, bad port token -> rc 2, a missing
    backend degrades to rc 1 with a clear message;
  * that Bluetooth is OFF by default (build-time) and airplane kills every radio.

The command line interface is exercised via its bundle executed in one namespace, so tests drive the real
functions exactly as shipped.
"""

from __future__ import annotations

import types

import pytest

from packages.azzio.bundle import bundle_source
from packages import openbox as desktop


def _command_line_interface():
    """Exec the bundled azzio command line interface in a fresh module namespace (as shipped)."""
    mod = types.ModuleType("azzio_cli_network_test")
    exec(compile(bundle_source(), "azzio_command_line_interface", "exec"), mod.__dict__)
    return mod


def _capture_sudo(command_line_interface, monkeypatch, rc=0):
    """Replace _sudo with a recorder; return the list that receives each argv tuple."""
    calls: list[tuple] = []
    monkeypatch.setattr(command_line_interface, "_sudo", lambda *a, **k: (calls.append(a) or rc))
    return calls


def _stub_reads(command_line_interface, monkeypatch, have=True, run=(0, "")):
    """Stub the read side so nothing shells out: _have -> `have`, _run -> `run`."""
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: have)
    monkeypatch.setattr(command_line_interface, "_run", lambda *a: run)


# --- dispatch wiring --------------------------------------------------------

def test_network_is_a_dispatch_branch_in_main():
    src = desktop.azzio_command_line_interface()
    assert 'cmd == "network"' in src
    assert "return cmd_network(argv[1:])" in src
    # advertised in the top-level usage()
    assert "network <wifi|wired|bluetooth|airplane|firewall|ip|status>" in src


def test_network_help_prints_usage_and_exits_zero(capsys):
    command_line_interface = _command_line_interface()
    rc = command_line_interface.main(["network", "--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Usage: azzio network" in out
    for noun in ("wifi", "wired", "bluetooth", "airplane", "firewall", "ip"):
        assert noun in out
    # The firewall line advertises the full port verb set, including delete.
    assert "port list|open|close|delete" in out


def test_bare_network_prints_overview(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    # No backends present -> status still returns 0 and prints the sections.
    _stub_reads(command_line_interface, monkeypatch, have=False)
    rc = command_line_interface.main(["network"])
    out = capsys.readouterr().out
    assert rc == 0
    # The overview LEADS with the plain internet headline (the same one the terminal user interface shows); with
    # no nmcli that is the Offline verdict.
    assert "Offline - No Internet" in out
    assert "Airplane mode:" in out
    assert "Bluetooth:" in out
    assert "Firewall:" in out


def test_network_overview_reports_online_when_connectivity_full(monkeypatch, capsys):
    """With nmcli reporting full connectivity, the overview headline is the Online verdict."""
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    monkeypatch.setattr(command_line_interface, "_run",
                        lambda *a: (0, "full\n")
                        if a == ("nmcli", "networking", "connectivity") else (0, ""))
    assert command_line_interface.main(["network"]) == 0
    assert "Online - Connected to Internet" in capsys.readouterr().out


def test_unknown_noun_is_rc_two(capsys):
    command_line_interface = _command_line_interface()
    rc = command_line_interface.main(["network", "bogus"])
    assert rc == 2
    assert "unknown command" in capsys.readouterr().err


@pytest.mark.parametrize("noun", ["wifi", "wired", "bluetooth", "airplane", "firewall", "ip"])
def test_each_noun_help_exits_zero(noun, capsys):
    command_line_interface = _command_line_interface()
    assert command_line_interface.main(["network", noun, "--help"]) == 0
    assert "Usage:" in capsys.readouterr().out


# --- wifi -------------------------------------------------------------------

def test_wifi_connect_builds_nmcli_argv(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    rc = command_line_interface.main(["network", "wifi", "connect", "MyNet", "hunter2"])
    assert rc == 0
    assert ("nmcli", "device", "wifi", "connect", "MyNet", "password", "hunter2") in calls
    assert "Connected to MyNet." in capsys.readouterr().out


def test_wifi_connect_without_ssid_is_rc_two(capsys):
    command_line_interface = _command_line_interface()
    assert command_line_interface.main(["network", "wifi", "connect"]) == 2
    assert "need an SSID" in capsys.readouterr().err


def test_wifi_on_off_toggles_radio(monkeypatch):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "wifi", "on"]) == 0
    assert command_line_interface.main(["network", "wifi", "off"]) == 0
    assert ("nmcli", "radio", "wifi", "on") in calls
    assert ("nmcli", "radio", "wifi", "off") in calls


def test_wifi_without_nmcli_degrades(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: False)
    rc = command_line_interface.main(["network", "wifi", "on"])
    assert rc == 1
    assert "nmcli not found" in capsys.readouterr().err


def _stub_devices(command_line_interface, monkeypatch, table: str):
    """Stub `nmcli -t -f ... device` (the DEVICE,TYPE,STATE,CONNECTION read that
    _link_state uses) to return `table`; everything else reads empty."""
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)

    def fake_run(*a):
        if a[:3] == ("nmcli", "-t", "-f") and a[-1] == "device":
            return (0, table)
        return (0, "")
    monkeypatch.setattr(command_line_interface, "_run", fake_run)


def test_wifi_and_wired_are_mutually_exclusive_wired_wins(monkeypatch):
    """When ethernet is connected, wired is 'connected' and wifi is 'off' -- never both on
    (the spec: "if wired is connected then wifi is off"). Both read one device table."""
    command_line_interface = _command_line_interface()
    # ethernet connected, a wifi device present but merely 'disconnected'.
    _stub_devices(command_line_interface, monkeypatch,
                  "enp0s6:ethernet:connected:Wired connection 1\n"
                  "wlan0:wifi:disconnected:\n")
    assert command_line_interface._wired_state() == "connected"
    assert command_line_interface._wifi_state() == "off"


def test_wifi_and_wired_are_mutually_exclusive_wifi_wins(monkeypatch):
    """When wifi is the active link, wifi is 'connected' and wired is 'disconnected' -- the
    other half of one-or-the-other."""
    command_line_interface = _command_line_interface()
    _stub_devices(command_line_interface, monkeypatch,
                  "enp0s6:ethernet:disconnected:\n"
                  "wlan0:wifi:connected:HomeNet\n")
    assert command_line_interface._wifi_state() == "connected"
    assert command_line_interface._wired_state() == "disconnected"


# --- bluetooth (off by default) --------------------------------------------

def test_bluetooth_on_unblocks_and_enables(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    rc = command_line_interface.main(["network", "bluetooth", "on"])
    assert rc == 0
    assert ("rfkill", "unblock", "bluetooth") in calls
    assert ("systemctl", "enable", "--now", "bluetooth") in calls
    assert "Bluetooth enabled." in capsys.readouterr().out


def test_bluetooth_off_blocks_and_disables(monkeypatch):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "bluetooth", "off"]) == 0
    assert ("rfkill", "block", "bluetooth") in calls
    assert ("systemctl", "disable", "--now", "bluetooth") in calls


def test_bluetooth_connect_needs_mac(capsys):
    command_line_interface = _command_line_interface()
    assert command_line_interface.main(["network", "bluetooth", "connect"]) == 2
    assert "need a device MAC" in capsys.readouterr().err


# --- airplane ---------------------------------------------------------------

def test_airplane_on_really_drops_networking(monkeypatch, capsys):
    """Airplane ON must ACTUALLY drop the internet, not just the radios -- a wired VM has no
    radio to kill. So it turns NetworkManager's master switch off (`nmcli networking off`,
    which disconnects wired too), flips the radios, and rfkill-blocks. This pins the fix for
    the "fake airplane mode -- doesn't disable internet" report."""
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    rc = command_line_interface.main(["network", "airplane", "on"])
    assert rc == 0
    assert ("nmcli", "networking", "off") in calls      # the master switch: wired drops too
    assert ("nmcli", "radio", "all", "off") in calls
    assert ("rfkill", "block", "all") in calls
    assert "Airplane mode ON" in capsys.readouterr().out


def test_airplane_off_restores_networking(monkeypatch):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "airplane", "off"]) == 0
    assert ("nmcli", "networking", "on") in calls        # reconnects wired + auto profiles
    assert ("nmcli", "radio", "all", "on") in calls
    assert ("rfkill", "unblock", "all") in calls


def test_airplane_status_on_when_networking_disabled(monkeypatch, capsys):
    """Airplane is read from NetworkManager's master switch now: `nmcli networking` ==
    'disabled' means airplane is ON (the internet is really down). This replaces the old
    radios-only read that left a wired machine reporting airplane OFF while offline."""
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    monkeypatch.setattr(command_line_interface, "_run",
                        lambda *a: (0, "disabled\n")
                        if a == ("nmcli", "networking") else (0, ""))
    assert command_line_interface.main(["network", "airplane", "status"]) == 0
    assert "Airplane mode: on" in capsys.readouterr().out


def test_airplane_status_off_when_networking_enabled(monkeypatch, capsys):
    """`nmcli networking` == 'enabled' => airplane OFF (the default)."""
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    monkeypatch.setattr(command_line_interface, "_run",
                        lambda *a: (0, "enabled\n")
                        if a == ("nmcli", "networking") else (0, ""))
    assert command_line_interface.main(["network", "airplane", "status"]) == 0
    assert "Airplane mode: off" in capsys.readouterr().out


# --- firewall + ports -------------------------------------------------------

def test_firewall_default_builds_ufw_argv(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    rc = command_line_interface.main(["network", "firewall", "default", "deny", "allow"])
    assert rc == 0
    assert ("ufw", "default", "deny", "incoming") in calls
    assert ("ufw", "default", "allow", "outgoing") in calls
    assert "incoming deny, outgoing allow" in capsys.readouterr().out


def test_firewall_default_rejects_bad_policy(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "firewall", "default", "open", "allow"]) == 2
    assert "allow|deny|reject" in capsys.readouterr().err


def test_firewall_enable_uses_force(monkeypatch):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "firewall", "enable"]) == 0
    assert ("ufw", "--force", "enable") in calls
    assert command_line_interface.main(["network", "firewall", "disable"]) == 0
    assert ("ufw", "disable") in calls


def test_firewall_port_open_and_close(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "firewall", "port", "open", "8080/tcp"]) == 0
    assert ("ufw", "allow", "8080/tcp") in calls
    assert command_line_interface.main(["network", "firewall", "port", "close", "53/udp"]) == 0
    assert ("ufw", "deny", "53/udp") in calls
    out = capsys.readouterr().out
    assert "Port 8080/tcp opened." in out
    assert "Port 53/udp closed." in out


def test_firewall_port_rejects_bad_token(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "firewall", "port", "open", "99999"]) == 2
    assert command_line_interface.main(["network", "firewall", "port", "open", "80/sctp"]) == 2
    assert command_line_interface.main(["network", "firewall", "port", "open", "abc"]) == 2
    assert "invalid port" in capsys.readouterr().err


# --- firewall port: Title column + delete (the PROMPT.md additions) ---------

def test_firewall_port_49154_has_timedate_title():
    """The port-title map ships 49154 -> 'timedate' (the Azzio timedate home page)."""
    command_line_interface = _command_line_interface()
    assert command_line_interface.PORT_TITLES.get(49154) == "timedate"
    # The title is matched by base port, regardless of protocol suffix.
    assert command_line_interface._title_for_to("49154") == "timedate"
    assert command_line_interface._title_for_to("49154/tcp") == "timedate"
    # An untitled / non-numeric 'To' value yields an empty Title cell (never raises).
    assert command_line_interface._title_for_to("22/tcp") == ""
    assert command_line_interface._title_for_to("Anywhere") == ""


def test_firewall_port_list_renders_title_column():
    """`firewall port list` re-renders ufw's numbered rules with a leading Title column,
    and port 49154 shows the 'timedate' title in that column."""
    command_line_interface = _command_line_interface()
    sample = (
        "Status: active\n"
        "\n"
        "     To                         Action      From\n"
        "     --                         ------      ----\n"
        "[ 1] 49154                      DENY IN     Anywhere\n"
        "[ 2] 22/tcp                     ALLOW IN    Anywhere\n"
    )
    table = command_line_interface._render_port_table(sample)
    header = table.splitlines()
    # The Title column header is present...
    assert any(line.strip().startswith("#") and "Title" in line and "To" in line
               for line in header)
    # ...and 49154's row carries 'timedate' while the 22/tcp row's Title stays empty.
    row_49154 = next(l for l in header if "49154" in l)
    assert "timedate" in row_49154
    row_22 = next(l for l in header if "22/tcp" in l)
    assert "timedate" not in row_22


def test_firewall_port_list_dispatches_to_ufw_status_numbered(monkeypatch, capsys):
    """The list verb reads ufw status numbered (via a sudo -n probe) and prints the table."""
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    monkeypatch.setattr(command_line_interface, "_run",
                        lambda *a: (0, "Status: active\n[ 1] 49154   DENY IN   Anywhere\n"))
    _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "firewall", "port", "list"]) == 0
    out = capsys.readouterr().out
    assert "Title" in out
    assert "timedate" in out


def test_firewall_port_delete_removes_rule(monkeypatch, capsys):
    """`firewall port delete <n>` issues `ufw delete` for BOTH the allow and deny forms so
    the rule is removed whether it was opened or closed, and reports it deleted."""
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    calls = _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "firewall", "port", "delete", "8080/tcp"]) == 0
    assert ("ufw", "delete", "allow", "8080/tcp") in calls
    assert ("ufw", "delete", "deny", "8080/tcp") in calls
    assert "Port 8080/tcp deleted." in capsys.readouterr().out


def test_firewall_port_delete_rejects_bad_token(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    _stub_reads(command_line_interface, monkeypatch, have=True)
    _capture_sudo(command_line_interface, monkeypatch)
    assert command_line_interface.main(["network", "firewall", "port", "delete", "99999"]) == 2
    assert "invalid port" in capsys.readouterr().err


def test_firewall_port_help_lists_delete(capsys):
    command_line_interface = _command_line_interface()
    assert command_line_interface.main(["network", "firewall", "port", "--help"]) == 0
    assert "delete" in capsys.readouterr().out


def test_firewall_help_documents_title_and_delete(capsys):
    command_line_interface = _command_line_interface()
    assert command_line_interface.main(["network", "firewall", "--help"]) == 0
    out = capsys.readouterr().out
    assert "Title column" in out
    assert "port delete" in out


# --- ip (static / dynamic IPv4) --------------------------------------------

def test_ip_static_builds_nmcli_argv(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    # _conn_for_iface reads nmcli device rows; return a connection named 'Wired-1' for eth0.
    monkeypatch.setattr(command_line_interface, "_conn_for_iface", lambda iface: "Wired-1")
    calls = _capture_sudo(command_line_interface, monkeypatch)
    rc = command_line_interface.main(["network", "ip", "static", "eth0",
                   "192.168.1.50/24", "192.168.1.1", "1.1.1.1", "8.8.8.8"])
    assert rc == 0
    assert ("nmcli", "connection", "modify", "Wired-1",
            "ipv4.method", "manual",
            "ipv4.addresses", "192.168.1.50/24",
            "ipv4.gateway", "192.168.1.1") in calls
    assert ("nmcli", "connection", "modify", "Wired-1", "ipv4.dns", "1.1.1.1,8.8.8.8") in calls
    assert ("nmcli", "connection", "up", "Wired-1") in calls
    assert "Static IPv4 192.168.1.50/24" in capsys.readouterr().out


def test_ip_static_requires_cidr(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    monkeypatch.setattr(command_line_interface, "_conn_for_iface", lambda iface: "Wired-1")
    _capture_sudo(command_line_interface, monkeypatch)
    # address without /prefix is rejected before any sudo call
    assert command_line_interface.main(["network", "ip", "static", "eth0", "192.168.1.50", "192.168.1.1"]) == 2
    assert "must be CIDR" in capsys.readouterr().err


def test_ip_dynamic_switches_to_auto(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    monkeypatch.setattr(command_line_interface, "_conn_for_iface", lambda iface: "Wired-1")
    calls = _capture_sudo(command_line_interface, monkeypatch)
    rc = command_line_interface.main(["network", "ip", "dynamic", "eth0"])
    assert rc == 0
    assert ("nmcli", "connection", "modify", "Wired-1",
            "ipv4.method", "auto",
            "ipv4.addresses", "",
            "ipv4.gateway", "",
            "ipv4.dns", "") in calls
    assert "Dynamic IPv4 (DHCP) set on eth0." in capsys.readouterr().out


def test_ip_static_without_connection_is_rc_one(monkeypatch, capsys):
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    monkeypatch.setattr(command_line_interface, "_conn_for_iface", lambda iface: "")
    _capture_sudo(command_line_interface, monkeypatch)
    rc = command_line_interface.main(["network", "ip", "static", "eth9", "10.0.0.2/24", "10.0.0.1"])
    assert rc == 1
    assert "no NetworkManager connection" in capsys.readouterr().err


def test_prefix_to_netmask_conversion():
    command_line_interface = _command_line_interface()
    f = command_line_interface._prefix_to_netmask
    assert f("24") == "255.255.255.0"
    assert f("16") == "255.255.0.0"
    assert f("8") == "255.0.0.0"
    assert f("32") == "255.255.255.255"
    assert f("25") == "255.255.255.128"
    assert f("0") == "0.0.0.0"
    # Invalid prefixes yield "" (so the caller just omits the mask, never crashes).
    assert f("33") == ""
    assert f("x") == ""
    assert f("") == ""


def test_ip_show_reports_address_mask_gateway_and_dns(monkeypatch, capsys):
    # Regression: `azzio network ip show` used to print ONLY the address, hiding the subnet
    # mask, gateway and DNS the user needs to examine. It must now print all four for every
    # CONNECTED device, and derive the dotted subnet mask from the CIDR prefix.
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    # One connected ethernet device + a loopback that must be ignored (not "connected").
    monkeypatch.setattr(command_line_interface, "_nm_field",
        lambda fields, *a: [["enp0s6", "ethernet", "connected", "Wired connection 1"],
                            ["lo", "loopback", "unmanaged", ""]])

    def fake_run(*a):
        # device status table
        if a[:2] == ("nmcli", "-f"):
            return (0, "DEVICE  TYPE      STATE      CONNECTION\nenp0s6  ethernet  connected  Wired connection 1\n")
        # per-field -g reads
        if a[:3] == ("nmcli", "-g", "IP4.ADDRESS"):
            return (0, "10.0.2.15/24\n")
        if a[:3] == ("nmcli", "-g", "IP4.GATEWAY"):
            return (0, "10.0.2.2\n")
        if a[:3] == ("nmcli", "-g", "IP4.DNS"):
            return (0, "10.0.2.3\n8.8.8.8\n")
        return (0, "")
    monkeypatch.setattr(command_line_interface, "_run", fake_run)

    rc = command_line_interface.main(["network", "ip", "show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "enp0s6:" in out
    assert "address: 10.0.2.15/24" in out
    assert "subnet mask 255.255.255.0" in out
    assert "gateway: 10.0.2.2" in out
    # Multiple DNS servers are joined, in order.
    assert "dns:     10.0.2.3, 8.8.8.8" in out


def test_ip_show_handles_connected_device_without_ipv4(monkeypatch, capsys):
    # A connected device that has no IPv4 lease yet must not crash and must not print a
    # half-empty block -- it is simply skipped (no address -> nothing to show).
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_have", lambda prog: True)
    monkeypatch.setattr(command_line_interface, "_nm_field",
        lambda fields, *a: [["enp0s6", "ethernet", "connected", "Wired connection 1"]])
    monkeypatch.setattr(command_line_interface, "_run",
        lambda *a: (0, "") if a[:2] == ("nmcli", "-g") or a[:3][1:2] == ("-g",) else (0, "hdr\n"))
    rc = command_line_interface.main(["network", "ip", "show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "enp0s6:" not in out  # no address -> device block skipped


# --- port token validator (unit) -------------------------------------------

def test_port_spec_validator():
    command_line_interface = _command_line_interface()
    assert command_line_interface._port_spec("80") == "80"
    assert command_line_interface._port_spec("8080/tcp") == "8080/tcp"
    assert command_line_interface._port_spec("53/udp") == "53/udp"
    assert command_line_interface._port_spec("0") is None
    assert command_line_interface._port_spec("70000") is None
    assert command_line_interface._port_spec("80/sctp") is None
    assert command_line_interface._port_spec("abc") is None
