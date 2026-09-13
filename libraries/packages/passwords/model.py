"""Data model and text<->object (de)serialization for the password store.

On-disk plaintext format (one or more entries):

    ####
    title) <title>
    password) <secret>
    <key>) <value>          # zero or more extra single-line elements
    notes)                  # optional, normally last
        <indented line>     # zero or more note (random text) lines
    ####

Entries are wrapped by delimiter lines (canonically "####"). Parsing is lenient:
any non-indented line whose stripped form starts with "###" is a delimiter, and
any block of non-blank lines between two delimiters is one entry. This tolerates
the sloppy "###" / "###ww" delimiters that can appear in hand-written files.
"""

import re

# key) value  -- the space after ")" is optional so "notes)" parses too. The key
# is everything up to the first ")", so any key serialize() can emit (digits,
# "-", ".", spaces) parses back identically. Keys never contain ")" (clean_key
# strips it on input), so splitting on the first ")" is unambiguous.
KEY_RE = re.compile(r'^([^)\n]+)\) ?(.*)$')
DELIM = '####'
NOTE_INDENT = '    '
# Only the title is required to save an entry now; password is just another
# optional element. Kept as ('title',) so the editor still refuses to blank or
# remove the title while letting the password be edited/removed freely.
ESSENTIAL = ('title',)

# Leading URI scheme (http/https/ftp/...) plus optional "www.", stripped from a
# title so pasting "https://www.example.com" stores just "example.com". Matches a
# scheme "<letters>://" and/or a leading "www." at the very start, case-insensitive.
_SCHEME_RE = re.compile(r'^\s*(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?(?:www\.)?', re.IGNORECASE)


def strip_scheme(title):
    """Strip a leading URI scheme and/or "www." from a title.

    "https://www.example.com" -> "example.com", "http://foo.io/x" -> "foo.io/x",
    "example.com" -> "example.com". Only the very start is touched; the rest of
    the string (path, query) is left intact. Leading whitespace is dropped too."""
    return _SCHEME_RE.sub('', title, count=1)


def clean_key(name):
    """Normalize a user-entered element name so it always round-trips: no ")"
    (which would split the line) and no newlines, trimmed at the edges."""
    return name.replace(')', '').replace('\n', '').replace('\r', '').strip()


def notes_last(elements):
    """Return `elements` reordered so any 'notes' element sits LAST, with the
    relative order of everything else untouched (a stable partition).

    Notes are pinned to the bottom of every entry (PROMPT.md): they are free-form
    text meant to be read/edited by hand, always separated from the columns, and
    never part of the reorderable middle. Applying this on serialize keeps the
    on-disk file consistent (notes always last) no matter what order the elements
    happen to be in memory, while leaving the title and the columns between it and
    notes exactly where the user put them."""
    non_notes = [e for e in elements if e[0] != 'notes']
    notes = [e for e in elements if e[0] == 'notes']
    return non_notes + notes


def move_element(elements, index, delta):
    """Move the [key, value] at `index` by `delta` (typically -1 up or +1 down)
    within `elements`, in place. Returns the element's new index (unchanged if the
    move is not allowed).

    Two elements are pinned and never move, so only the columns BETWEEN them are
    reorderable:
      * the title is pinned FIRST -- it never moves, and nothing may move into
        position 0 while a title occupies it;
      * a 'notes' element is pinned LAST -- notes never moves, and nothing may
        move past notes into the final slot.
    This backs the reorder screens (new-entry wizard and the editor) so the
    on-disk element order -- exactly what the list's "(N: names)" and the detail
    view render -- becomes the order the user arranges, with notes always at the
    bottom."""
    n = len(elements)
    if not (0 <= index < n):
        return index
    target = index + delta
    if not (0 <= target < n):
        return index                      # already at an edge
    key = elements[index][0]
    if key in ('title', 'notes'):
        return index                      # title/notes are pinned, never move
    # Do not let another element take the title's slot (index 0) when a title is
    # present -- the title stays first.
    if target == 0 and elements and elements[0][0] == 'title':
        return index
    # Do not let another element cross BELOW a notes element -- notes stays last.
    if elements[target][0] == 'notes':
        return index
    elements[index], elements[target] = elements[target], elements[index]
    return target


def _is_delimiter(line):
    # Delimiters are never indented; note/content lines always are. This keeps an
    # indented note like "    ### heading" from being mistaken for a delimiter.
    return line[:1] not in (' ', '\t') and line.lstrip().startswith('###')


