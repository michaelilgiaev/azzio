#!/usr/bin/env python3
"""Small presentation helpers shared by the `backup` and `unpack` commands (fully-named
module ``user_interface.py`` -- matching the repo convention of spelled-out module names
like command_line_interface.py / terminal_user_interface.py).

Both commands are interactive CLIs; keeping their headers, field rows, bullet lists,
rules, and status lines in ONE place means the two read the same and cannot drift in
tone (the PROMPT's step three asked for clean, aligned, readable output matching the
`passwords` manager). This is presentation only -- no archive/vault behaviour lives
here -- and it is Python standard library only (nothing but ``print``/``sys.stdout``).

The style mirrors the original prototype in data/backup.py: a plain-text title with a
thin rule under it, left-aligned ``Label: value`` rows, ``  - item`` bullets, and
``Warning:``/``note:`` prefixes -- no emojis, no colour, no unicode box-drawing beyond a
single horizontal rule that degrades to ASCII if the terminal cannot encode it.
"""

import sys

# The horizontal rule under a header. A light box-drawing dash if the stream can encode
# it (looks tidy in the Azzio kitty terminal), else a plain ASCII dash -- decided once
# per stream so a redirected/ASCII-only console never raises on a print.
_RULE_CHAR = "─"   # BOX DRAWINGS LIGHT HORIZONTAL
_RULE_WIDTH = 48


def _rule_char_for(stream):
    """Return the rule glyph this stream can actually encode ('-' as the safe fallback)."""
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        _RULE_CHAR.encode(encoding)
        return _RULE_CHAR
    except (UnicodeError, LookupError):
        return "-"


def rule(width=_RULE_WIDTH, stream=None):
    """A horizontal rule string of ``width`` chars (encoding-safe for ``stream``)."""
    stream = sys.stdout if stream is None else stream
    return _rule_char_for(stream) * width


def header(title, stream=None):
    """Print a section header: the title on its own line with a thin rule beneath it,
    and a trailing blank line for breathing room. Used at the top of `backup`/`unpack`."""
    stream = sys.stdout if stream is None else stream
    print(title, file=stream)
    print(rule(stream=stream), file=stream)


def field(label, value, width=8, stream=None):
    """Print one left-aligned ``Label   value`` row (labels padded to ``width`` so a
    block of rows lines up). ``label`` is given WITHOUT a trailing colon; the colon is
    added here so every row is punctuated identically."""
    stream = sys.stdout if stream is None else stream
    print(f"{(label + ':').ljust(width + 1)} {value}", file=stream)


def bullet(text, stream=None):
    """Print one ``  - text`` list item (the prototype's bullet style)."""
    stream = sys.stdout if stream is None else stream
    print(f"  - {text}", file=stream)


def result_line(label, dest, size_text, label_width=16, stream=None):
    """Print an aligned ``  <label>  ->  <dest>  (<size>)`` line for a written archive
    or a restored destination, so a column of them lines up on the arrow and the size."""
    stream = sys.stdout if stream is None else stream
    suffix = f"  ({size_text})" if size_text else ""
    print(f"  {label.ljust(label_width)}  ->  {dest}{suffix}", file=stream)


def note(text, stream=None):
    """Print a low-key ``  note: text`` line (a skipped-but-fine outcome)."""
    stream = sys.stdout if stream is None else stream
    print(f"  note: {text}", file=stream)


def warn(text, stream=None):
    """Print a ``  warning: text`` line (something the user should see but that did not
    fail the run -- e.g. the passphrase did not unlock the vault store)."""
    stream = sys.stdout if stream is None else stream
    print(f"  warning: {text}", file=stream)
