"""The main search/select curses UI.

Two input states resolve the conflict between "type letters to search" and
"single-letter commands":

  SEARCH state (default) - printable keys filter by title, live. the arrow keys
                           OR enter move into the result list (enter lands the
                           highlight on the first match; with no matches it is a
                           no-op). enter here never copies and never quits.
  SELECT state           - the highlight is on a result; the action keys act on
                           it. ENTER "continue"s INTO the entry view (columns +
                           copy/clip/edit). "/" drops back to the search box.

NAVIGATION mirrors the Azzio terminal UI (packages/azzio/render.c): the same
WASD / HJKL / arrow movement, "/" for search. The bottom line is a plain,
left-aligned "KEY label   KEY label" nav bar built the same way azzio draws its
verbs (a keycap, a space, a dim label, a gap) -- NOT centred and NOT coloured,
just structured the same.

Because W/A/S/D and H/J/K/L are movement, the single-letter ACTIONS deliberately
avoid every movement key: n new, x delete. ENTER opens the entry view (which
answers to a single keypress: a number clips a column, "c" clips every column in
order, "e" edits, "s" toggles STAY OPEN) -- the old "v show", "e edit" and "m
multi" verbs are folded into that view. Clipping there quits the app unless STAY
OPEN is on. There is no Tab. ESC never quits -- it jumps back to the START of the
UI (clears the query, highlight to the top) and is safe to spam. Only q / Q quit.
"""

import curses

import forms
import new_entry

SEARCH, SELECT = 0, 1

# Movement keys, mirroring azzio: WASD + HJKL + arrows all drive the vertical
# list (there is only a vertical axis here, exactly like azzio's own list, whose
# nav labels every one of these clusters simply "move"). Held as sets of the
# ordinals so the handlers can test membership cheaply.
_UP_KEYS = {curses.KEY_UP, ord('w'), ord('W'), ord('k'), ord('K'),
            ord('a'), ord('A'), ord('h'), ord('H'), curses.KEY_LEFT}
_DOWN_KEYS = {curses.KEY_DOWN, ord('s'), ord('S'), ord('j'), ord('J'),
              ord('d'), ord('D'), ord('l'), ord('L'), curses.KEY_RIGHT}
_MOVE_KEYS = _UP_KEYS | _DOWN_KEYS


# The nav bar is drawn by forms.nav_bar (shared with the detail view so both
# bottom bars look identical); _nav is kept as a thin local alias.
_nav = forms.nav_bar


# The SELECT-mode nav: the packed movement cluster (one "cell" whose key glyphs
# read "WASD HJKL <arrows>" and whose label is "move"), then the verbs. Kept as
# data so the drawing and the tests share one definition.
#
# ENTER now "continue"s INTO the entry view (the hub that shows the columns and
# offers copy-column / clip-in-order / edit) rather than copying the password
# outright -- so the old "v show", "e edit" and "m multi" verbs are gone, folded
# into that view.
_NAV_MOVE = ('WASD HJKL ←↑→↓', 'move')
_NAV_SELECT = [
    _NAV_MOVE,
    ('ENTER', 'continue'),
    ('n', 'new'),
    ('x', 'delete'),
    ('/', 'search'),
    ('ESC', 'back'),
    ('Q', 'quit'),
]


