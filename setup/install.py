#!/usr/bin/env python3
"""Install user-local helpers. Never overwrite an existing account credential."""
import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib

CLI_VERSION = '0.159.0'
SUPPORTED_CLI_VERSIONS = ('0.158.0', '0.159.0')
EXTENSION_VERSION = '26.917.62051'
VERSION = '0.2.1'
START = '# BEGIN N_CODEX_ACCOUNTS'
END = '# END N_CODEX_ACCOUNTS'
SOURCE = Path(__file__).resolve().parent


def load_accounts(user_root):
    spec = importlib.util.spec_from_file_location('accounts', SOURCE / 'accounts.py')
    accounts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(accounts)
    accounts.USER_ROOT = user_root
    accounts.CANONICAL = user_root / '.codex'
    accounts.REGISTRY_DIR = user_root / '.config/n-codex-accounts'
    accounts.REGISTRY = accounts.REGISTRY_DIR / 'accounts.json'
    accounts.BACKUP_ROOT = user_root / '.local/share/n-codex-accounts/auth-backups'
    return accounts


def install(user_root, binary, *, login=True, managed_update=False):
    journal = user_root/'.local/share/n-codex-accounts/update-journal.json'
    if not managed_update and journal.exists() and json.loads(journal.read_text())['status'] not in ('complete', 'rolled-back'):
        raise ValueError('An unfinished update needs recovery before installation')
    accounts = load_accounts(user_root)
    canonical = accounts.CANONICAL
    shell = user_root / '.zshrc'
    previous = shell.read_text() if shell.exists() else ''
    if previous.count(START) != previous.count(END) or previous.count(START) > 1:
        raise ValueError('Malformed existing shell integration; inspect .zshrc first')
    stripped = re.sub(re.escape(START) + r'.*?' + re.escape(END), '', previous, flags=re.S)
    if re.search(r'(?:function\s+codex\b|\bcodex\s*\(\s*\)|alias\s+codex=)', stripped):
        raise ValueError('Existing codex shell override found; reconcile it before installing')
    # Unknown sourced account switchers need human review; never silently supersede one.
    if re.search(r'^\s*(?:source|\.)\s+[^\n]*(?:codex|account)[^\n]*', stripped, re.M | re.I):
        raise ValueError('An existing sourced account integration needs review before installing')
    config = canonical / 'config.toml'
    parsed = tomllib.loads(config.read_text()) if config.exists() else {}
    if parsed.get('cli_auth_credentials_store', 'file') != 'file':
        raise ValueError('Existing credential storage is not file-based; no automatic conversion is performed')
    if parsed.get('sqlite_home') and Path(parsed['sqlite_home']).expanduser().resolve() != canonical.resolve():
        raise ValueError('Existing sqlite_home conflicts with canonical history')
    auth = canonical / 'auth.json'
    if auth.is_symlink():
        raise ValueError('Symlinked primary credentials are not supported')
    if not auth.is_file():
        if not login:
            raise ValueError('No primary file-based login; rerun interactively to initialize it')
        if accounts.active_account_processes('primary', canonical):
            raise ValueError('Close default-account Codex/VS Code processes before initial login')
        accounts.confirm('Initialize primary account using isolated browser login? [y/N]: ')
        staging = accounts.create_staging('primary')
        try:
            os.environ['CODEX_REAL_BINARY'] = str(binary)
            staged_auth = accounts.login_into(staging)
            accounts.credential_identity(staged_auth.read_bytes())
            canonical.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Exclusive creation: a concurrent login must never be overwritten.
            descriptor = os.open(auth, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(staged_auth.read_bytes())
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    else:
        accounts.credential_identity(auth.read_bytes())
    for item in ('sessions', 'archived_sessions', 'attachments', 'thread-writer-locks'):
        (canonical / item).mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = user_root / '.local/share/n-codex-accounts'
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    (destination / 'bin').mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = destination / 'backups' / stamp
    backup.mkdir(parents=True, mode=0o700)
    if shell.exists():
        shutil.copy2(shell, backup / 'zshrc')
        os.chmod(backup / 'zshrc', 0o600)
    python = str(Path(sys.executable).resolve())
    for source in SOURCE.iterdir():
        if source.suffix not in ('.py', '.zsh') or source.name == 'install.py':
            continue
        target = destination / source.name
        if target.exists():
            shutil.copy2(target, backup / target.name)
        accounts.atomic_write(target, source.read_text(), 0o644)
    aliases = {'accounts': 'accounts.py', 'preflight': 'preflight.py',
               'run': 'run.zsh', 'guard': 'guard.zsh', 'vscode-account': 'vscode.zsh',
               'ncodex': 'updater.py'}
    for alias, filename in aliases.items():
        interpreter = python if filename.endswith('.py') else '/bin/zsh'
        command = f'#!/bin/sh\nexec {shlex.quote(interpreter)} {shlex.quote(str(destination / filename))} "$@"\n'
        accounts.atomic_write(destination / 'bin' / alias, command, 0o755)
    runtime = f'export CODEX_REAL_BINARY={shlex.quote(str(binary))}\n'
    accounts.atomic_write(destination / 'runtime.zsh', runtime, 0o600)
    accounts.atomic_write(destination / 'VERSION', VERSION + '\n', 0o644)
    accounts.ensure_core()
    block = f'{START}\nsource "$HOME/.local/share/n-codex-accounts/shell.zsh"\n{END}\n'
    if START in previous:
        updated = re.sub(re.escape(START) + r'.*?' + re.escape(END) + r'\n?', lambda _: block, previous, flags=re.S)
    else:
        updated = previous.rstrip() + '\n\n' + block
    accounts.atomic_write(shell, updated, shell.stat().st_mode & 0o777 if shell.exists() else 0o600)
    return backup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cli', type=Path, help='Path to the real Codex executable')
    parser.add_argument('--skip-login', action='store_true', help='Require an existing primary auth.json')
    parser.add_argument('--with-vscode', action='store_true', help='Install the reviewed VS Code extension release')
    args = parser.parse_args()
    if sys.platform != 'darwin' or sys.version_info < (3, 11):
        parser.error('Requires macOS and Python 3.11+')
    binary = args.cli or shutil.which('codex')
    if not binary:
        parser.error(f'Install the CLI first: npm install -g @openai/codex@{CLI_VERSION}')
    binary = Path(binary).expanduser().absolute()
    actual = subprocess.check_output([str(binary), '--version'], text=True).strip()
    if actual not in {f'codex-cli {version}' for version in SUPPORTED_CLI_VERSIONS}:
        parser.error(f'Reviewed CLI versions are {", ".join(SUPPORTED_CLI_VERSIONS)}; found {actual}. Do not downgrade newer migrated state automatically.')
    code = Path('/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code')
    if args.with_vscode and not code.is_file():
        parser.error('Install Visual Studio Code in /Applications before using --with-vscode')
    try:
        backup = install(Path.home(), binary, login=not args.skip_login)
        print(f'Installed. Shell/helper backup: {backup}')
        print('Activate: source ~/.zshrc')
        print('Then: codex add work')
        if args.with_vscode:
            subprocess.run([str(code), '--install-extension', f'openai.chatgpt@{EXTENSION_VERSION}'], check=True)
        if not args.skip_login and sys.stdin.isatty():
            accounts = load_accounts(Path.home())
            os.environ['CODEX_REAL_BINARY'] = str(binary)
            while True:
                label = input('Add another account name (Enter to finish): ').strip()
                if not label:
                    break
                with accounts.registry_lock():
                    accounts.add_account(label)

    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
