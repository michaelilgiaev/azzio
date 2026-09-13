#!/usr/bin/env python3
"""azzio guest command line interface -- `azzio network firewall` (ufw front-end + port table).

Split out of network.py (which was growing past the project's per-file size budget). This
module owns EVERYTHING firewall: the ufw enable/disable/default-policy commands, the port
open/close/DELETE verbs, and the port LISTING that stitches a human-friendly Title column
onto ufw's numbered rules (ufw itself has no notion of a rule name).

WHY A TITLE COLUMN. ufw lists rules by port/proto only, so a fresh developer sees `49154`
and has no idea it is the Azzio timedate home page. PORT_TITLES maps the ports Azzio
itself claims to a label, and `firewall port list` renders them in a Title column so the
listing reads, e.g., `49154 ... timedate`. Titles may be empty; the column is always shown
so the layout is stable. Extend PORT_TITLES as Azzio claims more ports.

OPEN / CLOSE / DELETE. The three port verbs map to ufw as:
  open   -> `ufw allow`   (let it through)
  close  -> `ufw deny`    (add a rule that DROPS it -- the rule STAYS in the list)
  delete -> `ufw delete`  (REMOVE the rule entirely, so the port falls back to the default
                           policy). close and delete are different: close leaves a visible
                           deny rule; delete takes the rule off the list altogether.

This module is BUNDLED into the single /usr/local/bin/azzio script ahead of network.py, so
its functions share the one runtime namespace and are called by bare name from
network.py's cmd_network / network_status (see common.py for the bundle mechanism). It uses
only the shared helpers already defined earlier in the bundle (_run/_sudo/_have/_need/_err).
"""

from __future__ import annotations

# BUNDLE_START


# ---------------------------------------------------------------------------
# firewall (ufw) + ports
# ---------------------------------------------------------------------------
# Human-friendly TITLES for well-known ports the Azzio build itself uses. ufw has no
# notion of a "name" for a rule, so we keep the map here and stitch it onto the numbered
# listing (the Title column) at display time. Extend it as Azzio claims more ports; a
# title of "" means "known port, but no title" and simply shows an empty Title cell. The
# lookup is by the base PORT number, so it matches whether the rule is 49154, 49154/tcp,
# 49154/udp, or a "port on <ip>" form -- the port is what identifies the service.
#   49154 = the Azzio timedate home page (Flask on localhost:49154), closed off-box by
#   the shipped firewall baseline (installer.setup_pkgs_sh).
PORT_TITLES = {
    49154: "timedate",
}


def _title_for_to(to_field: str) -> str:
    """The Title for a ufw 'To' column value ('49154', '49154/tcp', '22', ...).

    Pull the leading integer port out of the 'To' field and look it up in PORT_TITLES;
    return "" when the field has no numeric port (e.g. 'Anywhere') or the port is untitled.
    Kept forgiving so a listing never breaks on an unexpected 'To' shape."""
    head = to_field.strip().split()[0] if to_field.strip() else ""
    port, _, _proto = head.partition("/")
    if port.isdigit():
        return PORT_TITLES.get(int(port), "")
    return ""


def _firewall_port_list() -> int:
    """`firewall port list` -- ufw's numbered rules WITH an extra Title column.

    ufw stores no rule names, so we run `ufw status numbered`, keep its non-rule lines
    (Status/blank) verbatim, and re-render each '[ n] <to> <action> <from>' rule row as a
    fixed-width table that prepends a Title cell (from PORT_TITLES). This is what makes
    `azzio network firewall port list` show, e.g., `49154 ... timedate`. Titles can be
    empty; the column is always present so the layout is stable."""
    rc, out = _run("sudo", "-n", "ufw", "status", "numbered")
    if rc != 0:
        # Needs root; go through _sudo so a password prompt (if any) is shown to the user.
        rc = _sudo("ufw", "status", "numbered", check=False)
        return 0 if rc == 0 else 1
    print(_render_port_table(out))
    return 0


