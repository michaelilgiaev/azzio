#!/usr/bin/env python3
"""Unit tests for the `passwords` app's logic.

Most curses UI flows (prompt_line, new_entry, entry_view) need a live terminal;
instead we test the pure pieces they lean on -- URL scheme stripping, the
title-only save rule, element (de)serialization round-trips, alphabetical
sorting, element reordering, notes-pinned-last, and the small display/label
helpers in forms.

Where a flow's control logic is worth pinning down we drive it headless: a no-op
window (_FakeWin) plus a stubbed prompt_line cover confirm_delete, new_entry's
empty-title handling, and the entry-view hub (copy a column / clip in order /
edit), and a key-queue window (_KeyWin) feeds scripted keystrokes to the reorder
screen. The clipboard is covered without touching X: clipboard.copy/copy_sequence
have their spawn captured, and clipboard_owner._Server's paste-once sequencing is
pure logic. The real X selection owner is exercised separately (see the manual
end-to-end checks in the module docs), not here. Run: python3 test_passwords.py
"""

import curses
import os
import sys
import unittest
from pathlib import Path

# The app modules import each other by BARE top-level name (`import model`,
# `from forms import addstr`, ...) because at runtime the launcher runs them with
# the passwords package directory on sys.path. This test lives in tests/ (where the
# tests belong), so it puts THAT directory -- libraries/packages/passwords/ -- on
# sys.path first, exactly as the launcher does, and the bare imports resolve.
_PASSWORDS_DIR = Path(__file__).resolve().parents[1] / "libraries" / "packages" / "passwords"
sys.path.insert(0, str(_PASSWORDS_DIR))

import clipboard
import clipboard_owner
import forms
import model
import new_entry
import terminal_user_interface
from model import (Entry, Store, clean_key, move_element, notes_last,
                   strip_scheme)


class StripSchemeTests(unittest.TestCase):
    def test_https_www(self):
        self.assertEqual(strip_scheme('https://www.example.com').strip(),
                         'example.com')

    def test_http_www(self):
        self.assertEqual(strip_scheme('http://www.example.com').strip(),
                         'example.com')

    def test_scheme_without_www(self):
        self.assertEqual(strip_scheme('https://example.com').strip(),
                         'example.com')

    def test_www_without_scheme(self):
        self.assertEqual(strip_scheme('www.github.com').strip(), 'github.com')

    def test_path_and_query_kept(self):
        self.assertEqual(strip_scheme('http://foo.io/path?q=1').strip(),
                         'foo.io/path?q=1')

    def test_other_scheme(self):
        self.assertEqual(strip_scheme('ftp://files.x.org').strip(),
                         'files.x.org')

    def test_leading_whitespace_dropped(self):
        self.assertEqual(strip_scheme('  https://www.spaced.com').strip(),
                         'spaced.com')

    def test_case_insensitive(self):
        self.assertEqual(strip_scheme('HTTPS://WWW.CAPS.COM').strip(),
                         'CAPS.COM')

    def test_plain_title_untouched(self):
        self.assertEqual(strip_scheme('My Bank').strip(), 'My Bank')
        self.assertEqual(strip_scheme('example.com').strip(), 'example.com')

    def test_www_not_a_prefix(self):
        # "wwwsomething" does not start with "www." -> left alone.
        self.assertEqual(strip_scheme('wwwsomething').strip(), 'wwwsomething')

    def test_scheme_only(self):
        self.assertEqual(strip_scheme('http://').strip(), '')


class EssentialRuleTests(unittest.TestCase):
    def test_only_title_is_essential(self):
        # Password is no longer required to save -- only the title is essential.
        self.assertEqual(model.ESSENTIAL, ('title',))
        self.assertIn('title', model.ESSENTIAL)
        self.assertNotIn('password', model.ESSENTIAL)


class RoundTripTests(unittest.TestCase):
    def test_title_only_round_trip(self):
        e = Entry([['title', 'example.com']])
        text = Store([e]).serialize()
        back = Store.parse(text)
        self.assertEqual(back.entries[0].title, 'example.com')
        self.assertEqual(back.entries[0].password, '')  # absent -> ''

    def test_columns_round_trip(self):
        e = Entry([['title', 'something.com'],
                   ['username', 'some_user'],
                   ['password', 'lol']])
        text = Store([e]).serialize()
        back = Store.parse(text).entries[0]
        self.assertEqual([k for k, _ in back.elements],
                         ['title', 'username', 'password'])
        self.assertEqual(back.get('username'), 'some_user')
        self.assertEqual(back.get('password'), 'lol')

    def test_notes_round_trip(self):
        e = Entry([['title', 'x'], ['notes', 'line one\nline two']])
        text = Store([e]).serialize()
        back = Store.parse(text).entries[0]
        self.assertEqual(back.get('notes'), 'line one\nline two')


class CleanKeyTests(unittest.TestCase):
    def test_strips_paren_and_newlines(self):
        self.assertEqual(clean_key(' Foo)\n'), 'Foo')

    def test_empty(self):
        self.assertEqual(clean_key('   '), '')