class Entry:
    def __init__(self, elements=None, original_body=None):
        # elements: ordered list of [key, value]; value may contain newlines (notes).
        self.elements = elements if elements is not None else []
        # original_body: the entry's EXACT inner text as parsed (the lines between
        # its delimiters, verbatim -- blank lines and element order preserved). Kept
        # so an UNMODIFIED entry serializes back byte-for-byte, honouring PROMPT.md's
        # "the passwords.txt itself should remain the same, no new lines no nothing".
        # Cleared the moment the entry is edited (mark_dirty), after which it is
        # re-serialized canonically (notes forced last). None for entries created in
        # the app (they have no original on disk -> always canonical).
        self._original_body = original_body

    def mark_dirty(self):
        """Forget the parsed-from-disk text so the next serialize() rebuilds this
        entry canonically. Call whenever the entry's elements are changed (edited,
        reordered, a column added/removed) -- the user touched it, so re-formatting
        it (notes last, normalized spacing) is now wanted and expected."""
        self._original_body = None

    def get(self, key):
        for k, v in self.elements:
            if k == key:
                return v
        return None

    def set(self, key, value):
        self.mark_dirty()
        for pair in self.elements:
            if pair[0] == key:
                pair[1] = value
                return
        self.elements.append([key, value])

    @property
    def title(self):
        return self.get('title') or ''

    @property
    def password(self):
        return self.get('password') or ''

    def non_title_elements(self):
        return [e for e in self.elements if e[0] != 'title']

    def element_names(self):
        return [e[0] for e in self.non_title_elements()]

    def display_elements(self):
        """Elements in display/serialize order: title/columns as stored, with any
        notes forced last (notes is pinned to the bottom of every entry)."""
        return notes_last(self.elements)

    def copy_sequence(self):
        """The values clipped by the "clip each column in order" action: every
        column except the title and notes, top to bottom (so the user pastes
        e.g. email, then username, then password). Notes is excluded -- it is
        free-form text, not part of the ordered credential paste."""
        return [e for e in self.elements if e[0] not in ('title', 'notes')]

    def serialize(self):
        """The entry as on-disk text, wrapped in delimiter lines.

        An UNMODIFIED entry (still holding its parsed-from-disk body) is emitted
        verbatim -- exact bytes, blank lines and element order untouched -- so
        saving the store never reformats entries the user did not edit. A new or
        edited entry is rebuilt canonically: title first, columns, notes LAST,
        one line each (multi-line notes indented). Delimiters are always the
        canonical DELIM."""
        if self._original_body is not None:
            body = self._original_body
            return DELIM + ('\n' + body if body else '') + '\n' + DELIM
        lines = [DELIM]
        for key, value in self.display_elements():
            if key == 'notes' or '\n' in value:
                lines.append('%s)' % key)
                if value != '':
                    for vline in value.split('\n'):
                        lines.append(NOTE_INDENT + vline)
            elif value == '':
                lines.append('%s)' % key)
            else:
                lines.append('%s) %s' % (key, value))
        lines.append(DELIM)
        return '\n'.join(lines)


class Store:
    def __init__(self, entries=None):
        self.entries = entries if entries is not None else []

    @classmethod
    def parse(cls, text):
        entries = []
        block = []
        for raw in text.splitlines():
            if _is_delimiter(raw):
                if any(l.strip() for l in block):
                    entries.append(cls._parse_block(block))
                block = []
            else:
                block.append(raw)
        if any(l.strip() for l in block):
            entries.append(cls._parse_block(block))
        return cls(entries)

    @staticmethod
    def _block_body(lines):
        """The block's verbatim inner text with only leading/trailing blank lines
        trimmed (those are inter-entry spacing, reconstructed by Store.serialize).
        Blank lines BETWEEN fields are kept -- that is the hand-formatting we
        preserve for untouched entries."""
        start, end = 0, len(lines)
        while start < end and lines[start].strip() == '':
            start += 1
        while end > start and lines[end - 1].strip() == '':
            end -= 1
        return '\n'.join(lines[start:end])

    @staticmethod
    def _parse_block(lines):
        original_body = Store._block_body(lines)
        elements = []
        for raw in lines:
            if raw[:1] in (' ', '\t'):
                # Indented continuation -> append to the current element (notes).
                # Strip only one level of indentation (the NOTE_INDENT prefix, or
                # a leading tab as used by hand-written files) so any deeper
                # indentation inside the note survives the round-trip.
                if elements:
                    if raw.startswith(NOTE_INDENT):
                        text = raw[len(NOTE_INDENT):]
                    elif raw[:1] == '\t':
                        text = raw[1:]
                    else:
                        text = raw.lstrip()
                    if elements[-1][1] == '':
                        elements[-1][1] = text
                    else:
                        elements[-1][1] += '\n' + text
                continue
            if raw.strip() == '':
                continue
            m = KEY_RE.match(raw)
            if m:
                elements.append([m.group(1), m.group(2)])
            elif elements:
                # Non-indented, non-key line -> fold into the current value.
                elements[-1][1] += ('\n' if elements[-1][1] else '') + raw
        return Entry(elements, original_body=original_body)

    def serialize(self):
        if not self.entries:
            return ''
        return '\n\n'.join(e.serialize() for e in self.entries) + '\n'

    def filter(self, query):
        """Entries whose title contains query (case-insensitive), sorted
        alphabetically by title (case-insensitive). Empty query returns
        everything (still sorted) -- the search/select UI shows this list, so the
        first page is always in alphabetical order."""
        q = query.strip().lower()
        if not q:
            matches = list(self.entries)
        else:
            matches = [e for e in self.entries if q in e.title.lower()]
        return sorted(matches, key=lambda e: e.title.lower())
