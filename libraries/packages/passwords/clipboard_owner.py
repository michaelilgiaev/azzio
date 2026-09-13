"""A one-shot X11 CLIPBOARD selection owner (the "paste once, then clear" core).

The password manager must put a value on the clipboard, let the user paste it
*exactly once*, and then have the clipboard go empty -- and if the user copies
something else first, that must cancel/replace what we were holding. None of that
is possible by shelling out to `xclip` with a value: a fixed request count cannot
tell a real paste apart from the TARGETS "what formats do you offer?" probe that
most toolkits (GTK, Qt, browsers, VS Code) send *before* the paste, so `xclip
-loops N` either clears too early (empty paste) or leaves the secret pasteable
again. Verified locally on xclip 0.13: one GUI paste is TARGETS + the data
request = two selection requests.

So we become the selection owner ourselves (via libX11 through ctypes, the same
technique keyboard.py already uses) and drive ICCCM by hand:

  * We answer TARGETS / TIMESTAMP / MULTIPLE probes WITHOUT counting them -- they
    are not a paste.
  * Only a real *data* conversion (UTF8_STRING / STRING / TEXT / COMPOUND_TEXT)
    counts as a paste. After serving the data once we advance: in SINGLE mode we
    then relinquish the selection (the clipboard goes empty); in SEQUENCE mode we
    move to the next value, and only relinquish after the last one is served.
  * A SelectionClear event means another program took the clipboard (the user
    copied something else) -- we exit at once, leaving THEIR content in place.
    That is the "copying something else cancels/overwrites us" behaviour.
  * A backstop timeout (no paste within N seconds) clears and exits so a secret
    never lingers on the clipboard forever.

X selections live only in this process / the X server's memory, so nothing here
persists across an X restart or a reboot -- that requirement is satisfied for
free (the caveat is a clipboard *manager* like GPaste snapshotting the
value; see clipboard.py's module note).

Values are passed in on argv-free stdin as a NUL-separated list so secrets never
appear in `ps`/argv. Run headless-testably: `serve_values([...], loops=...)`.
Payloads are assumed to fit a single transfer (passwords/usernames are tiny); the
INCR chunking protocol for huge selections is deliberately not implemented.
"""

import ctypes
import ctypes.util
import select
import sys
import time

# ---- libX11 bindings (only the handful of calls we need) --------------------

_X11 = None


def _load_x11():
    global _X11
    if _X11 is not None:
        return _X11
    name = ctypes.util.find_library('X11')
    if not name:
        raise OSError('libX11 not found')
    lib = ctypes.CDLL(name)
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XCreateSimpleWindow.restype = ctypes.c_ulong
    lib.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                        ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint, ctypes.c_uint,
                                        ctypes.c_uint, ctypes.c_ulong,
                                        ctypes.c_ulong]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XSetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                       ctypes.c_ulong, ctypes.c_ulong]
    lib.XGetSelectionOwner.restype = ctypes.c_ulong
    lib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.XPending.restype = ctypes.c_int
    lib.XPending.argtypes = [ctypes.c_void_p]
    lib.XSendEvent.restype = ctypes.c_int
    lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                               ctypes.c_long, ctypes.c_void_p]
    lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                    ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.XGetAtomName.restype = ctypes.c_char_p
    lib.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XFree.argtypes = [ctypes.c_void_p]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XConnectionNumber.restype = ctypes.c_int
    lib.XConnectionNumber.argtypes = [ctypes.c_void_p]
    _X11 = lib
    return lib


# Event structs. Rather than hand-pack bytes (fragile: padding, field counts) we
# declare the two selection events as ctypes Structures so the compiler-correct
# alignment is handled for us. Atoms/Window/Time are all `unsigned long` in Xlib.
_XID = ctypes.c_ulong
_ATOM = ctypes.c_ulong
_TIME = ctypes.c_ulong


class XSelectionRequestEvent(ctypes.Structure):
    _fields_ = [
        ('type', ctypes.c_int),
        ('serial', ctypes.c_ulong),
        ('send_event', ctypes.c_int),
        ('display', ctypes.c_void_p),
        ('owner', _XID),
        ('requestor', _XID),
        ('selection', _ATOM),
        ('target', _ATOM),
        ('property', _ATOM),
        ('time', _TIME),
    ]


