#!/usr/bin/env python3
"""Read-only checks before launching a selected account."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tomllib


def check(home, canonical):
    for key in ('CODEX_API_KEY', 'OPENAI_API_KEY', 'CODEX_ACCESS_TOKEN',
                'OPENAI_FEDERATION_RULE_ID', 'OPENAI_IDENTITY_TOKEN_FILE', 'CODEX_REMOTE_AUTH_TOKEN'):
        if os.environ.get(key):
            raise ValueError(f'Unset {key} before using saved ChatGPT account selection')
    if not (home / 'auth.json').is_file():
        raise ValueError(f'Credential file is missing in {home}')
    for name in ('sessions', 'archived_sessions', 'attachments', 'thread-writer-locks'):
        if not (canonical / name).is_dir():
            raise ValueError(f'Missing shared directory: {canonical / name}')
        if home != canonical and (not (home / name).is_symlink() or
                                  (home / name).resolve() != (canonical / name).resolve()):
            raise ValueError(f'Incorrect shared link: {home / name}')
    config = home / 'config.toml'
    value = tomllib.loads(config.read_text()) if config.exists() else {}
    if value.get('cli_auth_credentials_store', 'file') != 'file':
        raise ValueError('This setup requires cli_auth_credentials_store="file"')
    if value.get('sqlite_home') and Path(value['sqlite_home']).expanduser().resolve() != canonical.resolve():
        raise ValueError('sqlite_home conflicts with shared history')
    # A genuinely fresh home has no database until its first Codex session.
    for db in canonical.glob('state_*.sqlite'):
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError(f'State database integrity check failed: {db.name}')


def main():
    home = Path(sys.argv[1]).expanduser().resolve()
    canonical = (Path.home() / '.codex').resolve()
    try:
        journal = Path.home()/'.local/share/n-codex-accounts/update-journal.json'
        if journal.exists() and json.loads(journal.read_text())['status'] not in ('complete', 'rolled-back'):
            raise ValueError('An interrupted or active update needs recovery before launching')
        check(home, canonical)
        location = Path(__file__).resolve().parent
        subprocess.run([sys.executable, str(location / 'validate_lineage.py'),
                        str(canonical / 'sessions'), str(canonical / 'archived_sessions')],
                       check=True, stdout=subprocess.DEVNULL)
    except (ValueError, OSError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