class FormsHelperTests(unittest.TestCase):
    def test_label_capitalizes_builtins(self):
        # The four built-in keys are stored lower-case and shown title-cased.
        self.assertEqual(forms._label('username'), 'Username')
        self.assertEqual(forms._label('email'), 'Email')
        self.assertEqual(forms._label('password'), 'Password')
        self.assertEqual(forms._label('notes'), 'Notes')

    def test_label_custom_key_verbatim(self):
        # A CUSTOM column keeps the user's exact capitalization -- never re-cased.
        self.assertEqual(forms._label('recovery code'), 'recovery code')
        self.assertEqual(forms._label('MyBank'), 'MyBank')
        self.assertEqual(forms._label('API key'), 'API key')

    def test_header_strips_scheme(self):
        self.assertEqual(new_entry._header_for_title('https://www.x.com'),
                         'NEW ENTRY x.com')

    def test_header_empty_title(self):
        self.assertEqual(new_entry._header_for_title(''), 'NEW ENTRY')
        self.assertEqual(new_entry._header_for_title('http://'), 'NEW ENTRY')

    def test_column_choices_present(self):
        keys = [k for _, k, _ in forms._COLUMN_CHOICES]
        self.assertEqual(keys, ['email', 'username', 'password', 'notes'])
        # Notes is the only multi-line column.
        multiline = [k for _, k, ml in forms._COLUMN_CHOICES if ml]
        self.assertEqual(multiline, ['notes'])


class _FakeWin:
    """Minimal no-op curses window so form helpers that draw can run headless."""
    def getmaxyx(self):
        return (24, 80)

    def erase(self):
        pass

    def move(self, *a):
        pass

    def clrtoeol(self):
        pass

    def clrtobot(self):
        pass

    def clearok(self, *a):
        pass

    def addstr(self, *a, **k):
        pass


class SetColumnTests(unittest.TestCase):
    """_set_column drives one column's value page; with the value prompt stubbed
    and a no-op window it is testable headless. It must store under the key and
    replace (not duplicate) an existing value when the column is re-picked."""

    def test_adds_then_edits_in_place(self):
        win = _FakeWin()
        elements = [['title', 'x']]
        seq = iter(['some_user', 'edited_user'])
        orig = forms.prompt_line
        forms.prompt_line = lambda *a, **k: next(seq)
        try:
            new_entry._set_column(win, elements, 'x', 'username', 'Username', False)
            self.assertEqual(elements[-1], ['username', 'some_user'])
            # Re-picking the same column edits in place -- no duplicate key.
            new_entry._set_column(win, elements, 'x', 'username', 'Username', False)
            self.assertEqual(elements[-1], ['username', 'edited_user'])
            self.assertEqual([k for k, _ in elements], ['title', 'username'])
        finally:
            forms.prompt_line = orig

    def test_cancel_value_leaves_elements_unchanged(self):
        win = _FakeWin()
        elements = [['title', 'x']]
        orig = forms.prompt_line
        forms.prompt_line = lambda *a, **k: None   # ESC on the value page
        try:
            new_entry._set_column(win, elements, 'x', 'email', 'Email', False)
            self.assertEqual(elements, [['title', 'x']])
        finally:
            forms.prompt_line = orig


class QuitVsBackTests(unittest.TestCase):
    """The whole-app rule: q is a FULL quit (handlers return False -> run() ends);
    ESC is only "back" (never quits). Driven headless through App.handle_* with a
    no-op stdscr, so we assert the control-flow contract without a terminal.

    handle_search / handle_select return False to end the app, True to keep going.
    """

    def _app(self):
        store = Store([Entry([['title', 'example.com'],
                              ['username', 'u'], ['password', 'p']])])
        app = terminal_user_interface.App(_FakeWin(), store)
        app.results = list(store.entries)
        app.sel = 0
        return app

    def test_q_quits_from_search(self):
        app = self._app()
        self.assertFalse(app.handle_search(ord('q')))
        self.assertFalse(app.handle_search(ord('Q')))

    def test_q_quits_from_select(self):
        app = self._app()
        app.mode = terminal_user_interface.SELECT
        self.assertFalse(app.handle_select(ord('q')))
        self.assertFalse(app.handle_select(ord('Q')))

    def test_esc_does_not_quit(self):
        app = self._app()
        # ESC returns True (stays in the app) and resets to the search box.
        self.assertTrue(app.handle_search(forms.ESC))
        self.assertEqual(app.mode, terminal_user_interface.SEARCH)
        app.mode = terminal_user_interface.SELECT
        self.assertTrue(app.handle_select(forms.ESC))

    def test_q_in_entry_view_quits_app(self):
        # entry_view (opened with ENTER) returns (changed, quit); q inside it must
        # propagate as a FULL quit (handle_select returns False).
        app = self._app()
        app.mode = terminal_user_interface.SELECT
        orig = forms.entry_view
        forms.entry_view = lambda *a, **k: (False, True)   # (changed, quit)
        try:
            for enter in forms.ENTER_KEYS:
                self.assertFalse(app.handle_select(enter))
        finally:
            forms.entry_view = orig

    def test_back_from_entry_view_keeps_app(self):
        app = self._app()
        app.mode = terminal_user_interface.SELECT
        orig = forms.entry_view
        forms.entry_view = lambda *a, **k: (False, False)   # ESC/back inside view
        try:
            self.assertTrue(app.handle_select(curses.KEY_ENTER))
        finally:
            forms.entry_view = orig

    def test_entry_view_change_marks_dirty_without_quitting(self):
        # An edit inside the entry view reports changed=True; the app must mark the
        # store dirty and stay open.
        app = self._app()
        app.mode = terminal_user_interface.SELECT
        orig = forms.entry_view
        forms.entry_view = lambda *a, **k: (True, False)   # changed, no quit
        try:
            self.assertTrue(app.handle_select(10))
            self.assertTrue(app.dirty)
        finally:
            forms.entry_view = orig

    def test_stay_open_starts_off_and_is_threaded_through(self):
        # The app owns a STAY OPEN holder, off by default, and hands it to
        # entry_view so the toggle survives across re-opens within one run.
        app = self._app()
        app.mode = terminal_user_interface.SELECT
        self.assertEqual(app.stay_open, [False])
        seen = {}
        orig = forms.entry_view

        def fake(win, entry, stay_open=None, *a, **k):
            seen['holder'] = stay_open
            return (False, False)

        forms.entry_view = fake
        try:
            app.handle_select(10)
        finally:
            forms.entry_view = orig
        self.assertIs(seen['holder'], app.stay_open)   # same object, not a copy


