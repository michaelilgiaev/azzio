"""passwords - Azzio's encrypted (GPG AES256) terminal password manager.

This package holds the whole application in one flat directory (there is no
`pwlib/` sub-library anymore): the entry script plus every module it calls.

Entry point:
    passwords                       the `passwords` command (self-inits + UI driver)

Working modules (imported by the entry script and each other):
    config                          persisted store paths (no secrets)
    cryptography                    GPG symmetric (AES256) encrypt/decrypt
    model                           Entry/Store data model + text (de)serialization
    clipboard                       launches the paste-once clipboard owner (xclip fallback)
    clipboard_owner                 the detached X CLIPBOARD owner (paste once, then clear)
    forms                           curses primitives + the entry view (columns/copy/edit)
    new_entry                       the new-entry wizard and the column-reorder screen
    terminal_user_interface         the main search/select curses UI
    keyboard                        live keyboard layout + Caps Lock readout at the prompt
    help                            shared help text

Also here (not part of the runtime import graph):
    encrypt_passwords_text_tile     optional importer: bulk-encrypt an existing plaintext
    packaging                       ISO build wiring (install paths, launcher, emit_plan)

The modules import each other by BARE top-level name (e.g. `import model`,
`from forms import addstr`). At runtime the launcher runs `passwords.py` with this
directory on sys.path, so those resolve as sibling modules; the `__init__.py` here
makes the same directory importable as the `packages.passwords` package for the
test suite.
"""