class XSelectionEvent(ctypes.Structure):
    # SelectionNotify -- like the request event but with NO `owner` field.
    _fields_ = [
        ('type', ctypes.c_int),
        ('serial', ctypes.c_ulong),
        ('send_event', ctypes.c_int),
        ('display', ctypes.c_void_p),
        ('requestor', _XID),
        ('selection', _ATOM),
        ('target', _ATOM),
        ('property', _ATOM),
        ('time', _TIME),
    ]


# XEvent is a union; its largest member is 24 longs on LP64. We read the leading
# `type` int out of a buffer of that size, then reinterpret the buffer as the
# matching event struct via ctypes.cast (no byte math on our side).
_XEVENT_SIZE = 24 * ctypes.sizeof(ctypes.c_long)

SelectionClear = 29
SelectionRequest = 30
SelectionNotify = 31

PropModeReplace = 0
_ANY_PROPERTY_TYPE = 0


class _Atoms:
    """Interned atoms we care about, plus the set that counts as 'a real paste'."""

    def __init__(self, lib, dpy):
        def atom(name):
            return lib.XInternAtom(dpy, name.encode(), False)
        self.CLIPBOARD = atom('CLIPBOARD')
        self.TARGETS = atom('TARGETS')
        self.TIMESTAMP = atom('TIMESTAMP')
        self.MULTIPLE = atom('MULTIPLE')
        self.UTF8_STRING = atom('UTF8_STRING')
        self.STRING = atom('STRING')
        self.TEXT = atom('TEXT')
        self.COMPOUND_TEXT = atom('COMPOUND_TEXT')
        # The targets we serve the value for -- and, importantly, the only ones
        # that count as an actual paste (TARGETS/TIMESTAMP/MULTIPLE do not).
        self.data_targets = {self.UTF8_STRING, self.STRING, self.TEXT,
                             self.COMPOUND_TEXT}
        # What we advertise in reply to a TARGETS request.
        self.offered = [self.TARGETS, self.TIMESTAMP, self.UTF8_STRING,
                        self.STRING, self.TEXT]


def _as_request(buf):
    """Reinterpret an XEvent buffer as an XSelectionRequestEvent."""
    return ctypes.cast(buf, ctypes.POINTER(XSelectionRequestEvent)).contents


def _send_selection_notify(lib, dpy, req, prop):
    """Reply to a SelectionRequest with a SelectionNotify naming `prop` (or 0 to
    refuse). `req` is the XSelectionRequestEvent we are answering."""
    ev = XSelectionEvent()
    ev.type = SelectionNotify
    ev.send_event = True
    ev.display = ctypes.cast(dpy, ctypes.c_void_p)
    ev.requestor = req.requestor
    ev.selection = req.selection
    ev.target = req.target
    ev.property = prop                       # 0 == refused
    ev.time = req.time
    lib.XSendEvent(dpy, req.requestor, False, 0, ctypes.byref(ev))
    lib.XFlush(dpy)


class _Server:
    """Owns CLIPBOARD and serves a sequence of values, one paste each.

    `values` is a list of strings. In SINGLE mode it has one element; in SEQUENCE
    mode it has several and we advance through them, one real paste per value,
    relinquishing after the last. Exposed as a class mainly so the event handling
    is unit-testable by feeding synthetic requests to handle_request()."""

    def __init__(self, values):
        self.values = list(values)
        self.index = 0            # which value is currently being offered
        self.done = False         # sequence exhausted -> relinquish and exit

    def current_value(self):
        if 0 <= self.index < len(self.values):
            return self.values[self.index]
        return None

    def note_data_served(self):
        """Called after the *data* (not a probe) was handed to a requestor.

        Advances to the next value; when the last value has been served, flags
        done so the run loop relinquishes the selection and the clipboard goes
        empty (the whole point: paste each value exactly once, then nothing)."""
        self.index += 1
        if self.index >= len(self.values):
            self.done = True


def _classify_data_serve(requestor, elapsed, grace, manager_requestors):
    """Decide what a DATA serve to `requestor` means, `elapsed` seconds after we
    took ownership. Pure logic (no X), so it is unit-testable.

    Returns one of:
      'manager' -- inside the grace window: the clipboard manager priming its
                   cache. Serve it, remember the requestor, do NOT count it.
      'paste'   -- past the grace window and NOT a known manager requestor: the
                   user's real paste. Count it.
      'ignore'  -- past the grace window but a KNOWN manager requestor (a later
                   cache refresh). Serve it, do NOT count it.

    This replaces the earlier "any serve within 0.5s is the manager" rule, which
    also swallowed a fast real paste. Keying off the requestor lets a genuine
    paste count immediately (it comes from a different window), while the
    manager's grabs never count -- now or later."""
    if elapsed < grace:
        return 'manager'
    if requestor in manager_requestors:
        return 'ignore'
    return 'paste'