class SortTests(unittest.TestCase):
    """The first page (Store.filter) is alphabetical by title, case-insensitive,
    for both the empty query (everything) and a filtered query."""

    def _store(self):
        return Store([Entry([['title', t]]) for t in
                      ['banana', 'Apple', 'cherry', 'apricot', 'Blueberry']])

    def test_empty_query_sorted(self):
        titles = [e.title for e in self._store().filter('')]
        self.assertEqual(titles,
                         ['Apple', 'apricot', 'banana', 'Blueberry', 'cherry'])

    def test_filtered_query_sorted(self):
        # "a" matches Apple, banana, apricot (case-insensitive substring), sorted.
        titles = [e.title for e in self._store().filter('a')]
        self.assertEqual(titles, ['Apple', 'apricot', 'banana'])

    def test_sort_is_case_insensitive(self):
        store = Store([Entry([['title', 'zebra']]), Entry([['title', 'Ant']])])
        self.assertEqual([e.title for e in store.filter('')], ['Ant', 'zebra'])


class EnterInSearchTests(unittest.TestCase):
    """ENTER in SEARCH mode jumps the cursor into the results list (SELECT, first
    match). It must NOT copy and must NOT quit; with no matches it is a no-op."""

    def _app(self, titles=('example.com', 'other.com')):
        store = Store([Entry([['title', t], ['password', 'p']]) for t in titles])
        app = terminal_user_interface.App(_FakeWin(), store)
        app.refilter()
        return app

    def test_enter_jumps_to_list_without_quitting(self):
        app = self._app()
        for enter in forms.ENTER_KEYS:
            app.mode = terminal_user_interface.SEARCH
            app.sel = 5
            # True == stay in the app (does not quit); mode flips to SELECT and
            # the highlight lands on the first match. ENTER here never opens the
            # entry view or copies -- it only moves the cursor into the list.
            self.assertTrue(app.handle_search(enter))
            self.assertEqual(app.mode, terminal_user_interface.SELECT)
            self.assertEqual(app.sel, 0)          # highlight on the first match

    def test_enter_with_no_matches_is_noop(self):
        app = self._app()
        app.query = 'zzz-nothing-matches'
        app.refilter()
        self.assertEqual(app.results, [])
        app.mode = terminal_user_interface.SEARCH
        self.assertTrue(app.handle_search(10))    # stays in the app
        self.assertEqual(app.mode, terminal_user_interface.SEARCH)    # and stays in SEARCH (no jump)


class MoveElementTests(unittest.TestCase):
    """model.move_element: the pure reorder primitive behind both reorder UIs.
    Title is pinned first; edges and out-of-range are no-ops."""

    def _els(self):
        return [['title', 't'], ['email', 'e'], ['username', 'u'],
                ['password', 'p']]

    def test_move_down(self):
        els = self._els()
        new = move_element(els, 1, +1)     # email down past username
        self.assertEqual(new, 2)
        self.assertEqual([k for k, _ in els],
                         ['title', 'username', 'email', 'password'])

    def test_move_up(self):
        els = self._els()
        new = move_element(els, 3, -1)     # password up past username
        self.assertEqual(new, 2)
        self.assertEqual([k for k, _ in els],
                         ['title', 'email', 'password', 'username'])

    def test_title_never_moves(self):
        els = self._els()
        self.assertEqual(move_element(els, 0, +1), 0)
        self.assertEqual([k for k, _ in els],
                         ['title', 'email', 'username', 'password'])

    def test_cannot_take_title_slot(self):
        els = self._els()
        # The first non-title element cannot move up into the title's slot.
        self.assertEqual(move_element(els, 1, -1), 1)
        self.assertEqual(els[0][0], 'title')

    def test_bottom_edge_is_noop(self):
        els = self._els()
        self.assertEqual(move_element(els, 3, +1), 3)
        self.assertEqual([k for k, _ in els],
                         ['title', 'email', 'username', 'password'])

    def test_out_of_range_is_noop(self):
        els = self._els()
        self.assertEqual(move_element(els, 9, +1), 9)
        self.assertEqual(len(els), 4)

    def test_no_title_first_element_movable_to_top(self):
        # With no title present, nothing is pinned, so index 0 is a normal slot.
        els = [['email', 'e'], ['username', 'u']]
        self.assertEqual(move_element(els, 1, -1), 0)
        self.assertEqual([k for k, _ in els], ['username', 'email'])

    def _els_with_notes(self):
        return [['title', 't'], ['email', 'e'], ['username', 'u'],
                ['notes', 'n']]

    def test_notes_never_moves(self):
        # Notes is pinned last: trying to move it up is a no-op.
        els = self._els_with_notes()
        self.assertEqual(move_element(els, 3, -1), 3)
        self.assertEqual([k for k, _ in els],
                         ['title', 'email', 'username', 'notes'])

    def test_cannot_move_past_notes(self):
        # The last real column cannot move DOWN into (past) the notes slot.
        els = self._els_with_notes()
        self.assertEqual(move_element(els, 2, +1), 2)   # username stays put
        self.assertEqual([k for k, _ in els],
                         ['title', 'email', 'username', 'notes'])

    def test_column_still_moves_between_title_and_notes(self):
        # email can move down past username -- both are between title and notes.
        els = self._els_with_notes()
        self.assertEqual(move_element(els, 1, +1), 2)
        self.assertEqual([k for k, _ in els],
                         ['title', 'username', 'email', 'notes'])


