"""Tests for the hypervisor package's build wiring -- packaging.emit_plan() and the
bash completion it ships.

Two concerns, both regression-pinned:

  1. emit_plan() ships the bash completion (completion.bash) to the SYSTEM completion
     dir at 0644 with the basename `hypervisor` (the name the lazy loader looks up),
     verbatim, WITHOUT disturbing the launcher / entry-script / module entries or the
     purity of the plan. The completion is a DATA file, so it must NOT also appear as a
     LIB_DIR module (that would double-ship it and put a non-.py in the flat import dir).

  2. The completion script itself completes what PROMPT.md asked for: subcommands at
     position 1, and for view/stop the running VMs' NAMES and PIDS parsed from
     `hypervisor ls` (header + empty-state skipped, current prefix honoured). Driven by a
     real bash with `hypervisor` stubbed, so no VM and no install are needed; self-skips
     if bash is absent.

No emojis.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from packages.hypervisor import packaging

COMPLETION_FILE = Path(packaging.__file__).resolve().parent / packaging.COMPLETION_SOURCE_NAME


# --- 1. emit_plan ships the completion --------------------------------------

def _plan_by_dest():
    return {e["dest"]: e for e in packaging.emit_plan()}


def test_emit_plan_includes_the_completion():
    entry = _plan_by_dest().get(packaging.COMPLETION_SYSTEM_PATH)
    assert entry is not None, "completion not in emit_plan()"
    assert entry["mode"] == 0o644
    # The loader keys off the command name, so the destination basename must be exactly it.
    assert packaging.COMPLETION_SYSTEM_PATH.endswith("/hypervisor")
    assert packaging.COMPLETION_SYSTEM_PATH.startswith("/usr/share/bash-completion/completions")


def test_completion_builder_returns_the_script_verbatim():
    entry = _plan_by_dest()[packaging.COMPLETION_SYSTEM_PATH]
    built = entry["builder"]()
    assert built == COMPLETION_FILE.read_text(encoding="utf-8")
    assert "complete -F _hypervisor_complete hypervisor" in built


def test_completion_is_not_also_shipped_as_a_lib_module():
    # It is a data file: it must not be swept into LIB_DIR beside the .py modules.
    dests = set(_plan_by_dest())
    assert f"{packaging.LIB_DIR}/{packaging.COMPLETION_SOURCE_NAME}" not in dests
    assert not any(d.startswith(packaging.LIB_DIR) and d.endswith(".bash") for d in dests)


def test_launcher_and_entry_still_shipped():
    dests = set(_plan_by_dest())
    assert packaging.LAUNCHER_SYSTEM_PATH in dests
    assert f"{packaging.LIB_DIR}/command_line_interface.py" in dests


def test_emit_plan_is_repeatable():
    # Docstring promise: built fresh each call, equal results (pure function).
    first = [e["dest"] for e in packaging.emit_plan()]
    second = [e["dest"] for e in packaging.emit_plan()]
    assert first == second


# --- 2. the completion script behaves ---------------------------------------

_HAVE_BASH = shutil.which("bash") is not None

_LS_ONE = (
    "VM                       PID      SSH  DIRECTORY\n"
    "codelis-claudedebug  1438058    49156  /home/main/Ignore/claudedebug/venv/codelis\n"
)
_LS_EMPTY = "No hypervisor VMs are running.\n"


def _drive(comp_line: str, ls_output: str) -> list[str]:
    """Source completion.bash, stub `hypervisor ls` with ls_output, fire the completion
    for comp_line, return COMPREPLY. `printf '%b'` turns repr's escaped newlines back
    into real lines."""
    trailing_empty = comp_line.endswith(" ")
    script = textwrap.dedent(
        f"""
        set -u
        hypervisor() {{
            if [ "$1" = ls ]; then printf '%b' {ls_output!r}; fi
        }}
        command() {{
            if [ "$1" = -v ] && [ "$2" = hypervisor ]; then return 0; fi
            builtin command "$@"
        }}
        source {str(COMPLETION_FILE)!r}
        COMP_LINE={comp_line!r}
        COMP_POINT=${{#COMP_LINE}}
        read -r -a COMP_WORDS <<< {comp_line!r}
        {"COMP_WORDS+=(\"\")" if trailing_empty else ":"}
        COMP_CWORD=$(( ${{#COMP_WORDS[@]}} - 1 ))
        COMPREPLY=()
        _hypervisor_complete
        printf '%s\\n' "${{COMPREPLY[@]}}"
        """
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"bash failed: {out.stderr}"
    return [line for line in out.stdout.splitlines() if line != ""]


@pytest.mark.skipif(not _HAVE_BASH, reason="bash not available")
def test_completion_registers_spec():
    out = subprocess.run(
        ["bash", "-c", f"source {str(COMPLETION_FILE)!r} && complete -p hypervisor"],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert "-F _hypervisor_complete hypervisor" in out.stdout


@pytest.mark.skipif(not _HAVE_BASH, reason="bash not available")
def test_completion_position_one_lists_subcommands():
    reply = _drive("hypervisor ", _LS_EMPTY)
    for sub in ("install", "run", "ls", "view", "share", "status", "stop", "help", "--configure"):
        assert sub in reply


@pytest.mark.skipif(not _HAVE_BASH, reason="bash not available")
def test_completion_view_completes_name_and_pid():
    reply = _drive("hypervisor view ", _LS_ONE)
    assert "codelis-claudedebug" in reply
    assert "1438058" in reply


@pytest.mark.skipif(not _HAVE_BASH, reason="bash not available")
def test_completion_view_honours_name_prefix():
    assert _drive("hypervisor view codelis", _LS_ONE) == ["codelis-claudedebug"]


@pytest.mark.skipif(not _HAVE_BASH, reason="bash not available")
def test_completion_view_honours_pid_prefix():
    assert _drive("hypervisor view 143", _LS_ONE) == ["1438058"]


@pytest.mark.skipif(not _HAVE_BASH, reason="bash not available")
def test_completion_stop_uses_same_targets():
    reply = _drive("hypervisor stop ", _LS_ONE)
    assert "codelis-claudedebug" in reply
    assert "1438058" in reply


@pytest.mark.skipif(not _HAVE_BASH, reason="bash not available")
def test_completion_skips_header_and_empty_state():
    assert _drive("hypervisor view ", _LS_EMPTY) == []
    assert "VM" not in _drive("hypervisor view ", _LS_ONE)
