#!/usr/bin/env python3
"""The `passwords` command -- Azzio's encrypted password manager.

Streamlined: there is no separate setup step and nothing to source. Running
`passwords`:

  * FIRST RUN (no store at ~/Vault/passwords.txt.gpg) -- prompts you to CREATE a
    master password and writes an empty encrypted store. You are dropped straight
    into the UI; press 'n' to add the first entry.
  * AFTER THAT -- prompts for the master password (which is the GPG passphrase,
    never stored anywhere), decrypts the store to a session plaintext under
    ~/Vault for the search/select UI, then on quit re-encrypts ONLY if something
    changed (telling you it saved) and always deletes the session plaintext.

CRASH RECOVERY. The session plaintext is shredded on every catchable exit: normal
quit, exceptions, KeyboardInterrupt (atexit), and SIGTERM/SIGHUP (signal
handlers). Only SIGKILL and power loss -- uncatchable -- can leave it behind. So
on startup, BEFORE decrypting anything, we check for a leftover plaintext at the
session path. If one is there the last session died hard. The master password is
NOT stored, so we cannot silently re-encrypt it; instead we ALERT the user and
offer to re-encrypt the recovered plaintext now (asking for a master password).
Declining leaves the file in place and aborts, rather than silently deleting the
only copy of their data or clobbering it with a fresh decrypt.
"""

import atexit
import getpass
import os
import signal
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import cryptography
import live_keyboard_line
import terminal_user_interface
from help import HELP
from keyboard import keyboard_status_line
from model import Store


def _prompt_with_keyboard_line(prompt):
    """getpass(``prompt``) with the LIVE keyboard/Caps-Lock line refreshing above it while it
    blocks -- so toggling Caps Lock or switching layout AT the prompt updates the line
    immediately, exactly as `backup`/`unpack` now do. A thin adapter around
    live_keyboard_line.prompt_with_live_keyboard_line binding the status source
    (keyboard.keyboard_status_line) and the input (getpass). Off a tty / off X it degrades to a
    single static "Keyboard: ..." print (the prior behaviour). Mirrors archive._prompt_with_keyboard_line."""
    return live_keyboard_line.prompt_with_live_keyboard_line(
        lambda: getpass.getpass(prompt), keyboard_status_line)


def _make_cleanup(path):
    state = {'done': False}

    def cleanup(*_):
        if state['done']:
            return
        state['done'] = True
        try:
            os.remove(path)
        except OSError:
            pass

    return cleanup


def _prompt_new_master():
    """Prompt for a new master password twice and return it, or None if the user
    gives an empty password or the two entries do not match."""
    pw = _prompt_with_keyboard_line('Create a master password: ')
    if not pw:
        print('Empty password, aborting.')
        return None
    if pw != _prompt_with_keyboard_line('Confirm master password: '):
        print('Passwords do not match.')
        return None
    return pw


def _encrypt_text(text, enc, passphrase):
    """Encrypt the given plaintext string into the store at `enc` (via a 0600
    temp file that is always removed). Raises cryptography.CryptoError on failure."""
    fd, tmp = tempfile.mkstemp(prefix='.pwinit-')
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        cryptography.encrypt(tmp, enc, passphrase)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _verify_store(enc, expected_text, passphrase):
    """Decrypt the store at `enc` with `passphrase` and confirm it round-trips to
    exactly `expected_text`. Returns True on an exact match, False otherwise (a
    wrong passphrase, a corrupt write, or any mismatch). Used before deleting a
    recovered plaintext so an unopenable/typo'd store can never strand the user.
    Always cleans up its temp plaintext."""
    fd, tmp = tempfile.mkstemp(prefix='.pwverify-')
    os.close(fd)
    try:
        cryptography.decrypt_to_file(enc, tmp, passphrase)
        with open(tmp) as f:
            return f.read() == expected_text
    except cryptography.CryptoError:
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _init_store(enc):
    """First-run: create an EMPTY encrypted store at `enc`. Returns the chosen
    master password (so the caller can open the fresh store without re-asking), or
    None if the user aborted."""
    print('No password store yet. Creating one at:')
    print('  %s' % enc)
    print('This master password unlocks it. It is never stored -- if you lose it, '
          'the store cannot be recovered.')
    pw = _prompt_new_master()
    if pw is None:
        return None
    os.makedirs(os.path.dirname(enc), exist_ok=True)
    try:
        _encrypt_text(Store([]).serialize(), enc, pw)
    except cryptography.CryptoError as e:
        print('Could not create the store: %s' % e)
        return None
    print('Created an empty encrypted store. Press "n" to add your first entry.')
    return pw