def _write_property(lib, dpy, req, value, atoms):
    """Put `value` (a str) on the requestor's property as the requested text
    target, then confirm with a SelectionNotify. Returns True only if this was a
    real data serve (a paste); a probe (TARGETS/TIMESTAMP/MULTIPLE) returns False
    so it never counts against the paste budget."""
    target = req.target
    prop = req.property
    if prop == 0:
        prop = target        # obsolete requestors send property=None; use target

    if target == atoms.TARGETS:
        # Advertise the atoms we can supply. Property type ATOM(4), 32-bit.
        arr = (ctypes.c_ulong * len(atoms.offered))(*atoms.offered)
        lib.XChangeProperty(dpy, req.requestor, prop, 4, 32,
                            PropModeReplace,
                            ctypes.cast(arr, ctypes.c_char_p),
                            len(atoms.offered))
        _send_selection_notify(lib, dpy, req, prop)
        return False
    if target == atoms.TIMESTAMP:
        ts = (ctypes.c_ulong * 1)(0)
        lib.XChangeProperty(dpy, req.requestor, prop, 4, 32,
                            PropModeReplace,
                            ctypes.cast(ts, ctypes.c_char_p), 1)
        _send_selection_notify(lib, dpy, req, prop)
        return False
    if target == atoms.MULTIPLE:
        # We do not implement MULTIPLE (batched conversions). Refusing makes a
        # conforming client fall back to individual requests, which we do serve.
        _send_selection_notify(lib, dpy, req, 0)
        return False
    if target in atoms.data_targets:
        data = value.encode('utf-8')
        # UTF8_STRING keeps its own type; the legacy string targets are labelled
        # STRING so old clients accept them. Format 8 (bytes).
        prop_type = atoms.UTF8_STRING if target == atoms.UTF8_STRING else atoms.STRING
        lib.XChangeProperty(dpy, req.requestor, prop, prop_type, 8,
                            PropModeReplace, data, len(data))
        _send_selection_notify(lib, dpy, req, prop)
        return True
    # Unknown target -> refuse (property = None).
    _send_selection_notify(lib, dpy, req, 0)
    return False


# The desktop's clipboard manager (Cinnamon's csd-clipboard here, likewise
# GPaste and similar) snapshots the new selection the instant we take ownership -- it
# requests the DATA in ~1-2ms, every time, to cache it. If that grab counted as
# "the paste", the clipboard would clear before the user ever pressed Ctrl+V.
#
# We uncount the manager's grab WITHOUT a wide time window (an early version used
# 0.5s, which also swallowed a fast real paste and left the secret pasteable).
# Instead we key off the REQUESTOR: any data request in the first few tens of ms
# is the manager, and we remember its requestor window; from then on we only
# uncount data serves to THOSE remembered requestors. A real paste comes from a
# different window, so it counts immediately -- even if it lands early. The window
# is 50ms: measured csd-clipboard grabs in 0.8-4.5ms (so 50ms is ~10x its
# worst case, robust to a loaded machine) yet is far below the time it takes a
# human to copy in the TUI and then focus another window and press Ctrl+V, so a
# genuine paste can never fall inside it from a different requestor.
_MANAGER_GRACE = 0.05


