"""configuration_schema -- the typed hypervisor.cfg schema, coercion, and validation.

The whole point of Part 2 is that hypervisor.cfg is no longer "bools + strings":
each key has a real type (bool, int, str, false-or-path, list-of-paths) and a
validator. This schema is the SINGLE SOURCE OF TRUTH: HypervisorCfg.from_dir uses
it to parse the file, and the live-reload watcher uses the SAME validator to
decide "valid -> apply" vs "invalid -> revert". So it must be pure and airtight.

`coerce_all(raw: dict[str,str]) -> (values: dict, errors: list[str])`:
  * raw is the KEY=VALUE string map straight from the file (unknown keys ignored).
  * on success every key is coerced to its Python type; errors is empty.
  * on ANY bad value the key's error is collected; a caller treats a non-empty
    errors list as "invalid format -> revert".
"""

from __future__ import annotations

import pytest

from packages.hypervisor import configuration_schema as cs


# --- booleans ---------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("false", False),
    ("True", True), ("FALSE", False), ("  true  ", True),
])
def test_bool_accepts_true_false_any_case(raw, expected):
    vals, errors = cs.coerce_all({"ssh": raw})
    assert not errors
    assert vals["ssh"] is expected


@pytest.mark.parametrize("bad", ["yes", "1", "on", "", "tru"])
def test_bool_rejects_non_boolean(bad):
    vals, errors = cs.coerce_all({"ssh": bad})
    assert errors, f"{bad!r} should be rejected as a bool"


# --- integers (ram / cpus / port) -------------------------------------------

def test_int_coerces_to_python_int():
    vals, errors = cs.coerce_all({"ram": "8192", "cpus": "4",
                                  "ssh_guest_to_host_port_forward": "2222"})
    assert not errors
    assert vals["ram"] == 8192 and isinstance(vals["ram"], int)
    assert vals["cpus"] == 4
    assert vals["ssh_guest_to_host_port_forward"] == 2222


@pytest.mark.parametrize("bad", ["lots", "8g", "", "-4", "0", "3.5"])
def test_int_rejects_non_positive_or_nonnumeric(bad):
    # ram/cpus/port must be positive whole numbers.
    vals, errors = cs.coerce_all({"cpus": bad})
    assert errors, f"cpus={bad!r} should be rejected"


@pytest.mark.parametrize("bad", ["²", "³", "¹²", "٠١", "⁵"])
def test_int_rejects_unicode_digits_without_crashing(bad):
    # str.isdigit() is True for superscripts/other-script digits that int()
    # cannot parse. coerce_all must REJECT them cleanly, never raise ValueError.
    vals, errors = cs.coerce_all({"cpus": bad})
    assert errors, f"cpus={bad!r} must be rejected, not crash"


def test_port_upper_bound_is_enforced():
    # a TCP port must fit 1..65535; a huge value must be rejected here, not blow
    # up later in socket.bind (OverflowError) on the run path.
    _, errors = cs.coerce_all({"ssh_guest_to_host_port_forward": "70000"})
    assert errors, "port > 65535 must be rejected"
    _, ok = cs.coerce_all({"ssh_guest_to_host_port_forward": "65535"})
    assert not ok, "65535 is a valid port"


def test_coerce_all_tolerates_non_string_values():
    # defensive: coerce_all must never raise on a non-str value (e.g. a caller
    # passing an already-typed dict); it should treat it as invalid, not crash.
    _, errors = cs.coerce_all({"ram": None})
    assert errors
    _, errors = cs.coerce_all({"cpus": 5})       # already an int
    # an int value is not a raw string; must be handled without AttributeError.
    assert isinstance(errors, list)


@pytest.mark.parametrize("bad", ["٢٠٠G", "２００G", "२००G"])
def test_disk_size_rejects_unicode_digits(bad):
    # disk_size must be ASCII digits only, consistent with int coercion.
    _, errors = cs.coerce_all({"disk_size": bad})
    assert errors, f"disk_size={bad!r} (unicode digits) must be rejected"


# --- disk_size (string, but shape-checked) ----------------------------------

@pytest.mark.parametrize("good", ["200G", "40G", "1024M", "2T"])
def test_disk_size_accepts_sizes(good):
    vals, errors = cs.coerce_all({"disk_size": good})
    assert not errors and vals["disk_size"] == good


@pytest.mark.parametrize("bad", ["200", "big", "", "G200"])
def test_disk_size_rejects_bad_shapes(bad):
    vals, errors = cs.coerce_all({"disk_size": bad})
    assert errors, f"disk_size={bad!r} should be rejected"