def _render_port_table(status_numbered: str) -> str:
    """Turn `ufw status numbered` text into a table with a leading Title column.

    Rule rows look like '[ 1] 49154   DENY IN   Anywhere'. We split each into
    (index, to, action, from), attach the Title, and lay the columns out at a stable width.
    Non-rule lines (the 'Status: active' header, blanks, and ufw's own 'To/Action/From'
    header underline) are dropped in favour of our own header so the Title column lines up.
    Pure string work -- no I/O -- so it is trivially unit-testable."""
    rows: list[tuple[str, str, str, str, str]] = []  # (num, title, to, action, from)
    status_line = ""
    for raw in status_numbered.splitlines():
        line = raw.rstrip()
        if line.lower().startswith("status:"):
            status_line = line
            continue
        if not line.strip().startswith("["):
            continue  # ufw's own column header / underline / blanks -> replaced below
        # '[ 1] <To...>  <ACTION IN/OUT>  <From...>'. Split the index off first.
        close = line.find("]")
        if close == -1:
            continue
        num = line[line.find("[") + 1:close].strip()
        body = line[close + 1:].strip()
        to, action, frm = _split_rule_body(body)
        rows.append((num, _title_for_to(to), to, action, frm))

    # Column widths sized to the content (with sensible header minimums).
    w_num = max([len("#")] + [len(r[0]) for r in rows]) if rows else len("#")
    w_title = max([len("Title")] + [len(r[1]) for r in rows]) if rows else len("Title")
    w_to = max([len("To")] + [len(r[2]) for r in rows]) if rows else len("To")
    w_action = max([len("Action")] + [len(r[3]) for r in rows]) if rows else len("Action")

    lines: list[str] = []
    if status_line:
        lines.append(status_line)
        lines.append("")
    header = (f"{'#':<{w_num}}  {'Title':<{w_title}}  {'To':<{w_to}}  "
              f"{'Action':<{w_action}}  From")
    lines.append(header)
    lines.append("-" * len(header))
    if not rows:
        lines.append("(no port rules)")
    for num, title, to, action, frm in rows:
        lines.append(f"{num:<{w_num}}  {title:<{w_title}}  {to:<{w_to}}  "
                     f"{action:<{w_action}}  {frm}")
    return "\n".join(lines)


def _split_rule_body(body: str) -> tuple[str, str, str]:
    """Split a ufw rule body 'To...  ACTION IN  From...' into (to, action, from).

    ufw pads columns with runs of spaces, so we split on 2+ spaces first (the reliable
    column separator). Falls back to a best-effort single-space split so an odd row still
    yields three cells rather than raising."""
    import re
    cols = re.split(r"\s{2,}", body.strip())
    if len(cols) >= 3:
        return cols[0].strip(), cols[1].strip(), cols[2].strip()
    parts = body.split()
    if len(parts) >= 4:
        # e.g. '49154 DENY IN Anywhere' -> to, 'DENY IN', from
        return parts[0], f"{parts[1]} {parts[2]}", " ".join(parts[3:])
    if len(parts) == 3:
        return parts[0], f"{parts[1]} {parts[2]}", ""
    return (body.strip(), "", "")


def _firewall_print_status(indent: str = "") -> None:
    if not _have("ufw"):
        print(f"{indent}Firewall: ufw not found")
        return
    rc, out = _run("ufw", "status", "verbose")
    if rc != 0:
        # ufw status needs root; fall back to the plain active/inactive check via sudo.
        rc2, out2 = _run("sudo", "-n", "ufw", "status")
        out = out2
        rc = rc2
    first = (out.strip().splitlines() or ["unknown"])[0]
    print(f"{indent}Firewall: {first or 'unknown'}")


def _firewall_status() -> int:
    if not _need("ufw", "ufw"):
        return 1
    rc = _sudo("ufw", "status", "verbose", check=False)
    return 0 if rc == 0 else 1


def _firewall_enable(on: bool) -> int:
    if not _need("ufw", "ufw"):
        return 1
    rc = _sudo("ufw", "--force", "enable", check=False) if on \
        else _sudo("ufw", "disable", check=False)
    if rc != 0:
        _err("azzio network firewall: could not change the firewall state.")
        return 1
    print(f"Firewall {'enabled' if on else 'disabled'}.")
    return 0