class NotesLastTests(unittest.TestCase):
    """model.notes_last pins any notes element to the bottom (stable otherwise),
    and Entry.serialize applies it so the on-disk file always ends with notes."""

    def test_notes_moved_to_end(self):
        els = [['title', 't'], ['notes', 'n'], ['email', 'e'], ['password', 'p']]
        self.assertEqual([k for k, _ in model.notes_last(els)],
                         ['title', 'email', 'password', 'notes'])

    def test_stable_for_non_notes(self):
        els = [['title', 't'], ['email', 'e'], ['username', 'u']]
        self.assertEqual(model.notes_last(els), els)

    def test_no_notes_unchanged(self):
        els = [['title', 't'], ['password', 'p']]
        self.assertEqual([k for k, _ in model.notes_last(els)],
                         ['title', 'password'])

    def test_serialize_puts_notes_last(self):
        # Even if notes is stored in the middle, the serialized text ends with the
        # notes block (title/password stay in their given order before it).
        e = Entry([['title', 'x'], ['notes', 'a note'], ['password', 'p']])
        text = e.serialize()
        lines = [l for l in text.split('\n') if l and l != model.DELIM]
        self.assertEqual(lines[0], 'title) x')
        self.assertEqual(lines[1], 'password) p')
        self.assertEqual(lines[2], 'notes)')
        self.assertEqual(lines[3], '    a note')

    def test_serialize_round_trips_with_notes_last(self):
        e = Entry([['title', 'x'], ['notes', 'n1\nn2'], ['email', 'a@b.c']])
        back = Store.parse(Store([e]).serialize()).entries[0]
        self.assertEqual([k for k, _ in back.elements],
                         ['title', 'email', 'notes'])
        self.assertEqual(back.get('notes'), 'n1\nn2')

    def test_copy_sequence_excludes_title_and_notes(self):
        e = Entry([['title', 'x'], ['email', 'a@b.c'], ['password', 'p'],
                   ['notes', 'secret note']])
        self.assertEqual([k for k, _ in e.copy_sequence()],
                         ['email', 'password'])


class FilePreservationTests(unittest.TestCase):
    """PROMPT.md: "the passwords.txt itself should remain the same, no new lines no
    nothing". An UNMODIFIED entry must serialize back byte-for-byte (blank lines and
    element order intact, even notes deliberately in the middle); only an entry the
    user actually EDITS gets canonicalized (notes forced last)."""

    PROMPT_FILE = ('####\ntitle) x\n\nusername) u\npassword) p\n\n'
                   'notes)\n    hi\n####\n')

    def test_prompt_format_round_trips_byte_identical(self):
        # The exact shape PROMPT.md shows as the desired layout (blank lines around
        # fields) must survive parse->serialize unchanged.
        self.assertEqual(Store.parse(self.PROMPT_FILE).serialize(),
                         self.PROMPT_FILE)

    def test_notes_in_middle_preserved_when_untouched(self):
        text = '####\ntitle) keep.com\nnotes)\n    mid\npassword) pw\n####\n'
        self.assertEqual(Store.parse(text).serialize(), text)

    def test_adding_entry_does_not_reformat_untouched(self):
        text = '####\ntitle) keep.com\nnotes)\n    mid\npassword) pw\n####\n'
        store = Store.parse(text)
        store.entries.append(Entry([['title', 'new.com'], ['password', 'np']]))
        out = store.serialize()
        # The untouched entry keeps its exact body (notes still in the middle).
        self.assertIn('title) keep.com\nnotes)\n    mid\npassword) pw', out)
        # The new entry is canonical (notes-last rules don't apply, it has none).
        self.assertIn('title) new.com\npassword) np', out)

    def test_editing_entry_canonicalizes_notes_last(self):
        text = '####\ntitle) keep.com\nnotes)\n    mid\npassword) pw\n####\n'
        store = Store.parse(text)
        store.entries[0].set('password', 'CHANGED')   # an edit -> mark_dirty
        out = store.serialize()
        self.assertLess(out.index('password)'), out.index('notes)'))  # notes last
        self.assertIn('password) CHANGED', out)

    def test_mark_dirty_forces_canonical(self):
        e = Entry([['title', 'x'], ['notes', 'n'], ['password', 'p']],
                  original_body='title) x\nnotes)\n    n\npassword) p')
        # Untouched -> original body verbatim (notes in middle).
        self.assertIn('notes)\n    n\npassword) p', e.serialize())
        e.mark_dirty()
        # After mark_dirty -> canonical (notes last).
        out = e.serialize()
        self.assertLess(out.index('password)'), out.index('notes)'))

    def test_editor_change_marks_entry_dirty(self):
        # Driving edit_entry headless: a value change must mark the entry dirty so
        # its on-disk form is rebuilt (not the stale original).
        entry = Entry([['title', 'x'], ['notes', 'n'], ['password', 'old']],
                      original_body='title) x\nnotes)\n    n\npassword) old')
        # edit_entry normalizes notes-last on entry, so the list is
        # [title(0), password(1), notes(2)]. Script: highlight starts on title(0);
        # one KEY_DOWN lands on password(1); 'e' edits it (a single-line prompt, so
        # the stub value is stored directly, NOT the multiline notes path); ESC out.
        keys = iter([curses.KEY_DOWN, ord('e'), forms.ESC])
        win = _KeyWin([])
        win._keys = list(keys)
        orig = forms.prompt_line
        forms.prompt_line = lambda *a, **k: 'newpw'
        try:
            changed, quit_ = forms.edit_entry(win, entry)
        finally:
            forms.prompt_line = orig
        self.assertTrue(changed)
        self.assertIsNone(entry._original_body)          # dirtied
        self.assertEqual(entry.get('password'), 'newpw')

    def _remove_run(self, confirm):
        # Highlight moves to the email column (idx 1) and presses "r"; confirm_yes
        # is stubbed to `confirm`. Returns (changed, remaining keys after email).
        entry = Entry([['title', 'x'], ['email', 'a@b.c'], ['password', 'p']])
        win = _KeyWin([curses.KEY_DOWN, ord('r'), forms.ESC])
        orig = forms.confirm_yes
        forms.confirm_yes = lambda *a, **k: confirm
        try:
            changed, _quit = forms.edit_entry(win, entry)
        finally:
            forms.confirm_yes = orig
        return changed, [k for k, _ in entry.elements]

    def test_remove_requires_yes_confirmation(self):
        # "r" on a non-essential column must ask confirm_yes; only "yes" deletes.
        changed, keys = self._remove_run(confirm=True)
        self.assertTrue(changed)
        self.assertEqual(keys, ['title', 'password'])    # email removed

    def test_remove_cancelled_keeps_column(self):
        # Declining the confirmation leaves the column intact and reports no change.
        changed, keys = self._remove_run(confirm=False)
        self.assertFalse(changed)
        self.assertEqual(keys, ['title', 'email', 'password'])