def serve_values(values, loops=None, timeout=None, grace=_MANAGER_GRACE,
                 _display=None):
    """Own CLIPBOARD and serve each value once (one human paste each), then clear.

    values  -- list of strings; one entry = SINGLE mode, several = SEQUENCE mode.
    timeout -- backstop seconds; if no paste happens we clear and exit. None -> a
               sensible default scaled to how many values remain.
    grace   -- seconds after ownership during which a data request is taken to be
               the clipboard manager priming its cache (served, not counted as a
               paste); the requestor is remembered so its later grabs are also
               uncounted. See _MANAGER_GRACE.
    loops   -- test hook only: stop after this many *events* even if unfinished.

    Returns when the sequence is served (clipboard left empty), the selection is
    taken by another app (their content left in place), or the timeout fires.
    Does the real X work; raises OSError if X is unreachable so the caller can
    fall back to a plain copy."""
    values = [v for v in values if v is not None]
    if not values:
        return
    lib = _load_x11()
    dpy = lib.XOpenDisplay(_display.encode() if _display else None)
    if not dpy:
        raise OSError('cannot open X display')
    try:
        root = lib.XDefaultRootWindow(dpy)
        win = lib.XCreateSimpleWindow(dpy, root, 0, 0, 1, 1, 0, 0, 0)
        atoms = _Atoms(lib, dpy)
        lib.XSetSelectionOwner(dpy, atoms.CLIPBOARD, win, 0)  # 0 = CurrentTime
        if lib.XGetSelectionOwner(dpy, atoms.CLIPBOARD) != win:
            raise OSError('failed to take CLIPBOARD ownership')
        lib.XFlush(dpy)
        owned_at = time.monotonic()

        server = _Server(values)
        if timeout is None:
            # Give the user time to paste each value; generous but finite so a
            # secret never sits on the clipboard indefinitely.
            timeout = 45 * len(values)
        deadline = time.monotonic() + timeout
        fd = lib.XConnectionNumber(dpy)
        evbuf = (ctypes.c_char * _XEVENT_SIZE)()
        events_handled = 0
        # Requestor windows that grabbed the DATA inside the opening grace window
        # -- the clipboard manager. Their serves never count as a paste (not now,
        # not later); every other requestor's data serve is a real paste.
        manager_requestors = set()

        while not server.done:
            if loops is not None and events_handled >= loops:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break                       # backstop: clear and exit
            if lib.XPending(dpy) == 0:
                # Block until X has an event or the deadline, without a busy spin.
                try:
                    r, _, _ = select.select([fd], [], [], min(remaining, 1.0))
                except (OSError, ValueError):
                    r = []
                if not r:
                    continue
                if lib.XPending(dpy) == 0:
                    continue
            lib.XNextEvent(dpy, evbuf)
            events_handled += 1
            etype = ctypes.cast(evbuf, ctypes.POINTER(ctypes.c_int)).contents.value
            if etype == SelectionClear:
                # Someone else grabbed the clipboard -> we're cancelled. Leave
                # their content; just stop owning and exit.
                return
            if etype == SelectionRequest:
                req = _as_request(evbuf)
                if req.selection != atoms.CLIPBOARD:
                    continue
                value = server.current_value()
                if value is None:
                    _send_selection_notify(lib, dpy, req, 0)
                    continue
                is_data = _write_property(lib, dpy, req, value, atoms)
                if is_data:
                    kind = _classify_data_serve(
                        req.requestor, time.monotonic() - owned_at, grace,
                        manager_requestors)
                    if kind == 'manager':
                        # Clipboard manager priming its cache: served, remembered,
                        # never counted (so the real paste still works).
                        manager_requestors.add(req.requestor)
                    elif kind == 'paste':
                        server.note_data_served()   # the user's real paste
        # Sequence exhausted OR timeout: relinquish so the clipboard goes empty.
        lib.XSetSelectionOwner(dpy, atoms.CLIPBOARD, 0, 0)
        lib.XFlush(dpy)
    finally:
        lib.XCloseDisplay(dpy)


def _read_values_from_stdin():
    """Values arrive on stdin NUL-separated so secrets never touch argv."""
    raw = sys.stdin.buffer.read()
    if not raw:
        return []
    parts = raw.split(b'\0')
    # A trailing NUL yields a final empty chunk; drop only that.
    if parts and parts[-1] == b'':
        parts.pop()
    return [p.decode('utf-8', 'replace') for p in parts]


def main(argv):
    """Entry point for the detached owner process.

    argv (optional): "--timeout N". Values come from stdin (NUL-separated). We
    detach from the terminal's stdout/stderr; failures are silent (the parent
    already fell back to a best-effort copy if spawning failed)."""
    timeout = None
    if '--timeout' in argv:
        i = argv.index('--timeout')
        try:
            timeout = float(argv[i + 1])
        except (IndexError, ValueError):
            timeout = None
    values = _read_values_from_stdin()
    try:
        serve_values(values, timeout=timeout)
    except (OSError, Exception):
        # Best effort: if we cannot own the selection there is nothing to clean
        # up (we never put anything on the clipboard).
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
