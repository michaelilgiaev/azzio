"""Clipboard access with "paste once, then clear" semantics.

The old behaviour was a bare `xclip` write: the secret stayed on the clipboard
indefinitely, pasteable any number of times, until something else overwrote it.
The manager now wants, per PROMPT.md:

  * clip a value, let the user paste it ONCE, then the clipboard clears itself;
  * a SEQUENCE mode: clip several values so the user pastes them in order (e.g.
    email, then username, then password), and the clipboard clears after the last;
  * copying something else in the middle CANCELS what we were holding (their new
    content simply wins);
  * nothing survives a reboot.

That cannot be done by handing a value to `xclip` -- a paste-once owner has to
watch the X selection requests and relinquish at the right moment. That logic
lives in clipboard_owner.py; this module just launches it as a DETACHED helper
process (so the curses UI is never blocked) and feeds it the value(s) over stdin
(NUL-separated, so secrets never appear in `ps`/argv).

Taking ownership of the CLIPBOARD selection automatically sends the previous
owner a SelectionClear, so starting a new clip supersedes any still-pending one
without us tracking PIDs. X selections are in-memory only, so the "no reboot
persistence" requirement holds for free -- the one caveat is a desktop clipboard
*manager* (Cinnamon's csd-clipboard, GPaste, or similar) that may snapshot the
value; clipboard_owner.py absorbs the manager's initial grab, but a manager's
own on-disk history (if enabled) is outside our control and worth the user
knowing about.

If the helper cannot be launched (no X, no Python, a locked-down PATH) we fall
back to a best-effort plain `xclip` copy so the value is at least available --
without the auto-clear guarantee. copy()/copy_sequence() return True on success.
"""

import os
import subprocess
import sys

# The interpreter and module we re-exec as the detached owner. Using the same
# sys.executable keeps us on the interpreter the app is already running under. The
# owner is a sibling module (the app is one flat directory now), so it is run as the
# top-level module `clipboard_owner` with this directory on the child's PYTHONPATH.
_OWNER_MODULE = 'clipboard_owner'


def _module_dir():
    """Directory that contains this module (and its sibling clipboard_owner), so the
    child's PYTHONPATH can import clipboard_owner regardless of how `passwords` was
    launched."""
    return os.path.dirname(os.path.abspath(__file__))


def _spawn_owner(values, timeout=None):
    """Launch the detached paste-once owner for `values` (list of str). Returns
    True if the process was started. The values are written to its stdin
    NUL-separated and the pipe is closed; the child then owns the clipboard on its
    own and this function returns immediately (never waits)."""
    payload = '\0'.join(values).encode('utf-8')
    args = [sys.executable, '-m', _OWNER_MODULE]
    if timeout is not None:
        args += ['--timeout', str(timeout)]
    env = dict(os.environ)
    # Ensure the child can import the owner module even if launched from an installed
    # console-script wrapper whose sys.path[0] is not this directory.
    root = _module_dir()
    env['PYTHONPATH'] = root + (os.pathsep + env['PYTHONPATH']
                               if env.get('PYTHONPATH') else '')
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            close_fds=True,
            # Detach into its own session so it outlives this UI turn and is not
            # killed by signals sent to the app's process group.
            start_new_session=True,
        )
    except OSError:
        return False
    try:
        proc.stdin.write(payload)
        proc.stdin.close()
    except OSError:
        return False
    return True


def _plain_copy(text):
    """Fallback: a bare xclip write (no auto-clear). Best effort."""
    try:
        subprocess.run(['xclip', '-selection', 'clipboard'],
                       input=text.encode(), check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def copy(text, timeout=None):
    """Put a single value on the clipboard for exactly one paste, then it clears.

    Returns True if the paste-once owner was launched (or, failing that, a plain
    copy succeeded)."""
    if text is None:
        text = ''
    if _spawn_owner([text], timeout=timeout):
        return True
    return _plain_copy(text)


def copy_sequence(values, timeout=None):
    """Put a SEQUENCE of values on the clipboard: the user pastes them in order,
    one paste each, and the clipboard clears after the last. `values` is a list of
    strings in paste order.

    Returns True if the owner was launched. With no values there is nothing to do
    (returns False). If the owner cannot launch we fall back to a plain copy of
    just the FIRST value (the best we can do without the sequencing helper)."""
    values = [('' if v is None else v) for v in values]
    if not values:
        return False
    if _spawn_owner(values, timeout=timeout):
        return True
    return _plain_copy(values[0])