class NavLabelTests(unittest.TestCase):
    """Every ENTER hint now reads "ENTER continue" -- including the SELECT bar,
    where ENTER used to say "copy" but now continues into the entry view. There is
    no longer any "ENTER copy" anywhere. This pins that contract across all nav
    sets."""

    def _enter_labels(self, pairs):
        return [label for key, label in pairs if key == 'ENTER']

    def test_forms_enter_labels_are_continue(self):
        for name in ('_NAV_NEW_TITLE', '_NAV_PICK', '_NAV_VALUE', '_NAV_DELETE'):
            pairs = getattr(forms, name)
            for label in self._enter_labels(pairs):
                self.assertEqual(label, 'continue',
                                 '%s ENTER label should be "continue"' % name)

    def test_select_nav_enter_is_continue(self):
        # ENTER in the search/select bar continues into the entry view now.
        self.assertIn(('ENTER', 'continue'), terminal_user_interface._NAV_SELECT)
        self.assertNotIn(('ENTER', 'copy'), terminal_user_interface._NAV_SELECT)

    def test_select_nav_dropped_show_edit_multi(self):
        # The reworked SELECT bar no longer advertises v/e/m -- those verbs moved
        # into the entry view.
        keys = [k for k, _ in terminal_user_interface._NAV_SELECT]
        for gone in ('v', 'e', 'm'):
            self.assertNotIn(gone, keys)

    def test_no_stray_enter_labels(self):
        # Guard against a future nav set slipping in "ENTER next"/"ENTER add" etc.
        nav_sets = [v for k, v in vars(forms).items()
                    if k.startswith('_NAV_') and isinstance(v, list)]
        for pairs in nav_sets:
            for label in self._enter_labels(pairs):
                self.assertEqual(label, 'continue')


class ConfirmDeleteTests(unittest.TestCase):
    """confirm_delete mirrors the new-entry title screen: "yes"+ENTER deletes; a
    non-"yes" ENTER warns and stays, a second one cancels; ESC cancels. Driven
    headless by stubbing prompt_line to replay a script of answers."""

    def _run_with_answers(self, answers):
        seq = iter(answers)
        orig = forms.prompt_line
        forms.prompt_line = lambda *a, **k: next(seq)
        try:
            return forms.confirm_delete(_FakeWin(), Entry([['title', 'x']]))
        finally:
            forms.prompt_line = orig

    def test_yes_confirms(self):
        self.assertTrue(self._run_with_answers(['yes']))

    def test_yes_with_whitespace_confirms(self):
        self.assertTrue(self._run_with_answers(['  yes  ']))

    def test_esc_cancels(self):
        # prompt_line returns None on ESC.
        self.assertFalse(self._run_with_answers([None]))

    def test_empty_then_empty_cancels(self):
        # First empty ENTER warns and stays; the second exits (cancel).
        self.assertFalse(self._run_with_answers(['', '']))

    def test_non_yes_then_yes_confirms(self):
        # A wrong answer warns; typing "yes" next still deletes.
        self.assertTrue(self._run_with_answers(['no', 'yes']))

    def test_non_yes_then_empty_cancels(self):
        self.assertFalse(self._run_with_answers(['nope', '']))