class App:
    def __init__(self, stdscr, store):
        self.stdscr = stdscr
        self.store = store
        self.query = ''
        self.mode = SEARCH
        self.dirty = False
        self.sel = 0
        self.status = ''
        self.results = []
        # STAY OPEN toggle for the entry view, owned here so its state lives for
        # this whole run (App is built once per launch) and survives opening and
        # re-opening entries -- yet resets on the next launch (not persisted). A
        # one-element list so entry_view can flip it in place. Off by default.
        self.stay_open = [False]
        self.refilter()

    # ----- data -----

    def refilter(self):
        self.results = self.store.filter(self.query)
        if self.sel >= len(self.results):
            self.sel = max(0, len(self.results) - 1)

    def selected_entry(self):
        if 0 <= self.sel < len(self.results):
            return self.results[self.sel]
        return None

    def move(self, ch):
        if not self.results:
            return
        if ch in _UP_KEYS:
            self.sel = (self.sel - 1) % len(self.results)
        else:
            self.sel = (self.sel + 1) % len(self.results)

    def reset(self):
        """ESC: jump back to the START of the UI -- clear the search query, put
        the highlight at the top, and return to the search box. Spammable (a no-op
        once already at the start)."""
        self.query = ''
        self.sel = 0
        self.mode = SEARCH
        self.refilter()

    # ----- drawing -----

    def draw(self):
        s = self.stdscr
        s.erase()
        h, w = s.getmaxyx()
        header = ' passwords '
        forms.addstr(s, 0, 0, header + ' ' * max(0, w - len(header)),
                     curses.A_REVERSE)

        caret = ' _' if self.mode == SEARCH else ''
        forms.addstr(s, 1, 0, 'Search: ' + self.query + caret)
        forms.addstr(s, 2, 0, '-' * (w - 1))

        top = 3
        bottom = h - 1
        visible = max(1, bottom - top)
        start = 0
        if self.sel >= visible:
            start = self.sel - visible + 1
        row = top
        for idx in range(start, len(self.results)):
            if row >= bottom:
                break
            e = self.results[idx]
            names = e.element_names()
            meta = '(%d: %s)' % (len(names), ', '.join(names))
            marker = '> ' if idx == self.sel else '  '
            line = '%s%s   %s' % (marker, e.title, meta)
            if idx == self.sel:
                attr = curses.A_REVERSE if self.mode == SELECT else curses.A_BOLD
            else:
                attr = 0
            forms.addstr(s, row, 0, line, attr)
            row += 1
        if not self.results:
            forms.addstr(s, top, 0, '(no matches)', curses.A_DIM)

        if self.status:
            forms.addstr(s, h - 1, 0, self.status, curses.A_DIM)
        else:
            _nav(s, h - 1, _NAV_SELECT)
        self.status = ''
        s.refresh()

    # ----- input -----

    def run(self):
        curses.curs_set(0)
        while True:
            self.draw()
            try:
                ch = self.stdscr.getch()
            except KeyboardInterrupt:
                break
            if self.mode == SEARCH:
                if not self.handle_search(ch):
                    break
            else:
                if not self.handle_select(ch):
                    break
        return self.dirty

    def handle_search(self, ch):
        # Only q/Q quit. ESC never quits -- it resets to the start of the UI
        # (spammable): clears the query and puts the highlight back at the top.
        if ch in (ord('q'), ord('Q')):
            return False
        if ch == forms.ESC:
            self.reset()
            return True
        if ch in (curses.KEY_UP, curses.KEY_DOWN):
            # In SEARCH only the ARROWS move into the list -- letters are query
            # text here (WASD/HJKL become movement once in SELECT).
            if self.results:
                self.mode = SELECT
                self.move(ch)
            return True
        if ch in forms.ENTER_KEYS:
            # ENTER from the search box jumps the cursor DOWN into the results
            # list (SELECT mode, highlight on the first match) -- it does NOT copy
            # and does NOT quit. With no matches it is a no-op (stay in search).
            if self.results:
                self.mode = SELECT
                self.sel = 0
            return True
        if ch in forms.BACKSPACE_KEYS:
            self.query = self.query[:-1]
            self.refilter()
            return True
        if ch == ord('n'):
            # 'n' creates a new entry from the search box too, so the first entry
            # can be added on an empty store (this used to need Tab, now removed).
            self.action_new()
            return True
        if 32 <= ch <= 126:
            self.query += chr(ch)
            self.refilter()
            return True
        return True

    def handle_select(self, ch):
        # Only q/Q quit. ESC never quits -- it resets to the start of the UI
        # (spammable); "/" drops back to the search box.
        if ch in (ord('q'), ord('Q')):
            return False
        if ch == forms.ESC:
            self.reset()
            return True
        if ch == ord('/'):
            self.mode = SEARCH
            return True
        if ch in _MOVE_KEYS:
            self.move(ch)
            return True
        if ch in forms.BACKSPACE_KEYS:
            self.mode = SEARCH
            self.query = self.query[:-1]
            self.refilter()
            return True
        if ch in forms.ENTER_KEYS:
            # ENTER "continue"s INTO the entry view (columns + clip/edit), rather
            # than copying the password and closing. The view handles the clipboard
            # work itself and answers to single keypresses; here we only propagate
            # a full quit -- which now fires either on q OR on a clip while STAY
            # OPEN is off -- and mark the store dirty if an edit changed the entry.
            # self.stay_open is threaded in so the toggle persists across re-opens
            # for this run.
            entry = self.selected_entry()
            if entry is None:
                return True
            changed, quit_ = forms.entry_view(self.stdscr, entry, self.stay_open)
            if changed:
                self.dirty = True
            if quit_:
                return False
            return True
        c = chr(ch) if 32 <= ch <= 126 else ''
        if c == 'n':
            self.action_new()
            return True
        entry = self.selected_entry()
        if entry is None:
            return True
        if c == 'x':
            if forms.confirm_delete(self.stdscr, entry):
                self.store.entries.remove(entry)
                self.dirty = True
                self.refilter()
        return True

    # ----- actions -----

    def action_new(self):
        entry = new_entry.new_entry(self.stdscr)
        if entry is None:
            return
        self.store.entries.append(entry)
        self.dirty = True
        self.query = ''
        self.refilter()
        if entry in self.results:
            self.sel = self.results.index(entry)
        self.status = 'added: ' + entry.title


def _run(scr, store):
    # A single ESC must register at once. Without this curses waits ~1s after ESC
    # to see if it starts an escape sequence, so a lone ESC seemed to need a second
    # keypress to take effect -- 25ms is long enough for real sequences, short
    # enough to feel instant. Set inside the wrapper (after initscr) so the
    # terminal is initialised. Fall back silently on the rare build without it.
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass
    return App(scr, store).run()


def run(store):
    """Run the UI over store. Returns True if the store was modified."""
    return curses.wrapper(_run, store)
