"""Persisted paths for the password store. No secrets are stored here -- the
master password is the GPG passphrase and lives only in memory for a session."""

import json
import os

# The config (paths only, no secrets) lives in the USER's home, NOT beside the code:
# Azzio installs this package root-owned under /usr/local/lib/azzio-passwords, which
# a normal user cannot write to, so the setup script's save() must land somewhere the
# user owns. ~/.config/azzio-passwords/passwords.cfg is that place (XDG-style).
CONFIG_PATH = os.path.join(
    os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')),
    'azzio-passwords', 'passwords.cfg')

# DEFAULT_SOURCE: default plaintext file the setup script encrypts.
# DEFAULT_SESSION: where `passwords` decrypts the store to for a session.
# DEFAULT_ENCRYPTED: the encrypted store itself. All three live under ~/Vault
# (Azzio ships ~/Vault as a top-level home dir -- see the distribution's
# packages/file_manager/home_directory); the encrypted .gpg is the ONLY file that persists
# between sessions -- the plaintext is decrypted to it and then deleted on every run.
DEFAULT_SOURCE = os.path.expanduser('~/Vault/passwords.txt')
DEFAULT_SESSION = os.path.expanduser('~/Vault/passwords.txt')
DEFAULT_ENCRYPTED = os.path.expanduser('~/Vault/passwords.txt.gpg')


def load():
    """Return the config dict, filled with defaults for any missing key."""
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    # Back-compat: older configs used the key "plaintext_path".
    if 'session_path' not in data and 'plaintext_path' in data:
        data['session_path'] = data['plaintext_path']
    data.setdefault('session_path', DEFAULT_SESSION)
    data.setdefault('encrypted_path', DEFAULT_ENCRYPTED)
    return data


def save(data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(data, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)
    return CONFIG_PATH


def exists():
    return os.path.exists(CONFIG_PATH)
