"""Shared pytest fixtures + import-path setup for the azzio test suite.

`bash tests.sh` already puts libraries/ and scripts/libraries/ on PYTHONPATH, and
pyproject.toml's [tool.pytest.ini_options] pythonpath does the same for a bare
`pytest` run. This conftest belt-and-suspenders it so the flat compiler modules
(build, paths, ...), the packages.* packages, and the flat specification_* modules
resolve no matter how the tests are launched.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (REPO / "libraries", REPO / "scripts" / "libraries"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

# Also put THIS tests/ dir on the path so a test module can import a shared, test-only
# helper module that sits beside it (e.g. `from hypervisor_helpers import make_cfg`).
# Under pytest's importlib import mode the rootdir's tests/ dir is NOT added implicitly,
# so without this a sibling-helper import fails with ModuleNotFoundError. conftest is
# imported before any test module, so this runs early enough for every test.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# The tests/ dir is now on the path, so the test-mode helper (which lives beside this file)
# imports cleanly.
import _testmodes  # noqa: E402  (import after the sys.path setup above, by design)


def pytest_collection_modifyitems(config, items):
    """Skip the privileged test tiers unless their mode is enabled in tests/test_modes.conf.

    Two independent booleans gate two tiers; both default OFF so a plain `bash tests.sh` is green
    with no connectivity and no sudo. We SKIP (not deselect) so the logs still SHOW the gated tests
    as `s` -- visibly not-run, not silently vanished. Modes come from tests/test_modes.conf (env
    vars override); flip them with `tests.sh --online/--offline` (network) and `tests.sh
    --user/--root` (root). See tests/_testmodes.py for the parser and precedence.

    - `network`: OFFLINE (default) skips it -- those tests reach a real host and a plain run must
      stay green with no connectivity and NEVER hang on a DNS lookup. ONLINE runs them. (The
      network tests ALSO keep their own inline reachability probe as a second layer of defence.)
    - `root`: USER (default) skips it. ROOT selects it -- the two calamares btrfs loop-mount tests
      then run IF the process is actually UID 0, else self-skip via their own `os.geteuid()` guard.
    """
    marks = []
    if _testmodes.is_offline():
        marks.append(("network",
                      pytest.mark.skip(reason="offline mode (tests.sh --offline); run with tests.sh --online")))
    if _testmodes.is_user():
        marks.append(("root",
                      pytest.mark.skip(reason="user mode (tests.sh --user); run with tests.sh --root")))
    if not marks:
        return
    for item in items:
        for keyword, skip in marks:
            if keyword in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _isolated_gnupg_home(tmp_path_factory):
    """Point GNUPGHOME at a fresh, PRE-WARMED homedir for the whole test session.

    The backup / passwords tests shell out to the REAL `gpg` (its declared dep) for
    symmetric encrypt/decrypt. Left to the ambient ~/.gnupg they pass on a developer box
    (whose gpg-agent is already warm) but flake on a cold CI runner: the FIRST symmetric
    encrypt of the run races gpg's one-time homedir creation + gpg-agent socket spin-up and
    can return non-zero, surfacing as `AssertionError: vault setup failed`
    (test_configuration_backup.py). gpg discards its own stderr in the helper, so the CI log
    shows only `assert False` -- the cause is invisible.

    Fix both problems at the source: give every test an ISOLATED homedir (no dependency on
    ambient agent state, no cross-test contamination) and WARM it ONCE here -- create the
    keybox and spin up gpg-agent via a throwaway symmetric encrypt -- so no individual test
    ever pays the cold-start. gpg honours GNUPGHOME from the environment directly, so no
    production code needs a --homedir flag. The agent socket lives under
    /run/user/<uid>/gnupg (a short hashed path), not under GNUPGHOME, so the homedir path
    length is irrelevant to the AF_UNIX sun_path limit. If gpg is not installed the warm-up
    is skipped silently -- those tests self-skip on `shutil.which("gpg")` anyway.
    """
    home = tmp_path_factory.mktemp("gnupg")
    os.chmod(home, 0o700)
    previous = os.environ.get("GNUPGHOME")
    os.environ["GNUPGHOME"] = str(home)

    # Warm the fresh homedir: this first symmetric encrypt creates pubring.kbx and starts
    # gpg-agent, so the real tests never hit the cold-start race that flakes CI.
    seed = home / "warmup.txt"
    seed.write_text("warmup\n", encoding="utf-8")
    try:
        subprocess.run(
            ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback",
             "--symmetric", "--cipher-algo", "AES256", "--passphrase-fd", "0",
             "-o", str(home / "warmup.gpg"), str(seed)],
            input=b"warmup\n", capture_output=True, timeout=60, check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass  # gpg absent/misbehaving -- the real gpg tests self-skip via shutil.which.

    yield str(home)

    if previous is None:
        os.environ.pop("GNUPGHOME", None)
    else:
        os.environ["GNUPGHOME"] = previous
