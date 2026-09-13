"""Curses sub-screens and primitives: line/multi-line prompts, the entry view,
the element editor, the new-entry wizard, and the "type yes" confirmations (for
deleting an entry and for removing a column). Kept apart from terminal_user_interface.py
so each file stays small.

The entry view answers to a single keypress -- a NUMBER clips that column, "c"
clips every column in order, "e" edits, "s" toggles STAY OPEN -- and clipping
quits the whole app unless STAY OPEN is on for the session."""

import curses

import clipboard
from model import ESSENTIAL, clean_key, move_element, notes_last

ENTER_KEYS = (curses.KEY_ENTER, 10, 13)
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8)
ESC = 27


def addstr(win, y, x, text, attr=0):
    """Write text clipped to the window; never raises on edges/small terminals."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    text = text[:max(0, w - x - 1)]
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def nav_bar(win, y, pairs):
    """Draw a left-aligned "KEY label   KEY label ..." nav bar on row y.

    Structured like azzio's draw_nav: each verb is a keycap, a space, then a dim
    label, verbs separated by a 3-space gap. NOT centred and NOT coloured -- the
    keycap is drawn normal and the label A_DIM. `pairs` is a list of (key, label).
    Shared by the search UI (terminal_user_interface._nav) and the detail view so both bottom bars
    look identical."""
    _, w = win.getmaxyx()
    x = 0
    gap = '   '
    for i, (key, label) in enumerate(pairs):
        if i:
            addstr(win, y, x, gap, curses.A_DIM)
            x += len(gap)
        addstr(win, y, x, key)
        x += len(key)
        if label:
            addstr(win, y, x, ' ' + label, curses.A_DIM)
            x += 1 + len(label)
        if x >= w - 1:
            break


# Bottom navigation hints, one set per page. The bar at the bottom of every
# screen names exactly the keys that page answers to -- it is how the user learns
# the controls -- so each page passes its own set to nav_bar(). Nothing advertised
# here is a dead key on its page.
#
# The entry view (opened with ENTER on a result) is the hub that replaced the old
# "v show" / "e edit" / "m multi" verbs: it shows every column and answers to a
# SINGLE keypress (no typed input, no ENTER) -- a NUMBER clips that one column,
# "c" clips every column in order, "e" edits, "s" toggles STAY OPEN. Copies are
# paste-once (see clipboard.py). Clipping (a number or "c") QUITS the whole app
# right after, unless STAY OPEN has been toggled on for this session. The "s"
# label is rendered live to show the current state, so this list carries a
# placeholder that _nav_entry() replaces with the real "STAY OPEN: off/on".
_NAV_ENTRY = [
    ('1-9', 'clip column'),
    ('c', 'clip each column in order'),
    ('e', 'edit'),
    ('s', 'STAY OPEN'),
    ('ESC', 'back'),
    ('Q', 'quit'),
]


def _nav_entry(stay_open):
    """The entry-view nav bar pairs with the "s" cell reflecting the live STAY OPEN
    state ("STAY OPEN: on" when set, else "STAY OPEN: off"). Everything else is the
    static _NAV_ENTRY."""
    state = 'on' if stay_open else 'off'
    return [(k, ('STAY OPEN: ' + state) if k == 's' else lbl)
            for k, lbl in _NAV_ENTRY]
# New-entry: typing the title (the header updates live as you type). Every ENTER
# hint in the app reads "ENTER continue" (the sole exception is the SELECT-mode
# "ENTER copy") so the key does one consistent thing everywhere.
_NAV_NEW_TITLE = [
    ('ENTER', 'continue'),
    ('ESC', 'back'),
]
# New-entry: the column picker. Type a number then ENTER to open that column;
# ESC finishes and saves the entry. (Empty ENTER just notes "no input" and stays.)
_NAV_PICK = [
    ('ENTER', 'continue'),
    ('ESC', 'done'),
]
# New-entry: entering one column's value (its own NEW ENTRY-style page).
_NAV_VALUE = [
    ('ENTER', 'continue'),
    ('ESC', 'cancel'),
]
# Edit view (e): the element editor's key set, shown as a bottom bar like the
# rest of the app instead of a top hint line. "[ ]" reorder moves the highlighted
# column up/down (the title is pinned first).
_NAV_EDIT = [
    ('↑↓', 'move'),
    ('[ ]', 'reorder'),
    ('e', 'edit'),
    ('k', 'rename'),
    ('a', 'add'),
    ('r', 'remove'),
    ('ESC', 'back'),
]
# The reorder screen (shared by the editor and the new-entry column picker):
# arrows move the highlight, "[" / "]" move the highlighted column up / down.
_NAV_REORDER = [
    ('↑↓', 'move'),
    ('[', 'up'),
    (']', 'down'),
    ('ESC', 'done'),
]


def prompt_line(win, label, initial='', y=None, on_change=None, nav=None,
                note=None):
    """Single-line input. Returns the string, or None on Esc.

    Drawn as plain text (no reverse-video highlight) so typing does not paint a
    white bar. By default it sits on the bottom row; pass `y` to place it on a
    specific row (used so the new-entry inputs sit right under their header).
    `on_change(text)`, if given, is called with the current buffer after every
    edit -- the new-entry wizard uses it to refresh its "NEW ENTRY <title>"
    header live as the user types. `nav`, if given, is a nav_bar() pairs list
    drawn on the bottom row while the prompt is live so the page keeps its
    contextual key hints (the cursor stays on the input row). `note`, if given, is
    a dim one-line message drawn just BELOW the input row (used to echo "nothing
    written -- press ENTER again to exit"); it shows only while the input is not on
    the bottom row so it never collides with the nav bar."""
    buf = list(initial)
    curses.curs_set(1)
    try:
        while True:
            h, w = win.getmaxyx()
            row = h - 1 if y is None else y
            if nav is not None and row != h - 1:
                win.move(h - 1, 0)
                win.clrtoeol()
                nav_bar(win, h - 1, nav)
            if note and row + 1 < h - 1:
                win.move(row + 1, 0)
                win.clrtoeol()
                addstr(win, row + 1, 0, note, curses.A_DIM)
            win.move(row, 0)
            win.clrtoeol()
            text = label + ''.join(buf)
            addstr(win, row, 0, text)
            win.move(row, min(len(text), w - 1))
            ch = win.getch()
            if ch in ENTER_KEYS:
                return ''.join(buf)
            if ch == ESC:
                return None
            if ch in BACKSPACE_KEYS:
                if buf:
                    buf.pop()
            elif 32 <= ch <= 126:
                buf.append(chr(ch))
            else:
                continue
            if on_change is not None:
                on_change(''.join(buf))
    finally:
        curses.curs_set(0)


def prompt_multiline(win, label, start_row=None):
    """Collect note lines until an empty line is entered. Returns joined text.

    Default (start_row=None) is the standalone screen used by the editor: header
    on row 0, lines below. When start_row is given (the new-entry value page) the
    caller has already drawn the NEW ENTRY header + added list, so we render the
    prompt/lines from start_row down and keep the value-page key hints on the
    bottom row."""
    lines = []
    standalone = start_row is None
    top = 2 if standalone else start_row
    while True:
        if standalone:
            win.erase()
            addstr(win, 0, 0, label + ' (enter an empty line to finish):',
                   curses.A_BOLD)
        else:
            # Clear just the note region (header/added list drawn by the caller).
            h, _ = win.getmaxyx()
            for r in range(top, h - 1):
                win.move(r, 0)
                win.clrtoeol()
            addstr(win, top - 1, 0, 'add a note line; empty line finishes',
                   curses.A_DIM)
        for i, l in enumerate(lines):
            addstr(win, top + 1 + i, 2, l)
        input_row = None if standalone else top + 1 + len(lines)
        line = prompt_line(win, '> ', y=input_row,
                           nav=None if standalone else _NAV_VALUE)
        if line is None or line == '':
            break
        lines.append(line)
    return '\n'.join(lines)


def _label(key):
    """Display form of an element key. The built-in keys are stored lower-case and
    shown title-cased ("username" -> "Username"); a CUSTOM column is shown exactly
    as the user typed it (their capitalization stands -- we never re-case it)."""
    return _BUILTIN_LABELS.get(key, key)


def _copyable_columns(entry):
    """The columns the user can single-copy, numbered top to bottom: every element
    except the title, notes forced last (so the numbering matches the on-screen
    order). Returns a list of [key, value]. Notes IS copyable as a single column
    (a user may want its text) even though it is excluded from the ordered clip."""
    return [e for e in entry.display_elements() if e[0] != 'title']


def _render_entry(win, entry, note=None):
    """Draw the entry view body -- the title, then each column numbered for
    single-copy, notes rendered last under a blank line -- and return the row just
    below it (where the action input goes). `note`, if set, is a dim status line
    (e.g. "clipped Password -- paste once") shown under the body.

    Layout mirrors PROMPT.md: title first, then columns, then a blank line and the
    Notes block always at the bottom, its lines indented under the label."""
    win.erase()
    h, _ = win.getmaxyx()
    addstr(win, 0, 0, 'Title) ' + entry.title, curses.A_BOLD)
    row = 2  # blank line separates the title from the columns
    columns = _copyable_columns(entry)
    for n, (key, value) in enumerate(columns, 1):
        if row >= h - 2:
            break
        label = _label(key)
        if key == 'notes' or '\n' in value:
            # Notes (and any multi-line value) is a block: "N) Label)" then its
            # lines indented beneath. A blank line above it sets it apart.
            row += 1
            if row >= h - 2:
                break
            addstr(win, row, 0, '%d) %s)' % (n, label), curses.A_BOLD)
            row += 1
            for vline in (value.split('\n') if value else []):
                if row >= h - 2:
                    break
                addstr(win, row, 3, vline)
                row += 1
        else:
            addstr(win, row, 0, '%d) %s) ' % (n, label), curses.A_BOLD)
            addstr(win, row, len('%d) %s) ' % (n, label)), value)
            row += 1
    if note:
        row += 1
        addstr(win, min(row, h - 2), 0, note, curses.A_DIM)
        row += 1
    return columns, min(row + 1, h - 2)


def entry_view(win, entry, stay_open=None, do_copy=None, do_sequence=None):
    """The entry hub: show every column and act on this entry with ONE keypress.

    Replaces the old "v show" (this is that display), "e edit" (now the "e" action
    here), and "m multi" (now the "c" action). There is no typed input and no
    ENTER -- the user just presses a key:
      * a NUMBER  -> clip that one column (paste once, then it clears); notes is
                     clippable too;
      * "c"       -> clip every column in order (email, username, password, ...)
                     so the user pastes them one after another, then it clears;
      * "e"       -> edit this entry (change / rename / add / remove / reorder);
      * "s"       -> toggle STAY OPEN (see below);
      * ESC       -> back to the list; q/Q -> quit the whole app.

    Clipping (a number or "c") QUITS the whole app immediately afterward, so the
    user copies and is dropped straight back to the shell -- UNLESS STAY OPEN is
    on. STAY OPEN is a per-session toggle: off by default, "s" turns it on for the
    lifetime of this run (so clipping then keeps the manager open), and it is NOT
    persisted -- a fresh launch starts off again. Its state lives in the caller's
    `stay_open` holder (a one-element list) so it survives leaving and re-opening
    this view within the same run; if none is passed a local one is used.

    do_copy(value) and do_sequence(values) perform the actual clipboard work; they
    default to clipboard.copy / clipboard.copy_sequence and are injectable for
    tests. Returns (changed, quit): `changed` True if an edit modified the entry,
    `quit` True when the user pressed q OR clipped while STAY OPEN was off."""
    if do_copy is None:
        do_copy = clipboard.copy
    if do_sequence is None:
        do_sequence = clipboard.copy_sequence
    if stay_open is None:
        stay_open = [False]
    changed = False
    note = None
    while True:
        columns, _row = _render_entry(win, entry, note)
        note = None
        h, _ = win.getmaxyx()
        nav_bar(win, h - 1, _nav_entry(stay_open[0]))
        ch = win.getch()
        if ch == ESC:
            return changed, False            # ESC -> back to the list
        if ch in (ord('q'), ord('Q')):
            return changed, True             # q -> full quit
        if ch in (ord('s'), ord('S')):
            stay_open[0] = not stay_open[0]  # toggle for the rest of the session
            continue
        if ch in (ord('e'), ord('E')):
            ch_edit, quit_ = edit_entry(win, entry)
            changed = changed or ch_edit
            if quit_:
                return changed, True
            continue
        if ch in (ord('c'), ord('C')):
            seq = [v for _k, v in entry.copy_sequence()]
            if not seq:
                note = '(no columns to clip in order -- add some first)'
                continue
            do_sequence(seq)
            # Clipped -> quit unless the user asked us to STAY OPEN this session.
            if not stay_open[0]:
                return changed, True
            names = ', '.join(_label(k) for k, _v in entry.copy_sequence())
            note = 'clipping in order: %s -- paste each, then it clears' % names
            continue
        if ord('1') <= ch <= ord('9'):
            n = ch - ord('0')
            if 1 <= n <= len(columns):
                key, value = columns[n - 1]
                do_copy(value)
                if not stay_open[0]:
                    return changed, True     # clipped -> quit unless STAY OPEN
                note = 'clipped %s -- paste once, then it clears' % _label(key)
            else:
                note = '(no column %d -- press 1-%d)' % (n, len(columns))
            continue
        # Any other key: remind the user of the single-press controls.
        note = ('(press a column number to clip, "c" to clip in order, "e" to '
                'edit, "s" for STAY OPEN)')


# Delete confirmation: same shape as the new-entry title screen. Type "yes" then
# ENTER to delete; ESC cancels. Its ENTER hint reads "ENTER continue" like every
# other page (not "yes+ENTER delete").
_NAV_DELETE = [
    ('ENTER', 'continue'),
    ('ESC', 'cancel'),
]


def confirm_yes(win, header, message, verb='delete'):
    """Shared "type yes to confirm" screen. Draws `header` (bold) on row 0 and
    `message` on row 2, then asks the user to type "yes". You type "yes" and press
    ENTER to continue (the destructive action). If "yes" was NOT written (empty or
    anything else) it does not silently cancel -- it echoes that "yes" was not
    written and that pressing ENTER again exits; a second such ENTER cancels. ESC
    cancels at any time. `verb` only tweaks the warning wording. Returns True only
    when "yes" was confirmed. Backs both entry deletion and element removal."""
    win.erase()
    addstr(win, 0, 0, header, curses.A_BOLD)
    addstr(win, 2, 0, message)
    warned = False
    while True:
        note = ('"yes" was not written -- press ENTER again to exit, or type '
                '"yes" to %s' % verb) if warned else None
        ans = prompt_line(win, 'Type "yes" to confirm: ', y=4, nav=_NAV_DELETE,
                          note=note)
        if ans is None:
            return False                     # ESC -> cancel
        if ans.strip() == 'yes':
            return True                      # confirmed
        if warned:
            return False                     # second non-"yes" ENTER -> exit
        warned = True                        # first non-"yes" ENTER -> warn, stay


def confirm_delete(win, entry):
    """Confirm deleting an entry (the "type yes" screen). Returns True only when
    "yes" was confirmed. See confirm_yes for the exact keystroke behavior."""
    return confirm_yes(
        win, 'DELETE: ' + entry.title,
        'This permanently removes the entry from the store.', verb='delete')


# The offered columns after a title is entered. Order is the menu order; the key
# is what gets stored (lower-case, matching the on-disk element keys). "Notes" is
# multi-line; everything else is a single line. The final "custom" slot lets the
# user name their own column.
_COLUMN_CHOICES = [
    ('Email', 'email', False),
    ('Username', 'username', False),
    ('Password', 'password', False),
    ('Notes', 'notes', True),
]

# Display labels for the built-in keys only (title-cased). Custom keys are NOT in
# here, so _label() shows them verbatim -- the user's own capitalization wins.
_BUILTIN_LABELS = {key: label for label, key, _ml in _COLUMN_CHOICES}


def edit_entry(win, entry):
    """Interactive element editor.

    Returns (changed, quit): `changed` is True if any element changed; `quit` is
    True only if the user pressed q (a FULL quit that the caller propagates out of
    the app). ESC just leaves the editor (back), quit stays False.

    Notes is pinned to the bottom: the element list is normalized notes-last on
    entry so the editor's indices match what is shown, and the reorder keys refuse
    to move notes (or move anything past it)."""
    entry.elements[:] = notes_last(entry.elements)
    changed = False
    sel = 0
    while True:
        win.erase()
        h, _ = win.getmaxyx()
        addstr(win, 0, 0, 'EDIT: ' + entry.title, curses.A_BOLD)
        row = 2
        for i, (key, value) in enumerate(entry.elements):
            if row >= h - 1:
                break
            marker = '> ' if i == sel else '  '
            shown = value.replace('\n', ' / ')
            attr = curses.A_REVERSE if i == sel else 0
            addstr(win, row, 0, '%s%s) %s' % (marker, key, shown), attr)
            row += 1
        nav_bar(win, h - 1, _NAV_EDIT)   # bottom bar teaches the keys
        ch = win.getch()
        if ch in (ord('q'), ord('Q')):
            if changed:
                entry.mark_dirty()   # edited -> re-serialize canonically on save
            return changed, True     # q = FULL quit
        if ch == ESC:
            if changed:
                entry.mark_dirty()
            return changed, False    # ESC = back to the list only
        if not entry.elements:
            continue
        if ch == curses.KEY_UP:
            sel = (sel - 1) % len(entry.elements)
        elif ch == curses.KEY_DOWN:
            sel = (sel + 1) % len(entry.elements)
        elif ch == ord('['):
            # Reorder: move the highlighted element up (title stays pinned first).
            new_sel = move_element(entry.elements, sel, -1)
            if new_sel != sel:
                changed = True
            sel = new_sel
        elif ch == ord(']'):
            new_sel = move_element(entry.elements, sel, +1)
            if new_sel != sel:
                changed = True
            sel = new_sel
        elif ch in (ord('e'),) + ENTER_KEYS:
            key, value = entry.elements[sel]
            if key == 'notes':
                new = prompt_multiline(win, 'Notes')
            else:
                new = prompt_line(win, key + ': ', value)
            if new is not None:
                if key in ESSENTIAL and new == '':
                    continue  # title/password must not be blanked
                entry.elements[sel][1] = new
                changed = True
        elif ch == ord('k'):
            key = entry.elements[sel][0]
            if key in ESSENTIAL:
                continue
            new = prompt_line(win, 'Rename element: ', key)
            if new and clean_key(new):
                entry.elements[sel][0] = clean_key(new)
                changed = True
        elif ch == ord('a'):
            name = prompt_line(win, 'New element name: ')
            if name and clean_key(name):
                key = clean_key(name)
                value = prompt_line(win, key + ': ') or ''
                idx = len(entry.elements)
                for j, (k, _) in enumerate(entry.elements):
                    if k == 'notes':
                        idx = j
                        break
                entry.elements.insert(idx, [key, value])
                changed = True
                sel = idx
        elif ch in (ord('r'), ord('x'), curses.KEY_DC):
            key = entry.elements[sel][0]
            if key in ESSENTIAL:
                continue  # title/password are essential, cannot be removed
            # Removing a column is destructive, so require a typed "yes" first --
            # a stray "r" must not silently drop data.
            if not confirm_yes(win, 'REMOVE COLUMN: ' + _label(key),
                               'This permanently removes this column from the '
                               'entry.', verb='remove'):
                continue
            del entry.elements[sel]
            changed = True
            sel = min(sel, len(entry.elements) - 1)
