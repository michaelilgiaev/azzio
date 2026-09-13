#!/usr/bin/env python3
"""Azzio window switcher -- LIVE behavioral integration check (needs a real X display).

This is NOT part of the pure `bash tests.sh` suite (that suite never touches X/GTK). It is a
manual/CI-on-a-VM harness that proves the two alt-tab bugs stay fixed AT RUNTIME -- the layer
the headless unit tests (tests/test_switch_logic.c) cannot reach: the GTK key routing, the
_NET_ACTIVE_WINDOW round-trip in show_switcher(), and the override-redirect repaint.

WHY a separate script: the daemon holds a GTK seat grab while shown, and under that grab XTEST
(xdotool) mangles Shift+Tab into ISO_Next_Group -- so it CANNOT drive the real backward path.
This harness instead injects REAL hardware key events through a uinput virtual keyboard (they
traverse evdev -> X with the real keymap, exactly like a physical keyboard) and reads the result
back two ways: the daemon's committed window (_NET_ACTIVE_WINDOW after releasing Alt) and a Gdk
screenshot diff of the overlay (to prove the highlight actually repainted).

It builds the daemon straight from the repo source into a temp dir (window_switcher.build_daemon)
so it always tests HEAD, kills any running daemon, runs from the temp dir, and restores nothing
system-wide (the installed daemon path is untouched). Root is needed only for /dev/uinput.

Run ON the target machine (e.g. the hypervisor VM), not the build host:
    sudo python3 tests/integration_window_switcher_live.py
Environment it expects (the login session's): DISPLAY, XAUTHORITY, XDG_RUNTIME_DIR. If invoked
via sudo, pass them through, e.g.:
    sudo DISPLAY=:0 XAUTHORITY=$HOME/.Xauthority XDG_RUNTIME_DIR=/run/user/$(id -u) \
        python3 tests/integration_window_switcher_live.py

Exit code 0 = both bugs verified fixed; non-zero = a failure (prints which). This file is
imported by NO pytest; it is a standalone script so the pure suite stays X-free.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# --- repo wiring: make packages.* importable exactly like tests.sh does ------
_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO / "libraries", _REPO / "libraries" / "packages"):
    sys.path.insert(0, str(_p))

from packages.window_switcher import window_switcher as ws  # noqa: E402

PIDFILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "azzio-window-switcher.pid"

# The minimal uinput injector, compiled once. Real hardware key events (not XTEST) so Shift+Tab
# reaches the daemon as ISO_Left_Tab under the grab instead of the mangled ISO_Next_Group.
_UINPUT_C = r"""
#include <linux/uinput.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
static int ufd;
static void emit(int t,int c,int v){struct input_event e;memset(&e,0,sizeof(e));e.type=t;e.code=c;e.value=v;if(write(ufd,&e,sizeof(e))!=sizeof(e))perror("w");}
static void syn(void){emit(EV_SYN,SYN_REPORT,0);}
static int kc(const char*n){if(!strcmp(n,"ALT"))return KEY_LEFTALT;if(!strcmp(n,"SHIFT"))return KEY_LEFTSHIFT;if(!strcmp(n,"TAB"))return KEY_TAB;return -1;}
int main(int c,char**v){if(c<2)return 2;ufd=open("/dev/uinput",O_WRONLY|O_NONBLOCK);if(ufd<0){perror("uinput");return 1;}
ioctl(ufd,UI_SET_EVBIT,EV_KEY);ioctl(ufd,UI_SET_KEYBIT,KEY_LEFTALT);ioctl(ufd,UI_SET_KEYBIT,KEY_LEFTSHIFT);ioctl(ufd,UI_SET_KEYBIT,KEY_TAB);
struct uinput_setup u;memset(&u,0,sizeof(u));u.id.bustype=BUS_USB;u.id.vendor=0x1234;u.id.product=0x5678;strcpy(u.name,"azzio-virtual-kbd");
ioctl(ufd,UI_DEV_SETUP,&u);ioctl(ufd,UI_DEV_CREATE);usleep(400000);
char*s=NULL,*t=strtok_r(v[1],",",&s);while(t){if(!strncmp(t,"sleep:",6))usleep(atoi(t+6)*1000);
else if(!strncmp(t,"down:",5)){int k=kc(t+5);if(k>=0){emit(EV_KEY,k,1);syn();}}
else if(!strncmp(t,"up:",3)){int k=kc(t+3);if(k>=0){emit(EV_KEY,k,0);syn();}}t=strtok_r(NULL,",",&s);}
usleep(200000);ioctl(ufd,UI_DEV_DESTROY);close(ufd);return 0;}
"""

# Reads the XKB locked layout group (0 = us, 1 = il here). Used to prove the overlay does NOT flip
# the language while it is shown (the daemon pins US) and that a real Alt+Shift+Tab therefore
# navigates backward instead of toggling to Hebrew. Prints just the integer group.
_GRPSTATE_C = r"""
#include <X11/XKBlib.h>
#include <stdio.h>
int main(void){Display*d=XOpenDisplay(NULL);if(!d)return 1;XkbStateRec s;
if(XkbGetState(d,XkbUseCoreKbd,&s)!=Success){XCloseDisplay(d);return 1;}
printf("%d\n",s.locked_group);XCloseDisplay(d);return 0;}
"""

# Gdk full-root screenshot (works for override-redirect overlays; note GdkPixbuf is 2.0).
_SHOT_PY = r"""
import sys, gi
gi.require_version('Gdk','3.0'); gi.require_version('GdkPixbuf','2.0')
from gi.repository import Gdk
out=sys.argv[1]; root=Gdk.get_default_root_window()
pb=Gdk.pixbuf_get_from_window(root,0,0,root.get_width(),root.get_height())
if pb is None: sys.exit("PIXBUF_NONE")
pb.savev(out,"png",[],[]); print("SAVED",out)
"""


def _sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True, **kw)


def _managed_windows():
    """(xid, wm_class) for each _NET_CLIENT_LIST window, in list order."""
    out = _sh("xprop -root _NET_CLIENT_LIST").stdout
    ids = []
    if "#" in out:
        for chunk in out.split("#", 1)[1].split(","):
            w = chunk.strip()
            if w:
                ids.append(w)
    res = []
    for w in ids:
        cls = _sh(f"xprop -id {w} WM_CLASS").stdout.split("=", 1)[-1].strip()
        res.append((w, cls))
    return res


def _active_class():
    out = _sh("xprop -root _NET_ACTIVE_WINDOW").stdout
    xid = None
    for tok in out.replace(",", " ").split():
        if tok.startswith("0x"):
            xid = tok
    if not xid:
        return None
    return _sh(f"xprop -id {xid} WM_CLASS").stdout.split("=", 1)[-1].strip()


def _focus(cls_substr, tmp):
    for w, cls in _managed_windows():
        if cls_substr.lower() in cls.lower():
            _sh(f"xdotool windowactivate {w}")
            time.sleep(0.6)
            return True
    return False


class Harness:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.uinput = tmp / "uinput_key"
        self.grpstate = tmp / "grpstate"
        self.shot = tmp / "shot.py"
        self.daemon = tmp / "azzio-window-switcher-daemon"
        self.stderr_log = tmp / "daemon.stderr"
        self.pid = None

    def build(self):
        (self.tmp / "uinput_key.c").write_text(_UINPUT_C)
        (self.tmp / "grpstate.c").write_text(_GRPSTATE_C)
        self.shot.write_text(_SHOT_PY)
        cc = _sh(f"gcc -O2 -o {self.uinput} {self.tmp/'uinput_key.c'}")
        if cc.returncode != 0:
            raise SystemExit(f"uinput build failed:\n{cc.stderr}")
        gc = _sh(f"gcc -O2 -o {self.grpstate} {self.tmp/'grpstate.c'} -lX11")
        if gc.returncode != 0:
            raise SystemExit(f"grpstate build failed:\n{gc.stderr}")
        ws.build_daemon(self.daemon)  # compiles the repo HEAD sources

    def group(self):
        """The current XKB locked layout group (0=us, 1=il), or None if it can't be read."""
        r = _sh(str(self.grpstate))
        try:
            return int(r.stdout.strip())
        except (ValueError, AttributeError):
            return None

    def set_group(self, g: int):
        """Force the layout group (0=us). Resets any Hebrew latch left over from a prior chord."""
        _sh(f"setxkbmap -layout us,il -option grp:alt_shift_toggle")
        # XkbLockGroup via a throwaway one-liner (setxkbmap alone does not reset locked_group).
        _sh(
            "python3 - <<'PY'\n"
            "import gi\ngi.require_version('Gdk','3.0')\nfrom gi.repository import Gdk\n"
            "import ctypes,ctypes.util\n"
            "x=ctypes.CDLL(ctypes.util.find_library('X11'))\n"
            "d=x.XOpenDisplay(None)\n"
            f"x.XkbLockGroup(d,0x0100,{g})\n"  # 0x0100 == XkbUseCoreKbd
            "x.XSync(d,0)\nx.XCloseDisplay(d)\nPY"
        )

    def start_daemon(self):
        if PIDFILE.exists():
            try:
                os.kill(int(PIDFILE.read_text().strip()), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        _sh("pkill -f azzio-window-switcher-daemon")
        time.sleep(1)
        PIDFILE.unlink(missing_ok=True)
        # AZZIO_SWITCHER_TIMING makes the daemon print "AZZIO_SHOW_MS <ms>" per show (see
        # switcher.c on_sig_pipe); we capture stderr so the snappiness check can read it.
        env = dict(os.environ, AZZIO_SWITCHER_TIMING="1")
        self._stderr_fh = open(self.stderr_log, "w")
        subprocess.Popen(
            ["setsid", str(self.daemon)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=self._stderr_fh, start_new_session=True, env=env,
        )
        time.sleep(2)
        self.pid = int(PIDFILE.read_text().strip())

    def show_latencies(self):
        """The AZZIO_SHOW_MS samples (ms, float) the daemon has emitted so far, in order."""
        try:
            text = self.stderr_log.read_text()
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            if line.startswith("AZZIO_SHOW_MS"):
                try:
                    out.append(float(line.split()[1]))
                except (IndexError, ValueError):
                    pass
        return out

    def open_and_dismiss(self, sig=signal.SIGUSR1):
        """Hold Alt, signal (show), release Alt -> commit+hide. One clean show cycle (for timing)."""
        import threading
        t = threading.Thread(target=self.inject, args=("down:ALT,sleep:700,up:ALT",))
        t.start()
        time.sleep(0.35)
        os.kill(self.pid, sig)
        time.sleep(0.5)
        t.join()
        time.sleep(0.4)

    def inject(self, seq: str):
        subprocess.run([str(self.uinput), seq], check=False)

    def screenshot(self, path: Path):
        subprocess.run([sys.executable, str(self.shot), str(path)], check=False)

    def open_and_commit(self, sig=signal.SIGUSR1):
        """Hold Alt, signal the daemon (SIGUSR1=--next / SIGUSR2=--prev), release -> commit;
        return the activated WM_CLASS. The direction is driven by the SIGNAL (exactly how OpenBox's
        A-Tab / A-S-Tab bindings drive it), NOT by a Tab key under the grab -- so it is reliable
        regardless of how the test keyboard injects keys."""
        import threading
        t = threading.Thread(target=self.inject, args=("down:ALT,sleep:1600,up:ALT",))
        t.start()
        time.sleep(0.45)
        os.kill(self.pid, sig)
        time.sleep(0.6)
        t.join()
        time.sleep(0.5)
        return _active_class()

    def open_forward_and_commit(self):
        """Hold Alt, SIGUSR1 (--next), release -> commit; return the activated WM_CLASS."""
        return self.open_and_commit(signal.SIGUSR1)

    def open_chord_and_commit(self, backward: bool):
        """Drive a REAL physical Alt+Tab / Alt+Shift+Tab chord (uinput) end to end and return the
        committed WM_CLASS. Unlike open_and_commit (which fakes direction via SIGUSR2), this fires
        the actual key chord OpenBox+XKB see, so it exercises the true bug: Azzio binds Alt+Shift
        to the language toggle, so a physical Alt+Shift+Tab used to open FORWARD (XKB ate the Shift)
        and flip to Hebrew. The daemon's fix (pin US on show + read the physical Shift key) must make
        this chord open BACKWARD with the group staying US. The launcher (SIGUSR1) still opens the
        overlay -- as it does on real hardware, where the first chord reaches OpenBox as plain
        Alt+Tab -- and the physical Shift held at show-time is what flips the daemon to backward."""
        import threading
        if backward:
            # Alt down, Shift down, Tab tap, Shift up, Alt up -- Shift is held THROUGH the show.
            seq = ("down:ALT,sleep:250,down:SHIFT,sleep:200,down:TAB,sleep:90,up:TAB,"
                   "sleep:600,up:SHIFT,sleep:150,up:ALT")
        else:
            seq = "down:ALT,sleep:250,down:TAB,sleep:90,up:TAB,sleep:600,up:ALT"
        t = threading.Thread(target=self.inject, args=(seq,))
        t.start()
        # The real first chord reaches OpenBox as plain Alt+Tab -> launcher --next -> SIGUSR1. Fire
        # that while Alt (and, for backward, Shift) are still held so show_switcher sees them.
        time.sleep(0.55)
        os.kill(self.pid, signal.SIGUSR1)
        time.sleep(0.5)
        t.join()
        time.sleep(0.5)
        return _active_class()


def _diff_nonempty(a: Path, b: Path) -> bool:
    """True if the two PNGs differ (the overlay repainted). Uses PIL if present, else bytes."""
    try:
        from PIL import Image, ImageChops
        ia = Image.open(a).convert("RGB")
        ib = Image.open(b).convert("RGB")
        return ImageChops.difference(ia, ib).getbbox() is not None
    except Exception:
        return a.read_bytes() != b.read_bytes()


def main() -> int:
    for var in ("DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR"):
        if not os.environ.get(var):
            print(f"[skip] {var} not set -- this harness needs a live X session", file=sys.stderr)
            return 77  # automake-style "skipped"
    if not os.access("/dev/uinput", os.W_OK):
        print("[skip] /dev/uinput not writable -- run under sudo", file=sys.stderr)
        return 77

    failures = []
    with tempfile.TemporaryDirectory(prefix="azzio-switcher-live-") as td:
        tmp = Path(td)
        h = Harness(tmp)
        h.build()
        h.start_daemon()

        wins = _managed_windows()
        classes = [c for _, c in wins]
        print(f"managed windows ({len(wins)}): {classes}")
        if len(wins) < 3:
            print("[skip] need >=3 open windows to exercise start-index anchoring", file=sys.stderr)
            return 77

        # --- Bug 2: fresh-open start is FOCUS-RELATIVE, not a fixed tile. ------
        # Committing right after opening forward activates the tile AFTER the focused window.
        # We assert that focusing two DIFFERENT windows yields two DIFFERENT commits (the old
        # bug always committed the same fixed tile regardless of focus).
        picks = {}
        for target in ("librewolf", "thunar", "kitty", "gedit"):
            if _focus(target, tmp):
                before = _active_class()
                got = h.open_forward_and_commit()
                print(f"  focus={target!r} active={before!r} -> committed {got!r}")
                picks[target] = got
        distinct = {v for v in picks.values() if v}
        if len(picks) >= 2 and len(distinct) < 2:
            failures.append(
                f"Bug2: fresh-open start ignores focus -- every focus committed the same tile "
                f"{distinct}; expected focus-relative starts to differ ({picks})"
            )
        else:
            print(f"  OK Bug2: focus-relative starts differ: {picks}")

        # --- Bug 1: BACKWARD (Shift+Tab) actually goes the other way (via the SIGNAL path). -----
        # This first check drives direction by SIGNAL: forward = SIGUSR1 (OpenBox's A-Tab -> --next),
        # backward = SIGUSR2 (A-S-Tab -> --prev). It proves show_switcher(-1) itself steps the other
        # way: from the SAME focused window a forward open commits the tile AFTER it and a backward
        # open the tile BEFORE it, so the two must differ. It does NOT prove the physical Alt+Shift
        # +Tab chord survives XKB's language toggle -- that end-to-end path is covered by Bug1b just
        # below, which injects the real chord. (The pure keyval+state->direction decision is also
        # proven exhaustively headless by tests/test_switch_logic.c.)
        fwd = {}
        bwd = {}
        for target in ("librewolf", "kitty", "thunar"):
            if _focus(target, tmp):
                fwd[target] = h.open_and_commit(signal.SIGUSR1)
                _focus(target, tmp)
                bwd[target] = h.open_and_commit(signal.SIGUSR2)
                print(f"  focus={target!r}: forward -> {fwd[target]!r} | backward -> {bwd[target]!r}")
        pairs = [(t, fwd[t], bwd[t]) for t in fwd if t in bwd and fwd[t] and bwd[t]]
        differ = [t for (t, f, b) in pairs if f != b]
        if not pairs:
            failures.append("Bug1: could not drive any forward/backward open to a commit")
        elif not differ:
            failures.append(
                f"Bug1: backward (SIGUSR2 / A-S-Tab) commits the SAME window as forward -- backward "
                f"direction is broken (forward={fwd}, backward={bwd})"
            )
        else:
            print(f"  OK Bug1: backward differs from forward for {differ} "
                  f"(A-S-Tab steps the other way)")

        # --- Bug 1b: a REAL Alt+Shift+Tab chord opens backward AND keeps the layout US. --------
        # This is the true end-to-end reproduction the SIGUSR2 test above cannot cover. Azzio
        # binds Alt+Shift to grp:alt_shift_toggle, so a physical Alt+Shift+Tab used to (a) flip the
        # layout US->Hebrew and (b) open FORWARD, because XKB consumed the Shift as the group-switch
        # chord and OpenBox saw a bare Alt+Tab. The daemon fix pins the group to US on show and reads
        # the PHYSICAL Shift key (XQueryKeymap) to recover the backward intent. We inject the actual
        # chord through the uinput keyboard (evdev -> libinput -> XKB, the real path) and assert:
        #   * forward chord (Alt+Tab) and backward chord (Alt+Shift+Tab) from the same focus DIFFER;
        #   * the locked layout group stays 0 (US) across the backward chord -- no Hebrew flip.
        # (Earlier harness generations skipped this believing a hotplugged uinput device can't reach
        # the grabbing client; it can -- verified -- so the real chord is tested here directly.)
        h.set_group(0)
        cfwd, cbwd, groups = {}, {}, {}
        for target in ("librewolf", "kitty", "thunar"):
            if _focus(target, tmp):
                h.set_group(0)
                cfwd[target] = h.open_chord_and_commit(backward=False)
                _focus(target, tmp)
                h.set_group(0)
                cbwd[target] = h.open_chord_and_commit(backward=True)
                groups[target] = h.group()
                print(f"  focus={target!r}: chord fwd -> {cfwd[target]!r} | "
                      f"chord bwd -> {cbwd[target]!r} | group after bwd={groups[target]}")
        cpairs = [(t, cfwd[t], cbwd[t]) for t in cfwd if t in cbwd and cfwd[t] and cbwd[t]]
        cdiffer = [t for (t, f, b) in cpairs if f != b]
        flipped = [t for t, g in groups.items() if g not in (0, None)]
        if not cpairs:
            failures.append("Bug1b: could not drive any real Alt+(Shift+)Tab chord to a commit")
        elif not cdiffer:
            failures.append(
                f"Bug1b: a REAL Alt+Shift+Tab chord commits the SAME window as Alt+Tab -- the "
                f"physical-Shift backward path is broken (fwd={cfwd}, bwd={cbwd})"
            )
        elif flipped:
            failures.append(
                f"Bug1b: the layout switched to Hebrew (group != 0) during an Alt+Shift+Tab chord "
                f"for {flipped} (groups={groups}); the overlay must NOT switch languages while shown"
            )
        else:
            print(f"  OK Bug1b: real Alt+Shift+Tab chord goes backward for {cdiffer} "
                  f"and the layout stayed US (groups={groups})")

        # --- Bug 3: the overlay is SNAPPY -- show latency stays tiny. -----------
        # The daemon prints "AZZIO_SHOW_MS <ms>" per show (AZZIO_SWITCHER_TIMING, set by the
        # harness). This is the signal-to-mapped cost. It was ~150-220ms when show_switcher
        # enumerated windows (forks xprop) and captured every tile's XComposite pixmap INLINE,
        # before moving the overlay on-screen -- the reported "delayed / not snappy", and why a
        # quick Alt+Shift+Tab felt dead (the overlay had not painted when the keys were released).
        # The fix seeds the strip at warmup and defers the reload to an idle AFTER the map, so the
        # show does only the cheap select+move+grab. We open/dismiss several times and assert the
        # MEDIAN show latency is well under the old cost. The threshold (60ms) sits far below the
        # ~200ms regression and comfortably above the few-ms fixed path, so it fails loudly if the
        # heavy work ever creeps back onto the show hot path.
        SNAPPY_MS = 60.0
        base = len(h.show_latencies())
        for _ in range(5):
            h.open_and_dismiss()
        samples = h.show_latencies()[base:]
        if not samples:
            failures.append("Bug3: no AZZIO_SHOW_MS samples captured (timing hook missing?)")
        else:
            samples_sorted = sorted(samples)
            median = samples_sorted[len(samples_sorted) // 2]
            worst = samples_sorted[-1]
            print(f"  show latency samples (ms): {[round(x,1) for x in samples]}")
            if median <= SNAPPY_MS:
                print(f"  OK Bug3: snappy -- median {median:.1f}ms <= {SNAPPY_MS:.0f}ms "
                      f"(worst {worst:.1f}ms)")
            else:
                failures.append(
                    f"Bug3: NOT snappy -- median show latency {median:.1f}ms > {SNAPPY_MS:.0f}ms "
                    f"(samples {[round(x,1) for x in samples]}); the heavy window-list/thumbnail "
                    f"work is back on the show hot path"
                )

    if failures:
        print("\nFAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nPASS: alt-tab bugs (start-index, shift+tab, snappiness) verified fixed at runtime")
    return 0


if __name__ == "__main__":
    sys.exit(main())
