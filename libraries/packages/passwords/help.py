"""Shared help text for `passwords -h` (the in-UI 'h' help screen was removed --
navigation now mirrors the Azzio terminal UI: ESC goes back, Q quits)."""

HELP = """passwords - encrypted password manager

USAGE
  passwords             unlock and open the search UI
  passwords -h | -help  show this help

UNLOCK
  first run       prompts you to CREATE a master password, then makes an empty
                  encrypted store at ~/Vault/passwords.txt.gpg
  after that      prompts for that master password to unlock the store
  the store is decrypted to a session plaintext only while the UI is open

NAVIGATION (mirrors the Azzio terminal UI)
  WASD / HJKL / arrows   move the highlight
  /                      search
  ESC                    jump back to the start of the UI (never quits; spammable)
  q / Q                  quit

SEARCH MODE  (default; cursor in the search box)
  type           filter entries by title, updates live
  arrows         move into the result list (switches to SELECT mode)
  enter          jump the cursor down into the result list (first match); does
                 NOT copy and does NOT quit. with no matches it does nothing
  n              new entry (works even with no results -- add the first entry);
                 only a title is required, and a leading http(s):// / www. is
                 stripped from it. ENTER on an empty title warns you nothing was
                 written; pressing ENTER again then exits the new entry.
  esc            jump back to the start of the UI
  q              quit

SELECT MODE  (cursor on the result list)
  WASD/HJKL/arrows  move the highlight
  enter             open the entry view (see below) for the highlighted entry
  x                 delete the entry (must type "yes"); no label is left behind
  n                 new entry: enter a title (only requirement), then pick
                    columns from a numbered menu -- Email, Username, Password,
                    Notes, or a custom-named column; type "r" in that menu to
                    reorder the columns you have added (notes always stays last)
  /                 drop back to the search box
  esc               jump back to the start of the UI
  backspace         back to the search box and delete the last query character
  q                 quit

ENTRY VIEW  (open with ENTER on a result)
  shows every column (title first, notes always last under a blank line) and acts
  on a SINGLE keypress -- there is no typed input and no ENTER:
  a number       clip that one column to the clipboard, then QUIT (see STAY OPEN)
  c              clip every column IN ORDER (e.g. email, then username, then
                 password -- notes excluded) so you paste them one after another,
                 then QUIT (see STAY OPEN)
  s              toggle STAY OPEN (see below)
  e              edit: change / rename / add / remove / reorder elements
                 ([ moves the highlighted column up, ] moves it down; the title
                 stays pinned first and notes pinned last). removing a column
                 asks you to type "yes" first
  esc            back to the result list
  q              quit

STAY OPEN  (toggle inside the entry view; off by default, not saved)
  clipping normally drops you straight back to the shell -- clip and you are out.
  press "s" to turn STAY OPEN on and the manager keeps running after you clip, so
  you can grab several things in a row. it lasts only for this run: relaunching
  starts with STAY OPEN off again.

CLIPBOARD  (paste once, then it clears)
  a copied column is held for a SINGLE paste; after you paste it once the
  clipboard clears itself. "clip in order" hands you the columns one at a time --
  paste, and the next is ready -- clearing after the last. copying something else
  yourself (in any app) cancels what the manager was holding. nothing survives a
  reboot (a desktop clipboard manager with saved history is the one exception,
  outside this app's control).

RESULT LINE
  > title   (N: name1, name2, ...)   N = count of non-title elements; the names
  are listed in the order you arranged them (the order the columns were picked
  when the entry was created, as changed by reorder; notes is always last)
  entries themselves are listed alphabetically by title

ON QUIT
  no changes -> the session plaintext is just deleted
  changes    -> saved and re-encrypted into the store, then the session
                plaintext is deleted (you are told the changes were saved)
"""