class NewEntryEmptyTitleTests(unittest.TestCase):
    """new_entry's empty-title handling: the first empty ENTER warns and stays, a
    second empty ENTER (or ESC) exits with None; a real title proceeds to the
    column picker (stubbed here) and returns an Entry."""

    def _run_with_titles(self, titles, added=None):
        seq = iter(titles)
        orig_prompt = forms.prompt_line
        orig_add = new_entry._add_columns
        forms.prompt_line = lambda *a, **k: next(seq)
        new_entry._add_columns = (added if added is not None
                              else (lambda win, els, title: None))
        try:
            return new_entry.new_entry(_FakeWin())
        finally:
            forms.prompt_line = orig_prompt
            new_entry._add_columns = orig_add

    def test_empty_then_empty_returns_none(self):
        self.assertIsNone(self._run_with_titles(['', '']))

    def test_esc_returns_none(self):
        self.assertIsNone(self._run_with_titles([None]))

    def test_empty_then_title_proceeds(self):
        entry = self._run_with_titles(['', 'example.com'])
        self.assertIsNotNone(entry)
        self.assertEqual(entry.title, 'example.com')

    def test_title_strips_scheme(self):
        entry = self._run_with_titles(['https://www.example.com'])
        self.assertEqual(entry.title, 'example.com')

    def test_added_columns_are_kept(self):
        # Whatever _add_columns appends must survive into the returned Entry.
        def add(win, els, title):
            els.append(['email', 'a@b.c'])
        entry = self._run_with_titles(['site.com'], added=add)
        self.assertEqual([k for k, _ in entry.elements], ['title', 'email'])


class _KeyWin(_FakeWin):
    """A no-op window that also replays a scripted list of getch() return values,
    so keystroke-driven screens (reorder) run headless. Raises if the script runs
    dry -- a screen that never exits is a bug the test should surface, not hang."""
    def __init__(self, keys):
        self._keys = list(keys)

    def getch(self):
        if not self._keys:
            raise AssertionError('getch ran past the scripted keys (no exit?)')
        return self._keys.pop(0)


class ReorderColumnsTests(unittest.TestCase):
    """new_entry.reorder_columns driven by scripted keystrokes. The highlight starts
    on the first non-title column; "[" / "]" move it; ESC finishes."""

    def _els(self):
        return [['title', 't'], ['email', 'e'], ['username', 'u'],
                ['password', 'p']]

    def test_move_selected_down_then_exit(self):
        els = self._els()
        # ] moves the first movable (email) down one, then ESC.
        changed = new_entry.reorder_columns(_KeyWin([ord(']'), forms.ESC]),
                                        els, 'HDR')
        self.assertTrue(changed)
        self.assertEqual([k for k, _ in els],
                         ['title', 'username', 'email', 'password'])

    def test_arrow_then_move_up(self):
        els = self._els()
        # Down-arrow moves the highlight to username, "[" moves it up above email.
        changed = new_entry.reorder_columns(
            _KeyWin([curses.KEY_DOWN, ord('['), forms.ESC]), els, 'HDR')
        self.assertTrue(changed)
        self.assertEqual([k for k, _ in els],
                         ['title', 'username', 'email', 'password'])

    def test_esc_without_moving_reports_no_change(self):
        els = self._els()
        changed = new_entry.reorder_columns(_KeyWin([forms.ESC]), els, 'HDR')
        self.assertFalse(changed)
        self.assertEqual([k for k, _ in els],
                         ['title', 'email', 'username', 'password'])

    def test_title_stays_pinned_when_moving_top_column_up(self):
        els = self._els()
        # "[" on the top movable column (email) can't cross the title; no change.
        changed = new_entry.reorder_columns(_KeyWin([ord('['), forms.ESC]),
                                        els, 'HDR')
        self.assertFalse(changed)
        self.assertEqual(els[0][0], 'title')

    def test_fewer_than_two_movable_returns_false(self):
        # Only a title + one column -> nothing to reorder; any key returns.
        els = [['title', 't'], ['email', 'e']]
        changed = new_entry.reorder_columns(_KeyWin([ord('q')]), els, 'HDR')
        self.assertFalse(changed)

    def test_notes_normalized_last_and_pinned(self):
        # Notes given in the middle is normalized to the end on entry, and the
        # movable columns are only those between title and notes.
        els = [['title', 't'], ['notes', 'n'], ['email', 'e'], ['username', 'u']]
        # ] moves the first movable (email) down past username; notes stays last.
        changed = new_entry.reorder_columns(_KeyWin([ord(']'), forms.ESC]),
                                        els, 'HDR')
        self.assertTrue(changed)
        self.assertEqual([k for k, _ in els],
                         ['title', 'username', 'email', 'notes'])

    def test_only_notes_besides_columns_pinned(self):
        # title + one column + notes -> fewer than two MOVABLE columns -> no-op.
        els = [['title', 't'], ['email', 'e'], ['notes', 'n']]
        changed = new_entry.reorder_columns(_KeyWin([ord('q')]), els, 'HDR')
        self.assertFalse(changed)


