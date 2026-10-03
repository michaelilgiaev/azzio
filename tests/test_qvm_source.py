"""qvm_source -- azzio's build wiring that fetches `qvm` from GitHub and bakes it into the
ISO. These tests cover the parts that must be right for the build to ship a working `qvm`
WITHOUT touching the network: the emit plan's dests/modes, the no-cd launcher (the port's
single most important correctness property), and the AZZIO_QVM_DIR override that points the
whole thing at an existing checkout (so the build is offline/air-gapped-safe and so THIS
suite needs no clone).

The ensure_checkout() git path is deliberately NOT exercised here (it would need the
network / a real remote); AZZIO_QVM_DIR is the supported seam for an offline build and is
what we drive. A fake checkout (a libraries/ dir with a couple modules + completion.bash) is
built in tmp_path so nothing depends on the real qvm repo being present.
"""

from __future__ import annotations

import importlib

import pytest

import qvm_source


def _fake_checkout(root):
    """Build a minimal qvm checkout under `root`: libraries/ with the entry, a couple of
    sibling modules, and completion.bash. Returns the checkout root (what AZZIO_QVM_DIR
    points at). Mirrors qvm's real layout closely enough for the emit contract."""
    libs = root / "libraries"
    libs.mkdir(parents=True)
    (libs / "command_line_interface.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (libs / "configuration.py").write_text("# config\n", encoding="utf-8")
    (libs / "virtual_machine.py").write_text("# vm\n", encoding="utf-8")
    (libs / "qvm_main.py").write_text("# freeze entry (unused in the ISO)\n", encoding="utf-8")
    (libs / "completion.bash").write_text("complete -F _qvm_complete qvm\n", encoding="utf-8")
    return root


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A fake qvm checkout wired in via AZZIO_QVM_DIR, so emit_plan()/ensure_checkout()
    read it instead of cloning. Undone automatically by monkeypatch."""
    root = _fake_checkout(tmp_path / "qvm")
    monkeypatch.setenv("AZZIO_QVM_DIR", str(root))
    return root


def _plan_by_dest(plan):
    return {e["dest"]: e for e in plan}


def test_installed_paths_match_qvm_and_codelis():
    # /usr/bin/qvm (not /usr/local) so the on-PATH command is identical across qvm's own
    # install, codelis, and azzio. The lib dir and completion path follow from it.
    assert qvm_source.LAUNCHER_SYSTEM_PATH == "/usr/bin/qvm"
    assert qvm_source.LIB_DIR == "/usr/lib/qvm"
    assert qvm_source.ENTRY_SYSTEM_PATH == "/usr/lib/qvm/command_line_interface.py"
    assert qvm_source.COMPLETION_SYSTEM_PATH == "/usr/share/bash-completion/completions/qvm"


def test_emit_plan_ships_every_module_flat_into_lib_dir(checkout):
    plan = qvm_source.emit_plan()
    by_dest = _plan_by_dest(plan)
    # Every .py in the checkout's libraries/ lands flat in LIB_DIR at 0644.
    for mod in ("command_line_interface.py", "configuration.py", "virtual_machine.py", "qvm_main.py"):
        dest = f"/usr/lib/qvm/{mod}"
        assert dest in by_dest, f"{mod} not in emit plan"
        assert by_dest[dest]["mode"] == 0o644
    # The builder returns the file's verbatim text.
    entry = by_dest["/usr/lib/qvm/configuration.py"]
    assert entry["builder"]() == "# config\n"


def test_emit_plan_ships_completion_as_qvm(checkout):
    by_dest = _plan_by_dest(qvm_source.emit_plan())
    comp = by_dest.get("/usr/share/bash-completion/completions/qvm")
    assert comp is not None, "qvm completion not in emit plan"
    assert comp["mode"] == 0o644
    assert "complete -F _qvm_complete qvm" in comp["builder"]()


def test_emit_plan_ships_launcher_executable(checkout):
    by_dest = _plan_by_dest(qvm_source.emit_plan())
    launcher = by_dest.get("/usr/bin/qvm")
    assert launcher is not None, "qvm launcher not in emit plan"
    assert launcher["mode"] == 0o755


def test_completion_is_not_swept_into_lib_dir(checkout):
    # completion.bash is a DATA file: it must ship ONLY to the completion dir, never as
    # /usr/lib/qvm/completion.bash (it is not a module).
    dests = {e["dest"] for e in qvm_source.emit_plan()}
    assert "/usr/lib/qvm/completion.bash" not in dests


def test_launcher_execs_entry_and_does_not_cd():
    # The no-cd is load-bearing: `qvm` derives the VM identity from the caller's CWD, so the
    # launcher must exec the entry's ABSOLUTE path without changing directory. A `cd` would
    # make every VM resolve to LIB_DIR.
    sh = qvm_source.launcher_sh()
    assert qvm_source.ENTRY_SYSTEM_PATH in sh
    assert 'exec python -u' in sh
    assert '"$@"' in sh  # arguments forwarded, space-safe
    assert "cd " not in sh  # never changes directory


def test_builders_are_equal_for_same_path_so_emit_plan_is_pure(checkout):
    # Two emit_plan() calls must return EQUAL entries (compiler.py may call it more than once
    # per build). _FileBuilder keys equality on the path, so same-path builders compare equal.
    a = _plan_by_dest(qvm_source.emit_plan())
    b = _plan_by_dest(qvm_source.emit_plan())
    assert a.keys() == b.keys()
    assert a["/usr/lib/qvm/configuration.py"]["builder"] == b["/usr/lib/qvm/configuration.py"]["builder"]


def test_override_missing_entry_is_fatal(tmp_path, monkeypatch):
    # AZZIO_QVM_DIR pointing at something that is NOT a qvm checkout (no
    # libraries/command_line_interface.py) must raise, not silently ship an empty `qvm`.
    empty = tmp_path / "not-qvm"
    (empty / "libraries").mkdir(parents=True)
    monkeypatch.setenv("AZZIO_QVM_DIR", str(empty))
    with pytest.raises(qvm_source.QvmSourceError):
        qvm_source.ensure_checkout()


def test_override_points_ensure_checkout_at_it(checkout):
    # The override returns the checkout's libraries/ dir verbatim (no git involved).
    libs = qvm_source.ensure_checkout()
    assert libs == checkout / "libraries"
    assert (libs / "command_line_interface.py").is_file()


def test_repo_url_overridable_from_env(monkeypatch):
    # The remote is env-overridable (a local mirror / pinned build), matching codelis's
    # QVM_REPO_URL knob. qvm_source reads it at import, so reload after setting it.
    monkeypatch.setenv("QVM_REPO_URL", "https://example.invalid/mirror/qvm.git")
    monkeypatch.setenv("QVM_REPO_REF", "v1.2.3")
    try:
        reloaded = importlib.reload(qvm_source)
        assert reloaded.QVM_REPO_URL == "https://example.invalid/mirror/qvm.git"
        assert reloaded.QVM_REPO_REF == "v1.2.3"
    finally:
        monkeypatch.undo()
        importlib.reload(qvm_source)  # restore clean module state for other tests


def test_default_repo_is_the_canonical_qvm():
    # With no override, the default remote is the canonical baby on GitHub.
    assert qvm_source.QVM_REPO_URL == "https://github.com/michaelilgiaev/qvm.git"
    assert qvm_source.QVM_CHECKOUT.name == "qvm"
