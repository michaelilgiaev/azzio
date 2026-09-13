"""The new-entry wizard and the shared column-reorder screen.

Split out of forms.py to keep both files well under the module size limit: this is
the self-contained "create an entry" flow -- type a title, then pick columns from
a numbered menu, each on its own value page -- plus reorder_columns(), the screen
that arranges the columns between the pinned title and pinned notes.

It builds on forms.py's primitives (prompt_line / prompt_multiline / nav_bar /
addstr, the _label vocabulary and the _COLUMN_CHOICES menu, the nav-hint sets) and
the model helpers; forms.py does NOT import back from here (only
terminal_user_interface.py calls new_entry), so there is no import cycle.
"""

import curses

import forms
from forms import (addstr, nav_bar, _label, _COLUMN_CHOICES, ENTER_KEYS, ESC,
                    _NAV_PICK, _NAV_VALUE, _NAV_NEW_TITLE, _NAV_REORDER)
from model import Entry, clean_key, move_element, notes_last, strip_scheme

# The two interactive prompts are reached through the `forms` module (as
# forms.prompt_line / forms.prompt_multiline, not bound as bare names) so tests
# that stub them drive this whole flow headless.


def _header_for_title(title):
    """The NEW ENTRY header text for a (possibly URL-ish) title: the scheme/www
    prefix is stripped so "NEW ENTRY https://www.x.com" reads "NEW ENTRY x.com"."""
    shown = strip_scheme(title).strip()
    return 'NEW ENTRY ' + shown if shown else 'NEW ENTRY'


def _draw_new_header(win, title):
    """Redraw the NEW ENTRY header row for the current title (called live as the
    title is typed, and again on each column-picker redraw)."""
    h, w = win.getmaxyx()
    win.move(0, 0)
    win.clrtoeol()
    addstr(win, 0, 0, _header_for_title(title)[:w - 1], curses.A_BOLD)


def _page_reset(win):
    """Erase AND force the next refresh to repaint every cell from scratch.

    The new-entry pages redraw a lot with per-row clrtoeol() (climbing note input,
    live header), which can leave ncurses' cell-diff optimizer convinced a stale
    glyph is still correct -- so a bare erase() sometimes leaves a leftover '>'
    from the previous page. clearok(True) makes the next refresh unconditional, so
    no stale cell can survive a page transition. Called at the top of each
    new-entry page draw."""
    win.erase()
    try:
        win.clearok(True)
    except curses.error:
        pass


def _draw_added(win, elements, start_row):
    """Draw the columns added so far, one per line, starting at start_row. These
    populate ABOVE the "Add a column" menu so the entry grows on screen as the
    user fills it in. Returns the next free row. Title is skipped (it is already
    in the header); notes is shown LAST (it is pinned to the bottom) as "(N
    lines)". Other values are shown inline."""
    row = start_row
    h, _ = win.getmaxyx()
    for key, value in notes_last(elements):
        if key == 'title':
            continue
        if row >= h - 2:
            break
        if key == 'notes' or '\n' in value:
            n = len([l for l in value.split('\n') if l]) if value else 0
            shown = '(%d line%s)' % (n, '' if n == 1 else 's')
        else:
            shown = value
        addstr(win, row, 0, _label(key) + ') ', curses.A_BOLD)
        addstr(win, row, len(_label(key)) + 2, shown)
        row += 1
    return row


