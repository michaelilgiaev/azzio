"""packages.backup -- Azzio's backup + restore (the `backup` and `unpack` commands).

`backup` prompts once for a passphrase and writes TWO GPG-encrypted archives to the
home dir: ``~/backup.tar.gz.gpg`` (the top-level home folders -- skipping the ``Ignore``
directory and hidden dot files, keeping symlinks AS links) and ``~/passwords.tar.gz.gpg``
(the password store ~/Vault/passwords.txt.gpg, included from its DECRYPTED contents when
the same passphrase unlocks it). `unpack` reverses either archive, restoring the home
dirs back into ``~/`` and the password store back to ``~/Vault/``.

This package is one flat directory (like packages/passwords):

Entry points:
    backup                          the `backup` command (creates the two archives)
    unpack                          the `unpack` command (restores an archive)

Shared:
    archive                         tar+gpg pipeline + passphrase helpers (imported by both)

Also here (not part of the runtime import graph):
    packaging                       ISO build wiring (install paths, launchers, emit_plan)

The apps are Python standard library only (tarfile + gpg via subprocess); packaging.py
ships every module flat to /usr/local/lib/azzio-backup/ and installs the
/usr/local/bin/backup and /usr/local/bin/unpack launchers. This ``__init__.py`` makes the
same directory importable as the ``packages.backup`` package for the test suite.
"""

from __future__ import annotations