# --- audio (on|off) ---------------------------------------------------------

@pytest.mark.parametrize("good", ["on", "off", "ON", "Off"])
def test_audio_on_off(good):
    vals, errors = cs.coerce_all({"audio": good})
    assert not errors and vals["audio"] in ("on", "off")


def test_audio_rejects_other():
    vals, errors = cs.coerce_all({"audio": "loud"})
    assert errors


# --- shared: false OR "" (working dir) OR a path ----------------------------

def test_shared_false_is_bool_false():
    vals, errors = cs.coerce_all({"shared": "false"})
    assert not errors and vals["shared"] is False


def test_shared_empty_means_working_dir_sentinel():
    # "" is the ENABLED-at-working-dir case; represented as True (default dir).
    vals, errors = cs.coerce_all({"shared": ""})
    assert not errors
    assert vals["shared"] is True


def test_shared_path_is_kept_as_string():
    vals, errors = cs.coerce_all({"shared": "/mnt/host/share"})
    assert not errors
    assert vals["shared"] == "/mnt/host/share"


def test_shared_true_means_working_dir():
    # 'true' is a friendly alias for "enabled at the working dir".
    vals, errors = cs.coerce_all({"shared": "true"})
    assert not errors and vals["shared"] is True


# --- network: user | none | <interface> -------------------------------------

@pytest.mark.parametrize("good", ["user", "none"])
def test_network_keywords(good):
    vals, errors = cs.coerce_all({"network": good})
    assert not errors and vals["network"] == good


def test_network_interface_name_kept():
    vals, errors = cs.coerce_all({"network": "eno1"})
    assert not errors
    assert vals["network"] == "eno1"


@pytest.mark.parametrize("bad", ["", "eth 0", "bad/iface", "a b c"])
def test_network_rejects_garbage(bad):
    vals, errors = cs.coerce_all({"network": bad})
    assert errors, f"network={bad!r} should be rejected"


# --- usb: "" | one path | many paths ----------------------------------------

def test_usb_empty_is_empty_list():
    vals, errors = cs.coerce_all({"usb": ""})
    assert not errors and vals["usb"] == []


def test_usb_single_path_is_one_element_list():
    vals, errors = cs.coerce_all({"usb": "/dev/bus/usb/003/004"})
    assert not errors
    assert vals["usb"] == ["/dev/bus/usb/003/004"]


def test_usb_many_paths_space_or_comma_separated():
    space = cs.coerce_all({"usb": "/dev/bus/usb/003/004 /dev/sdb"})[0]
    comma = cs.coerce_all({"usb": "/dev/bus/usb/003/004, /dev/sdb"})[0]
    assert space["usb"] == ["/dev/bus/usb/003/004", "/dev/sdb"]
    assert comma["usb"] == ["/dev/bus/usb/003/004", "/dev/sdb"]


def test_usb_rejects_relative_or_nonpath_tokens():
    vals, errors = cs.coerce_all({"usb": "not-a-path"})
    assert errors, "a non-absolute usb token should be rejected"


def test_usb_legacy_boolean_is_tolerated_as_no_passthrough():
    # Old cfgs wrote `usb = false` (a boolean). Treat legacy true/false as "no
    # device passthrough" ([]) so a pre-redesign hypervisor.cfg still loads.
    assert cs.coerce_all({"usb": "false"}) == ({"usb": []}, [])
    assert cs.coerce_all({"usb": "true"}) == ({"usb": []}, [])


# --- whole-file behaviour ---------------------------------------------------

def test_unknown_keys_are_ignored_not_errors():
    vals, errors = cs.coerce_all({"totally_made_up": "1", "ram": "2048"})
    assert not errors
    assert "totally_made_up" not in vals
    assert vals["ram"] == 2048


def test_multiple_errors_all_collected():
    vals, errors = cs.coerce_all({"cpus": "lots", "audio": "loud", "ssh": "maybe"})
    assert len(errors) >= 3


def test_defaults_round_trip_clean():
    # Every default value, rendered to text and reparsed, must validate.
    from packages.hypervisor import configuration as config
    raw = {}
    for line in config._hypervisor_cfg_text(config._render_defaults()).splitlines():
        line = line.split("#", 1)[0]
        if "=" in line:
            k, v = line.split("=", 1)
            raw[k.strip()] = v.strip()
    vals, errors = cs.coerce_all(raw)
    assert not errors, f"generated default cfg must validate, got: {errors}"