def _add_columns(win, elements, title):
    """Column picker page. Shows the NEW ENTRY header, the columns added so far
    (populating above the menu), then "Add a column:" with a numbered menu whose
    input sits one line under it. Loops until the user is done. Mutates `elements`.

    Type a number then ENTER to open that column's value page; a column already
    present is tagged "(added)" and re-picking it edits its value. Type "r" then
    ENTER to reorder the columns added so far (the order is what the entry shows).
    ENTER on an EMPTY input just notes that nothing was typed and stays. ESC
    finishes and saves the entry."""
    custom_n = len(_COLUMN_CHOICES) + 1
    note = ''  # transient message under the input (e.g. "no input provided")
    while True:
        _page_reset(win)
        _draw_new_header(win, title)
        present = {k for k, _ in elements}
        # Added columns populate directly under the header (row 1 down); then one
        # blank line, then the menu.
        row = _draw_added(win, elements, 1)
        row += 1  # single blank line between the added list and the menu
        addstr(win, row, 0, 'Add a column:', curses.A_BOLD)
        row += 1
        for i, (label, key, _ml) in enumerate(_COLUMN_CHOICES, 1):
            tag = '  (added)' if key in present else ''
            addstr(win, row, 2, '%d) %s%s' % (i, label, tag))
            row += 1
        addstr(win, row, 2, '%d) <CUSTOM COLUMN>' % custom_n)
        row += 1
        addstr(win, row, 2, 'r) reorder columns')
        row += 2  # single blank line between the menu and the input

        # A transient hint (e.g. "no input provided") sits just below the input.
        if note:
            addstr(win, row + 1, 0, note, curses.A_DIM)
        choice = forms.prompt_line(win, '> ', y=row, nav=_NAV_PICK)
        note = ''
        if choice is None:
            break                    # ESC = done (save and leave)
        choice = choice.strip()
        if choice == '':
            note = '(no input provided -- type a number, or ESC to finish)'
            continue
        if choice.lower() == 'r':
            reorder_columns(win, elements, _header_for_title(title))
            continue
        if not choice.isdigit():
            note = '(type a number, "r" to reorder, or ESC to finish)'
            continue
        n = int(choice)
        if 1 <= n <= len(_COLUMN_CHOICES):
            label, key, multiline = _COLUMN_CHOICES[n - 1]
            _set_column(win, elements, title, key, label, multiline)
        elif n == custom_n:
            name = _prompt_custom_name(win, title, elements)
            if name:
                _set_column(win, elements, title, name, name, False)
        else:
            note = '(no such option -- pick 1-%d, or "r" to reorder)' % custom_n


def _column_page_header_row(win, title, elements):
    """Draw a value page's top (NEW ENTRY header + added list) and return the row
    for the column's own heading -- one blank line under the added list."""
    _page_reset(win)
    _draw_new_header(win, title)
    return _draw_added(win, elements, 1) + 1


def _prompt_custom_name(win, title, elements):
    """Value-page for a custom column's NAME (its own "NEW COLUMN" heading + input
    right under it). Returns the cleaned name, or '' if cancelled/empty. The name
    is stored verbatim -- we never change its capitalization."""
    row = _column_page_header_row(win, title, elements)
    addstr(win, row, 0, 'NEW COLUMN', curses.A_BOLD)
    name = forms.prompt_line(win, '', y=row + 1, nav=_NAV_VALUE)
    if name is None:
        return ''
    return clean_key(name)


def _set_column(win, elements, title, key, label, multiline):
    """Open a column's own value page (mirrors the NEW ENTRY title screen: a
    heading naming the column, the input right under it, contextual hints at the
    bottom) and store the value under `key`, replacing any existing value so
    re-picking a column edits it. Notes use the multi-line prompt.

    The heading shows the label as-is (built-ins title-cased, a custom name kept
    exactly as the user typed it -- never force-uppercased)."""
    heading = _label(label)
    if multiline:
        row = _column_page_header_row(win, title, elements)
        addstr(win, row, 0, heading, curses.A_BOLD)
        # +2 so prompt_multiline's own "add a note line" hint (drawn one row above
        # its region) lands below this heading rather than on top of it.
        value = forms.prompt_multiline(win, heading, start_row=row + 2)
    else:
        existing = next((v for k, v in elements if k == key), '')
        row = _column_page_header_row(win, title, elements)
        addstr(win, row, 0, heading, curses.A_BOLD)
        value = forms.prompt_line(win, '', existing, y=row + 1, nav=_NAV_VALUE)
        if value is None:
            return
    for pair in elements:
        if pair[0] == key:
            pair[1] = value
            return
    elements.append([key, value])


def _reorderable_indices(elements):
    """Indices of the columns the user may reorder: every element except the
    pinned title (first) and pinned notes (last)."""
    return [i for i, (k, _v) in enumerate(elements)
            if k not in ('title', 'notes')]