def _recover_stale_plaintext(enc, plain):
    """Handle a leftover session plaintext at `plain` (a hard crash last time).

    Alerts the user and offers to re-encrypt it into the store now. Returns:
      'resolved' -- the plaintext was re-encrypted and removed (safe to continue),
      'abort'    -- the user declined; the plaintext is left in place, do not open.
    """
    print('WARNING: found an UNENCRYPTED password file left from a previous '
          'session that did not exit cleanly:')
    print('  %s' % plain)
    print('Your machine may have lost power or been killed while `passwords` was '
          'open. The master password is not stored, so this file cannot be '
          're-encrypted automatically.')
    ans = input('Re-encrypt it into the store now? [Y/n]: ').strip().lower()
    if ans not in ('', 'y', 'yes'):
        print('Left the unencrypted file in place: %s' % plain)
        print('Re-run `passwords` to resolve it, or move/delete it yourself once '
              'you have secured its contents.')
        return 'abort'

    # Re-encrypt with a master password the user supplies. If the store already
    # exists we keep the SAME encrypted file (this recovered plaintext is the
    # freshest copy of the data); if it does not, we are recovering a first-run
    # crash and simply create it.
    #
    # The password is confirmed (entered twice) AND the written store is verified
    # to decrypt back to the exact recovered text BEFORE the plaintext is removed.
    # gpg accepts ANY passphrase, so without this a single typo would silently
    # lock the recovered data behind a password the user never meant to set and
    # then delete the only readable copy. We do not delete the plaintext unless
    # both the confirmation and the round-trip pass -- so a mistake is always
    # recoverable by just re-running.
    for _ in range(3):
        pw = _prompt_with_keyboard_line('Master password to re-encrypt with: ')
        if not pw:
            continue
        if pw != _prompt_with_keyboard_line('Confirm master password: '):
            print('Passwords do not match; try again.')
            continue
        try:
            with open(plain) as f:
                text = f.read()
            _encrypt_text(text, enc, pw)
        except (OSError, cryptography.CryptoError) as e:
            print('Re-encrypt failed: %s' % e)
            return 'abort'
        if not _verify_store(enc, text, pw):
            print('Re-encrypted store did not verify; left the unencrypted file '
                  'in place: %s' % plain)
            return 'abort'
        try:
            os.remove(plain)
        except OSError:
            pass
        print('Recovered and re-encrypted -> %s' % enc)
        return 'resolved'
    print('No usable password given; left the unencrypted file in place: %s'
          % plain)
    return 'abort'


def main(argv):
    if any(a in ('-h', '-help', '--help') for a in argv):
        print(HELP)
        return 0

    cfg = config.load()
    enc = cfg['encrypted_path']
    plain = cfg['session_path']

    # Crash recovery FIRST: a plaintext sitting at the session path means the last
    # session died before it could shred it. Handle it before we touch the store
    # so a fresh decrypt can never clobber the recovered data.
    if os.path.exists(plain):
        if _recover_stale_plaintext(enc, plain) == 'abort':
            return 1

    # First run: no store yet -> create an empty one instead of pointing the user
    # at a setup script. _init_store returns the master password so we can open
    # the fresh store without asking again.
    fresh_pw = None
    if not os.path.exists(enc):
        fresh_pw = _init_store(enc)
        if fresh_pw is None:
            return 1

    # Register cleanup BEFORE the plaintext can exist so any later exit (incl. a
    # KeyboardInterrupt between decrypt and the try block) still removes it.
    cleanup = _make_cleanup(plain)
    atexit.register(cleanup)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, lambda *_: (cleanup(), os._exit(1)))
        except (ValueError, OSError):
            pass

    if fresh_pw is not None:
        # Just created the store; decrypt the empty store we wrote so the rest of
        # the flow is identical (and confirms the passphrase round-trips).
        pw = fresh_pw
        cryptography.decrypt_to_file(enc, plain, pw)
    else:
        pw = None
        for _ in range(3):
            attempt = _prompt_with_keyboard_line('Master password: ')
            try:
                cryptography.decrypt_to_file(enc, plain, attempt)
                pw = attempt
                break
            except cryptography.CryptoError:
                print('Wrong master password.')
        if pw is None:
            return 1

    try:
        with open(plain) as f:
            store = Store.parse(f.read())
        dirty = terminal_user_interface.run(store)
        if dirty:
            with open(plain, 'w') as f:
                f.write(store.serialize())
            os.chmod(plain, 0o600)
            cryptography.encrypt(plain, enc, pw)
            print('changes saved and re-encrypted -> %s' % enc)
        else:
            print('no changes.')
    finally:
        cleanup()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
