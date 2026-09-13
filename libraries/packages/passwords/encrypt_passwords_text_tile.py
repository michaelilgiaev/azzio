#!/usr/bin/env python3
"""OPTIONAL importer: encrypt an EXISTING plaintext password list into the store.

You do NOT need this to start using `passwords` -- running `passwords` with no
store creates an empty encrypted one for you (see passwords.py). This script is
only for the case where you already have a plaintext file (default
~/Vault/passwords.txt, the documented format) and want to import it in bulk.

Asks where the plaintext file is and the master password, encrypts it with GPG
(AES256), VERIFIES the ciphertext decrypts back to the exact source, saves the
paths to the config, and only then offers to delete the original plaintext. After
this, use the `passwords` command.
"""

import getpass
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import cryptography
import live_keyboard_line
from keyboard import keyboard_status_line


def _prompt_with_keyboard_line(prompt):
    """getpass(``prompt``) with the LIVE keyboard/Caps-Lock line refreshing above it while it
    blocks (Caps Lock / layout changes AT the prompt show immediately, like `backup`/`unpack`).
    Off a tty / off X it degrades to a single static print. Mirrors passwords._prompt_with_keyboard_line."""
    return live_keyboard_line.prompt_with_live_keyboard_line(
        lambda: getpass.getpass(prompt), keyboard_status_line)


def ask(prompt, default):
    val = input('%s (ENTER = %s): ' % (prompt, default)).strip()
    return os.path.expanduser(val or default)


def _verify_roundtrip(enc_path, src_path, passphrase):
    """Decrypt enc_path and byte-compare against src_path. Returns True on match.
    Guarantees the store is recoverable before any plaintext is deleted."""
    fd, tmp = tempfile.mkstemp(prefix='.pwverify-')
    os.close(fd)
    try:
        cryptography.decrypt_to_file(enc_path, tmp, passphrase)
        with open(tmp, 'rb') as a, open(src_path, 'rb') as b:
            return a.read() == b.read()
    except cryptography.CryptoError:
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    print('Encrypt password text file (GPG AES256).')

    src = ask('Unencrypted text file to encrypt', config.DEFAULT_SOURCE)
    if not os.path.exists(src):
        print('Not found: %s' % src)
        return 1

    # The encrypted store is just the source name + ".gpg" (no prompt).
    out = src + '.gpg'

    pw = _prompt_with_keyboard_line('Master password: ')
    if not pw:
        print('Empty password, aborting.')
        return 1
    if pw != _prompt_with_keyboard_line('Confirm master password: '):
        print('Passwords do not match.')
        return 1

    try:
        cryptography.encrypt(src, out, pw)
    except cryptography.CryptoError as e:
        print('Encryption failed: %s' % e)
        return 1

    if not _verify_roundtrip(out, src, pw):
        print('Verification FAILED: the encrypted file did not decrypt back to '
              'the source. Leaving the plaintext in place. (%s removed)' % out)
        try:
            os.remove(out)
        except OSError:
            pass
        return 1

    config.save({'encrypted_path': out, 'session_path': config.DEFAULT_SESSION})
    print('Encrypted and verified -> %s' % out)

    ans = input('Delete the original plaintext "%s"? [Y/n]: ' % src).strip().lower()
    if ans in ('', 'y', 'yes'):
        os.remove(src)
        print('Deleted plaintext.')
    elif os.path.abspath(src) == os.path.abspath(config.DEFAULT_SESSION):
        print('Note: each `passwords` session decrypts to and then deletes "%s", '
              'so this kept copy will be replaced and removed by the next run. '
              'Your data stays safe in the encrypted store.' % src)

    print('Done. Run:  passwords')
    return 0


if __name__ == '__main__':
    sys.exit(main())