def reorder_columns(win, elements, header):
    """Shared reorder screen (new-entry column picker AND the editor).

    Lists the entry's columns with `header` on top; the title is shown pinned (dim)
    at the top and notes pinned (dim) at the bottom -- neither can move. Arrows
    move the highlight among the columns between them; "[" moves the highlighted
    column up, "]" moves it down. ESC / ENTER / q finish. Mutates `elements` in
    place (normalized notes-last first) and returns True if the order changed.

    The stored element order is exactly what the list line "(N: names)" and the
    entry view render, so arranging columns here sets the order the user sees --
    including the order columns were picked while creating the entry -- with notes
    always kept at the bottom."""
    elements[:] = notes_last(elements)
    movable = _reorderable_indices(elements)
    h, _ = win.getmaxyx()
    if len(movable) < 2:
        win.erase()
        addstr(win, 0, 0, header, curses.A_BOLD)
        addstr(win, 2, 0, 'Nothing to reorder (need at least two columns '
               'besides the title/notes).', curses.A_DIM)
        nav_bar(win, h - 1, [('ESC', 'back'), ('any', 'back')])
        win.getch()
        return False
    changed = False
    # `pos` indexes into the movable list; the highlighted real element is
    # movable[pos]. After a move we recompute movable and keep the highlight on the
    # element that moved.
    pos = 0
    while True:
        movable = _reorderable_indices(elements)
        pos = max(0, min(pos, len(movable) - 1))
        sel_real = movable[pos]
        win.erase()
        addstr(win, 0, 0, header, curses.A_BOLD)
        addstr(win, 1, 0, 'reorder columns -- [ up   ] down '
               '(notes stays at the bottom)', curses.A_DIM)
        row = 3
        for i, (key, value) in enumerate(elements):
            if row >= h - 1:
                break
            if key == 'title':
                addstr(win, row, 0, '  Title) ' + value, curses.A_DIM)
                row += 1
                continue
            if key == 'notes':
                shown = value.replace('\n', ' / ')
                addstr(win, row, 0, '  Notes) ' + shown, curses.A_DIM)
                row += 1
                continue
            marker = '> ' if i == sel_real else '  '
            shown = value.replace('\n', ' / ')
            attr = curses.A_REVERSE if i == sel_real else 0
            addstr(win, row, 0, '%s%s) %s' % (marker, _label(key), shown), attr)
            row += 1
        nav_bar(win, h - 1, _NAV_REORDER)
        ch = win.getch()
        if ch in (ESC, ord('q'), ord('Q')) + ENTER_KEYS:
            return changed
        if ch == curses.KEY_UP:
            pos = (pos - 1) % len(movable)
        elif ch == curses.KEY_DOWN:
            pos = (pos + 1) % len(movable)
        elif ch == ord('['):
            new_real = move_element(elements, sel_real, -1)
            if new_real != sel_real:
                changed = True
                pos = _reorderable_indices(elements).index(new_real)
        elif ch == ord(']'):
            new_real = move_element(elements, sel_real, +1)
            if new_real != sel_real:
                changed = True
                pos = _reorderable_indices(elements).index(new_real)


def new_entry(win):
    """Wizard: title (only requirement) -> numbered column picker.

    Only a title is needed to save. The title input sits directly under the NEW
    ENTRY header, which updates live to show the title with any URL scheme / www.
    prefix stripped.

    Empty-title handling (mirrors delete confirmation): pressing ENTER on an empty
    title does NOT silently leave -- it first echoes that nothing was written and
    that pressing ENTER again exits the new-entry section. So the first empty ENTER
    warns and stays; a second consecutive empty ENTER (or ESC any time) backs out,
    returning None. Typing a title clears the warning and continues."""
    _page_reset(win)
    _draw_new_header(win, '')
    warned = False
    while True:
        note = ('nothing written -- press ENTER again to exit the new entry, '
                'or type a title') if warned else None
        title = forms.prompt_line(win, '', y=1, nav=_NAV_NEW_TITLE, note=note,
                            on_change=lambda t: _draw_new_header(win, t))
        if title is None:
            return None                      # ESC -> back, nothing saved
        title = strip_scheme(title).strip()
        if title == '':
            if warned:
                return None                  # second empty ENTER -> exit
            warned = True                    # first empty ENTER -> warn and stay
            continue
        break
    elements = [['title', title]]
    _add_columns(win, elements, title)
    return Entry(elements)
