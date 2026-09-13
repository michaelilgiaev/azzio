"""GPG symmetric encryption helpers (AES256).

The master password is passed as the GPG passphrase via stdin (--passphrase-fd 0)
so it never appears in the process arg list. Nothing about it is stored on disk;
a wrong password simply makes decryption fail.
"""

import os
import subprocess

_GPG_COMMON = ['gpg', '--batch', '--yes', '--quiet',
               '--pinentry-mode', 'loopback', '--passphrase-fd', '0']


class CryptoError(Exception):
    pass


def _run(cmd, passphrase):
    # Tighten the umask so gpg creates its output file as 0600 from the start --
    # the later chmod would otherwise leave a brief 0644 window on the plaintext.
    old_umask = os.umask(0o077)
    try:
        return subprocess.run(cmd, input=passphrase.encode(),
                              capture_output=True)
    except FileNotFoundError:
        raise CryptoError('gpg not found; install gnupg')
    finally:
        os.umask(old_umask)


def encrypt(src_path, out_path, passphrase):
    """Encrypt src_path -> out_path with AES256 symmetric encryption."""
    cmd = _GPG_COMMON + ['--symmetric', '--cipher-algo', 'AES256',
                         '-o', out_path, src_path]
    proc = _run(cmd, passphrase)
    if proc.returncode != 0:
        raise CryptoError(proc.stderr.decode(errors='replace').strip())
    os.chmod(out_path, 0o600)


def decrypt_to_file(enc_path, out_path, passphrase):
    """Decrypt enc_path -> out_path. Raises CryptoError on a wrong passphrase
    (or a corrupt store), leaving no plaintext behind."""
    cmd = _GPG_COMMON + ['-o', out_path, '-d', enc_path]
    proc = _run(cmd, passphrase)
    if proc.returncode != 0:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise CryptoError(proc.stderr.decode(errors='replace').strip())
    os.chmod(out_path, 0o600)