class EntryViewTests(unittest.TestCase):
    """forms.entry_view is the hub opened with ENTER. It answers to a SINGLE
    keypress (no typed input, no ENTER): a NUMBER clips that column (paste-once),
    "c" clips every column in order, "e" edits, "s" toggles STAY OPEN, ESC backs
    out, q quits. Clipping (a number or "c") quits the whole app unless STAY OPEN
    is on. Driven headless with _KeyWin replaying getch() keystrokes; the clipboard
    calls are captured instead of touching X."""

    def _entry(self):
        return Entry([['title', 'site.com'], ['email', 'a@b.c'],
                      ['username', 'user'], ['password', 'pw'],
                      ['notes', 'note text']])

    def _run(self, keys, entry=None, stay_open=None):
        entry = entry or self._entry()
        copied = []
        seqd = []
        win = _KeyWin([(ord(k) if isinstance(k, str) else k) for k in keys])
        result = forms.entry_view(
            win, entry, stay_open=stay_open,
            do_copy=lambda v: copied.append(v),
            do_sequence=lambda vs: seqd.append(list(vs)))
        return result, copied, seqd, entry

    def test_esc_backs_out(self):
        # ESC -> (changed False, quit False), nothing clipped.
        (changed, quit_), copied, seqd, _ = self._run([forms.ESC])
        self.assertFalse(changed)
        self.assertFalse(quit_)
        self.assertEqual(copied, [])

    def test_number_clips_that_column_and_quits(self):
        # Columns numbered top-to-bottom (notes last): 1 email, 2 username, 3
        # password, 4 notes. Press 3 -> clips the password, then QUITS (STAY OFF).
        (changed, quit_), copied, seqd, _ = self._run(['3'])
        self.assertEqual(copied, ['pw'])
        self.assertEqual(seqd, [])
        self.assertTrue(quit_)
        self.assertFalse(changed)

    def test_number_can_clip_notes(self):
        # Notes is a clippable single column (the last number); clipping quits.
        (_c, quit_), copied, _s, _e = self._run(['4'])
        self.assertEqual(copied, ['note text'])
        self.assertTrue(quit_)

    def test_c_clips_in_order_excluding_notes_and_quits(self):
        # "c" clips every column in order: email, username, password -- NOT notes.
        (_c, quit_), copied, seqd, _ = self._run(['c'])
        self.assertEqual(seqd, [['a@b.c', 'user', 'pw']])
        self.assertEqual(copied, [])
        self.assertTrue(quit_)

    def test_bad_number_is_noop_and_stays(self):
        # A number past the last column clips nothing and does NOT quit; ESC out.
        (_c, quit_), copied, seqd, _ = self._run(['9', forms.ESC])
        self.assertEqual(copied, [])
        self.assertEqual(seqd, [])
        self.assertFalse(quit_)

    def test_unmapped_key_stays(self):
        # A key with no action (e.g. "z") just redraws with a hint; ESC then exits.
        (_c, quit_), copied, _s, _e = self._run(['z', forms.ESC])
        self.assertEqual(copied, [])
        self.assertFalse(quit_)

    def test_stay_open_keeps_view_after_number_clip(self):
        # Toggle STAY OPEN on with "s", then clip column 1: it clips but does NOT
        # quit; a following ESC leaves normally.
        (_c, quit_), copied, _s, _e = self._run(['s', '1', forms.ESC])
        self.assertEqual(copied, ['a@b.c'])
        self.assertFalse(quit_)

    def test_stay_open_keeps_view_after_c_clip(self):
        (_c, quit_), _copied, seqd, _ = self._run(['s', 'c', forms.ESC])
        self.assertEqual(seqd, [['a@b.c', 'user', 'pw']])
        self.assertFalse(quit_)

    def test_stay_open_toggle_reflected_in_holder(self):
        # The holder is flipped in place so the caller (App) keeps the state for
        # the session; toggling twice returns to off.
        holder = [False]
        self._run(['s', forms.ESC], stay_open=holder)
        self.assertTrue(holder[0])
        holder2 = [False]
        self._run(['s', 's', forms.ESC], stay_open=holder2)
        self.assertFalse(holder2[0])

    def test_stay_open_persists_from_caller_holder(self):
        # If the caller already has STAY OPEN on, clipping does not quit.
        (_c, quit_), copied, _s, _e = self._run(['1', forms.ESC], stay_open=[True])
        self.assertEqual(copied, ['a@b.c'])
        self.assertFalse(quit_)

    def test_e_edits_and_propagates_change(self):
        # "e" calls edit_entry; a change there marks entry_view's changed True.
        entry = self._entry()
        orig_edit = forms.edit_entry
        forms.edit_entry = lambda win, e: (True, False)
        try:
            win = _KeyWin([ord('e'), forms.ESC])
            changed, quit_ = forms.entry_view(
                win, entry, do_copy=lambda v: None,
                do_sequence=lambda vs: None)
        finally:
            forms.edit_entry = orig_edit
        self.assertTrue(changed)
        self.assertFalse(quit_)

    def test_q_quits(self):
        (changed, quit_), _c, _s, _e = self._run(['q'])
        self.assertTrue(quit_)

    def test_clip_in_order_with_no_columns_is_noop(self):
        # Title + notes only -> nothing to clip in order; stays (does not quit).
        entry = Entry([['title', 'x'], ['notes', 'just a note']])
        (_c, quit_), copied, seqd, _ = self._run(['c', forms.ESC], entry=entry)
        self.assertEqual(seqd, [])
        self.assertFalse(quit_)


class ClipboardSpawnTests(unittest.TestCase):
    """clipboard.copy / copy_sequence launch the detached paste-once owner with the
    values fed over stdin (NUL-separated). We capture the spawn instead of really
    forking, and check the payload + argv."""

    def _capture(self):
        calls = {}

        def fake_spawn(values, timeout=None):
            calls['values'] = list(values)
            calls['timeout'] = timeout
            return True

        return calls, fake_spawn

    def test_copy_single_value(self):
        calls, fake = self._capture()
        orig = clipboard._spawn_owner
        clipboard._spawn_owner = fake
        try:
            self.assertTrue(clipboard.copy('secret'))
        finally:
            clipboard._spawn_owner = orig
        self.assertEqual(calls['values'], ['secret'])

    def test_copy_sequence_values_in_order(self):
        calls, fake = self._capture()
        orig = clipboard._spawn_owner
        clipboard._spawn_owner = fake
        try:
            self.assertTrue(clipboard.copy_sequence(['a', 'b', 'c']))
        finally:
            clipboard._spawn_owner = orig
        self.assertEqual(calls['values'], ['a', 'b', 'c'])

    def test_copy_sequence_empty_is_false(self):
        self.assertFalse(clipboard.copy_sequence([]))

    def test_copy_none_becomes_empty(self):
        calls, fake = self._capture()
        orig = clipboard._spawn_owner
        clipboard._spawn_owner = fake
        try:
            clipboard.copy(None)
        finally:
            clipboard._spawn_owner = orig
        self.assertEqual(calls['values'], [''])

    def test_copy_falls_back_to_plain_when_spawn_fails(self):
        # If the owner cannot spawn, copy() falls back to a plain xclip write.
        plain = {}
        orig_spawn = clipboard._spawn_owner
        orig_plain = clipboard._plain_copy
        clipboard._spawn_owner = lambda values, timeout=None: False
        clipboard._plain_copy = lambda text: plain.setdefault('text', text) or True
        try:
            self.assertTrue(clipboard.copy('fallback-me'))
        finally:
            clipboard._spawn_owner = orig_spawn
            clipboard._plain_copy = orig_plain
        self.assertEqual(plain['text'], 'fallback-me')