def _firewall_default(args: list[str]) -> int:
    """`firewall default <incoming> <outgoing>` -- each is allow|deny|reject."""
    if len(args) < 2:
        _err("azzio network firewall default: need <incoming> <outgoing> "
             "(allow|deny|reject).")
        return 2
    if not _need("ufw", "ufw"):
        return 1
    incoming, outgoing = args[0], args[1]
    valid = ("allow", "deny", "reject")
    if incoming not in valid or outgoing not in valid:
        _err("azzio network firewall default: policies must be allow|deny|reject.")
        return 2
    rc = _sudo("ufw", "default", incoming, "incoming", check=False)
    rc |= _sudo("ufw", "default", outgoing, "outgoing", check=False)
    if rc != 0:
        _err("azzio network firewall: could not set default policy.")
        return 1
    print(f"Firewall default: incoming {incoming}, outgoing {outgoing}.")
    return 0


def _port_spec(token: str) -> str | None:
    """Validate a 'PORT' or 'PORT/proto' token; return the normalised spec or None.

    PORT is 1..65535; proto (optional) is tcp|udp. Guards the ufw argv so a bad token
    can't be passed straight to ufw."""
    port, _, proto = token.partition("/")
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        return None
    if proto and proto not in ("tcp", "udp"):
        return None
    return token


def _firewall_port(args: list[str]) -> int:
    """`firewall port list|open <n[/proto]>|close <n[/proto]>|delete <n[/proto]>`.

    open  -> `ufw allow`  (let it through)
    close -> `ufw deny`   (add a rule that DROPS it, matching the deny-incoming default)
    delete-> `ufw delete` (REMOVE the rule entirely, whether it was allow or deny, so the
             port falls back to the default policy). This is the "how do I delete a port"
             the spec asks about: close leaves a deny rule in the list, delete takes the
             rule off the list altogether."""
    verb = args[0] if args else "list"
    rest = args[1:]
    if not _need("ufw", "ufw"):
        return 1
    if verb == "list":
        return _firewall_port_list()
    if verb in ("open", "close", "delete"):
        if not rest:
            _err(f"azzio network firewall port {verb}: need a port (e.g. 8080 or 8080/tcp).")
            return 2
        spec = _port_spec(rest[0])
        if spec is None:
            _err(f"azzio network firewall port {verb}: invalid port '{rest[0]}' "
                 "(use 1-65535 optionally /tcp or /udp).")
            return 2
        if verb == "open":
            rc = _sudo("ufw", "allow", spec, check=False)
        elif verb == "close":
            # `ufw deny` closes the port by dropping it (matches the Deny-incoming default).
            rc = _sudo("ufw", "deny", spec, check=False)
        else:
            # `ufw delete <rule>` removes a matching rule by VALUE. ufw only ever added the
            # port as an allow or a deny, so try to delete both forms; success on either
            # means the rule is gone. (Deleting a non-existent rule is a no-op error in ufw,
            # so we OR the results and treat "at least one removed" as success.)
            rc_allow = _sudo("ufw", "delete", "allow", spec, check=False)
            rc_deny = _sudo("ufw", "delete", "deny", spec, check=False)
            rc = 0 if (rc_allow == 0 or rc_deny == 0) else 1
        if rc != 0:
            _err(f"azzio network firewall: could not {verb} port {spec}.")
            return 1
        done = {"open": "opened", "close": "closed", "delete": "deleted"}[verb]
        print(f"Port {spec} {done}.")
        return 0
    if verb in ("--help", "-h", "help"):
        print("Usage: azzio network firewall port "
              "[list|open <n[/proto]>|close <n[/proto]>|delete <n[/proto]>]")
        return 0
    _err(f"azzio network firewall port: unknown verb: {verb}")
    return 2


def cmd_firewall(args: list[str]) -> int:
    """`azzio network firewall ...`"""
    verb = args[0] if args else "status"
    rest = args[1:]
    if verb == "status":
        return _firewall_status()
    if verb == "enable":
        return _firewall_enable(True)
    if verb == "disable":
        return _firewall_enable(False)
    if verb == "default":
        return _firewall_default(rest)
    if verb == "port":
        return _firewall_port(rest)
    if verb in ("--help", "-h", "help"):
        print("Usage: azzio network firewall "
              "[status|enable|disable|default <in> <out>|port ...]\n"
              "\n"
              "  port list                 Show all port rules (with a Title column).\n"
              "  port open  <n[/proto]>    Allow the port.\n"
              "  port close <n[/proto]>    Deny (drop) the port -- rule stays in the list.\n"
              "  port delete <n[/proto]>   Remove the port's rule entirely (open or close),\n"
              "                            so it falls back to the default policy.")
        return 0
    _err(f"azzio network firewall: unknown verb: {verb}")
    return 2