class ClipboardOwnerServerTests(unittest.TestCase):
    """clipboard_owner._Server is the pure sequencing brain of the paste-once
    owner (no X needed). A data serve advances to the next value; the sequence is
    'done' only after the LAST value is served exactly once."""

    def test_single_value_done_after_one_serve(self):
        s = clipboard_owner._Server(['only'])
        self.assertEqual(s.current_value(), 'only')
        self.assertFalse(s.done)
        s.note_data_served()
        self.assertTrue(s.done)               # cleared after one paste
        self.assertIsNone(s.current_value())

    def test_sequence_advances_per_serve(self):
        s = clipboard_owner._Server(['e', 'u', 'p'])
        self.assertEqual(s.current_value(), 'e')
        s.note_data_served()
        self.assertEqual(s.current_value(), 'u')
        self.assertFalse(s.done)
        s.note_data_served()
        self.assertEqual(s.current_value(), 'p')
        self.assertFalse(s.done)
        s.note_data_served()
        self.assertTrue(s.done)               # done only after the last value
        self.assertIsNone(s.current_value())


class ClassifyDataServeTests(unittest.TestCase):
    """clipboard_owner._classify_data_serve is the pure rule that decides whether a
    DATA serve is the clipboard manager (uncounted) or the user's paste (counted).
    It replaced a time-only rule that swallowed fast real pastes; this pins the fix:
    a serve from a NON-manager requestor counts even when it lands early, while the
    manager's requestor is never counted."""

    GRACE = clipboard_owner._MANAGER_GRACE

    def test_early_serve_is_manager(self):
        # Any data serve inside the grace window is the manager priming its cache.
        self.assertEqual(
            clipboard_owner._classify_data_serve(111, 0.001, self.GRACE, set()),
            'manager')

    def test_late_serve_from_new_window_is_paste(self):
        # Past grace, a window we have NOT seen -> the user's real paste.
        self.assertEqual(
            clipboard_owner._classify_data_serve(222, self.GRACE + 0.01, self.GRACE,
                                                 {111}),
            'paste')

    def test_fast_paste_from_other_window_still_counts(self):
        # The regression the 0.5s window caused: a real paste at 0.2s. With the
        # requestor rule and a 0.15s grace, 0.2s is already past grace, and the
        # paste comes from a window that is NOT the manager -> it COUNTS.
        self.assertEqual(
            clipboard_owner._classify_data_serve(999, 0.2, self.GRACE, {111}),
            'paste')

    def test_known_manager_requestor_never_counts(self):
        # A later grab from the manager's own window, even past grace, is ignored.
        self.assertEqual(
            clipboard_owner._classify_data_serve(111, 5.0, self.GRACE, {111}),
            'ignore')

    def test_grace_is_tight(self):
        # The window must be far below human copy-then-focus-then-paste time, yet
        # comfortably above the manager's ~1ms grab. Guard the chosen value.
        self.assertLessEqual(clipboard_owner._MANAGER_GRACE, 0.25)
        self.assertGreaterEqual(clipboard_owner._MANAGER_GRACE, 0.02)


class DeleteNoStatusTests(unittest.TestCase):
    """PROMPT.md: deleting an entry must NOT leave a "deleted" status label. The
    handler removes the entry and marks the store dirty, but sets no status."""

    def _app(self):
        store = Store([Entry([['title', 'gone.com'], ['password', 'p']]),
                       Entry([['title', 'keep.com'], ['password', 'p']])])
        app = terminal_user_interface.App(_FakeWin(), store)
        app.mode = terminal_user_interface.SELECT
        app.refilter()
        return app, store

    def test_delete_sets_no_status(self):
        app, store = self._app()
        app.sel = 0
        target = app.selected_entry()
        orig = forms.confirm_delete
        forms.confirm_delete = lambda *a, **k: True
        try:
            app.handle_select(ord('x'))
        finally:
            forms.confirm_delete = orig
        self.assertNotIn(target, store.entries)   # actually removed
        self.assertTrue(app.dirty)
        self.assertEqual(app.status, '')          # NO "deleted" label

    def test_cancel_delete_keeps_entry(self):
        app, store = self._app()
        app.sel = 0
        target = app.selected_entry()
        orig = forms.confirm_delete
        forms.confirm_delete = lambda *a, **k: False
        try:
            app.handle_select(ord('x'))
        finally:
            forms.confirm_delete = orig
        self.assertIn(target, store.entries)
        self.assertFalse(app.dirty)


if __name__ == '__main__':
    unittest.main(verbosity=2)
