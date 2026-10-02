#!/usr/bin/env python3
"""Explicit, journaled helper updates. Never authenticates or replaces auth.json."""
from __future__ import annotations
import argparse
from contextlib import contextmanager, closing
from datetime import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

VERSION = '0.2.4'
REPO = 'khandelwaly940/n-codex-accounts'
PARTS = ('sessions', 'archived_sessions', 'attachments', 'thread-writer-locks')
SOURCE = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, indent=2); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def module(path):
    spec = importlib.util.spec_from_file_location('ncodex_' + path.stem, path)
    loaded = importlib.util.module_from_spec(spec); spec.loader.exec_module(loaded)
    return loaded


def runtime(user): return user / '.local/share/n-codex-accounts'


def version(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match: raise ValueError('Release is not a stable semantic version')
    return tuple(map(int, match.groups()))


def latest():
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/releases/latest',
                                     headers={'User-Agent': 'n-codex-accounts-updater'})
    with urllib.request.urlopen(request, timeout=3) as response:
        value = json.loads(response.read(1024 * 1024))
    version(value['tag_name'])
    if value.get('draft') or value.get('prerelease'): raise ValueError('Not a stable release')
    return {'tag': value['tag_name'], 'notes': str(value.get('body', ''))[:10000]}


def download(tag, directory):
    version(tag)
    base = f'https://github.com/{REPO}/releases/download/{tag}/'
    archive = directory / 'release.tar.gz'
    with urllib.request.urlopen(base + 'SHA256SUMS', timeout=15) as response:
        lines = response.read(16384).decode().splitlines()
    expected = [line.split()[0] for line in lines if line.split()[-1:] == ['n-codex-accounts.tar.gz']]
    if len(expected) != 1 or not re.fullmatch('[0-9a-f]{64}', expected[0]):
        raise ValueError('Missing or invalid release checksum')
    with urllib.request.urlopen(base + 'n-codex-accounts.tar.gz', timeout=20) as response:
        with archive.open('wb') as out:
            count = 0
            while chunk := response.read(65536):
                count += len(chunk)
                if count > 15 * 1024 * 1024: raise ValueError('Release exceeds size limit')
                out.write(chunk)
    if digest(archive) != expected[0]: raise ValueError('Release checksum mismatch')
    destination = directory / 'release'; destination.mkdir()
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        if sum(m.size for m in members) > 30 * 1024 * 1024: raise ValueError('Expanded release too large')
        for entry in members:
            path = Path(entry.name)
            if path.is_absolute() or '..' in path.parts or not (entry.isfile() or entry.isdir()):
                raise ValueError('Unsafe archive member')
        bundle.extractall(destination, filter='data')
    manifest = json.loads((destination / 'release.json').read_text())
    if manifest['version'] != tag.removeprefix('v'): raise ValueError('Release manifest version mismatch')
    actual = {str(p.relative_to(destination)) for p in destination.rglob('*') if p.is_file()}
    if actual != set(manifest['files']) | {'release.json'}: raise ValueError('Unexpected release files')
    for name, checksum in manifest['files'].items():
        if digest(destination / name) != checksum: raise ValueError(f'Payload checksum mismatch: {name}')
    return destination


def shell_block(text, begin, end):
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError('Missing or ambiguous managed shell block; manual review required')
    a, b = text.index(begin), text.index(end) + len(end)
    if a >= b: raise ValueError('Invalid managed block order')
    return text[a:b], text[:a] + text[b:]


def expand(value, user):
    value = value.replace('${HOME}', str(user)).replace('$HOME', str(user))
    if value.startswith('~/'): value = str(user / value[2:])
    if '$' in value or '`' in value or not Path(value).is_absolute():
        raise ValueError('Dynamic account paths require manual review')
    return Path(value)


def discover(user):
    shell = user / '.zshrc'
    text = shell.read_text() if shell.exists() else ''
    modern = user / '.config/n-codex-accounts/accounts.json'
    legacy = user / '.config/dual-codex/accounts.json'
    if modern.exists():
        block, remainder = shell_block(text, '# BEGIN N_CODEX_ACCOUNTS', '# END N_CODEX_ACCOUNTS')
        registry = json.loads(modern.read_text()); origin = 'public'
    elif legacy.exists():
        block, remainder = shell_block(text, '# BEGIN CODEX_MULTI_ACCOUNT_SWITCHER', '# END CODEX_MULTI_ACCOUNT_SWITCHER')
        registry = json.loads(legacy.read_text()); origin = 'legacy registry'
    else:
        block, remainder = shell_block(text, '# BEGIN CODEX_DUAL_ACCOUNT_SWITCHER', '# END CODEX_DUAL_ACCOUNT_SWITCHER')
        assignments = dict(re.findall(r'^export (CODEX_(?:PRIMARY_HOME|SECOND_HOME|SHARED_STATE))="([^"\n]+)"$', block, re.M))
        if set(assignments) != {'CODEX_PRIMARY_HOME', 'CODEX_SECOND_HOME', 'CODEX_SHARED_STATE'}:
            raise ValueError('Unrecognized legacy home declarations')
        primary = expand(assignments['CODEX_PRIMARY_HOME'], user)
        secondary = expand(assignments['CODEX_SECOND_HOME'], user)
        if primary.resolve() != (user / '.codex').resolve() or expand(assignments['CODEX_SHARED_STATE'], user).resolve() != primary.resolve():
            raise ValueError('Legacy setup does not use canonical shared SQLite; manual migration required')
        entries = {}
        pattern = r'^\s*([a-z0-9_|-]+)\)\s*\n\s*export CODEX_HOME="\$(CODEX_PRIMARY_HOME|CODEX_SECOND_HOME)"'
        for labels, variable in re.findall(pattern, block, re.M):
            for label in labels.split('|'):
                entries[label] = {'home': str(primary if variable == 'CODEX_PRIMARY_HOME' else secondary)}
        if not entries or {e['home'] for e in entries.values()} != {str(primary), str(secondary)}:
            raise ValueError('Could not map every legacy selector; manual review required')
        registry = {'version': 1, 'accounts': entries}; origin = 'legacy two-account'
    if registry.get('version') != 1 or not registry.get('accounts'): raise ValueError('Unknown registry schema')
    if re.search(r'\bcodex\s*\(|function\s+codex\b|alias\s+codex=|^\s*(?:source|\.)[^\n]*(?:codex|account)', remainder, re.M | re.I):
        raise ValueError('Another shell override exists outside the managed block')
    for name, entry in registry['accounts'].items():
        if not re.fullmatch('[a-z0-9][a-z0-9_-]*', name): raise ValueError('Invalid account name')
        home = Path(entry['home'])
        if not home.is_absolute() or home.is_symlink() or not home.resolve().is_relative_to(user.resolve()):
            raise ValueError('Account homes must be ordinary directories inside the user home')
        if not (home / 'auth.json').is_file() or (home / 'auth.json').is_symlink():
            raise ValueError(f'Missing or symlinked credential for {name}; no automatic repair')
    homes = sorted({Path(e['home']) for e in registry['accounts'].values()})
    canonical = user / '.codex'
    if canonical not in homes: raise ValueError('No registered canonical account')
    return {'origin': origin, 'registry': registry, 'remainder': remainder, 'homes': homes}


def fingerprint(paths):
    result = {}
    for root in paths:
        items = [root] + (list(root.rglob('*')) if root.is_dir() and not root.is_symlink() else [])
        for p in items:
            if p.is_symlink(): result[str(p)] = ['link', os.readlink(p)]
            elif p.is_file(): result[str(p)] = ['file', digest(p), p.stat().st_mode & 0o777]
            elif p.is_dir(): result[str(p)] = ['dir']
            else: result[str(p)] = ['absent']
    return result


def check_databases(homes, canonical):
    for home in homes:
        for db in home.glob('*.sqlite'):
            if home != canonical and db.name.startswith('state_') and db.resolve() != (canonical / db.name).resolve():
                raise ValueError(f'Separate account database: {db}; automatic DB merging is unsupported')
            with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError(f'Database integrity failure: {db}')


def merge_index(a, b):
    keys = {'attachmentPaths', 'pendingRemovalPaths', 'textExcerptsByPath'}
    if set(a) != keys or set(b) != keys: raise ValueError('Unknown attachment index schema')
    for x in (a, b):
        if not isinstance(x['textExcerptsByPath'], dict) or not all(isinstance(x[k], list) for k in ('attachmentPaths', 'pendingRemovalPaths')):
            raise ValueError('Invalid attachment index')
    excerpts = dict(a['textExcerptsByPath'])
    for path, text in b['textExcerptsByPath'].items():
        if path in excerpts and excerpts[path] != text: raise ValueError('Conflicting attachment index entry')
        excerpts[path] = text
    paths = list(dict.fromkeys(a['attachmentPaths'] + b['attachmentPaths']))
    if set(paths) != set(excerpts): raise ValueError('Attachment index is inconsistent')
    pending = [p for p in dict.fromkeys(a['pendingRemovalPaths'] + b['pendingRemovalPaths']) if p not in excerpts]
    return {'attachmentPaths': paths, 'pendingRemovalPaths': pending, 'textExcerptsByPath': excerpts}


def union(homes, stage):
    for part in PARTS:
        target = stage / part; target.mkdir(parents=True)
        if part == 'thread-writer-locks': continue  # Preserve canonical lock tree; never copy stale account locks.
        seen = set()
        for home in homes:
            source = home / part
            if source.resolve() in seen: continue
            seen.add(source.resolve())
            if not source.exists(): continue
            for item in source.rglob('*'):
                relative = item.relative_to(source); out = target / relative
                if item.is_symlink(): raise ValueError(f'Unexpected nested symlink: {item}')
                if item.is_dir(): out.mkdir(parents=True, exist_ok=True); continue
                if not item.is_file(): raise ValueError(f'Unsupported filesystem entry: {item}')
                out.parent.mkdir(parents=True, exist_ok=True)
                if out.exists() and digest(out) != digest(item):
                    if part == 'attachments' and str(relative) == 'pasted-text-attachments.json':
                        write(out, merge_index(json.loads(out.read_text()), json.loads(item.read_text())))
                    else: raise ValueError(f'Conflicting file: {relative}; nothing overwritten')
                elif not out.exists(): shutil.copy2(item, out)
    subprocess.run([sys.executable, str(SOURCE / 'validate_lineage.py'), str(stage / 'sessions'),
                    str(stage / 'archived_sessions')], check=True, stdout=subprocess.DEVNULL)


def plan(user, payload, stage):
    if (user/'.zshrc').is_symlink(): raise ValueError('Symlinked shell config requires manual review')
    found = discover(user); canonical = user / '.codex'
    homes = found['homes']; check_databases(homes, canonical)
    for home in homes:
        for part in PARTS:
            p = home / part
            if p.is_symlink() and (home == canonical or p.resolve() != (canonical / part).resolve()):
                raise ValueError(f'Unexpected top-level link: {p}')
            if p.exists() and not p.is_dir(): raise ValueError(f'Expected directory: {p}')
    changed_layout = any(home != canonical and not all((home/p).is_symlink() for p in PARTS) for home in homes)
    watched = [user / '.zshrc', user / '.config/n-codex-accounts/accounts.json',
               user / '.config/dual-codex/accounts.json']
    watched += [runtime(user)/p.name for p in (payload/'setup').iterdir() if p.suffix in ('.py', '.zsh')]
    watched += [runtime(user)/'runtime.zsh', runtime(user)/'VERSION']
    watched += [home/'auth.json' for home in homes] + [home/'config.toml' for home in homes]
    if changed_layout:
        watched += [home/p for home in homes for p in PARTS if p != 'thread-writer-locks']
    before = fingerprint(watched)
    if changed_layout: union(homes, stage)
    else:
        subprocess.run([sys.executable, str(SOURCE/'validate_lineage.py'), str(canonical/'sessions'), str(canonical/'archived_sessions')], check=True, stdout=subprocess.DEVNULL)
    if fingerprint(watched) != before: raise ValueError('Installation changed while planning; retry after work is idle')
    binary = os.environ.get('CODEX_REAL_BINARY') or shutil.which('codex')
    if not binary: raise ValueError('Real Codex executable is not available')
    binary = str(Path(binary).absolute())
    actual = subprocess.check_output([binary, '--version'], text=True).strip()
    allowed = json.loads((payload/'release.json').read_text())['cli_versions']
    if actual.removeprefix('codex-cli ') not in allowed: raise ValueError(f'CLI {actual} is not reviewed for this helper release')
    found.update({'watched': watched, 'fingerprint': before, 'layout': changed_layout, 'binary': binary})
    return found


def idle(user, homes):
    accounts = module(SOURCE / 'accounts.py'); accounts.CANONICAL = user / '.codex'
    relevant = {h.resolve() for h in homes}
    active = [pid for pid, _, home in accounts.process_snapshot() if home in relevant]
    if active: raise ValueError('Close affected Codex/editor/daemon processes first: ' + ', '.join(map(str, active)))


def managed_files(user, payload):
    root = runtime(user)
    names = {p.name for p in (payload/'setup').iterdir() if p.suffix in ('.py', '.zsh') and p.name != 'install.py'}
    return ([root/n for n in names] + [root/'runtime.zsh', root/'VERSION'] +
            [root/'bin'/n for n in ('accounts', 'preflight', 'run', 'guard', 'vscode-account', 'ncodex')] +
            [user/'.zshrc', user/'.config/n-codex-accounts/accounts.json'])


def restore(record):
    # Directory originals were renamed, never deleted. Never restore any auth or database file.
    for action in reversed(record['directories']):
        path, saved = Path(action['path']), Path(action['saved'])
        if saved.exists():
            if path.is_symlink(): path.unlink()
            elif path.exists():
                retained = saved.with_name(saved.name + '-failed-attempt')
                if retained.exists(): raise ValueError('Retained rollback path already exists')
                path.rename(retained)
            saved.rename(path)
        elif not action['existed'] and path.is_symlink(): path.unlink()
        elif not action['existed'] and path.exists(): path.rename(saved.with_name(saved.name + '-failed-attempt'))
    for entry in record['files']:
        path, saved = Path(entry['path']), Path(entry['saved'])
        if saved.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=path.parent); os.close(fd)
            try:
                shutil.copy2(saved, temporary); os.chmod(temporary, entry['mode']); os.replace(temporary, path)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
        elif not entry['existed'] and path.exists(): path.unlink()


def apply(user, payload, stage, proposed):
    root = runtime(user); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal = root/'update-journal.json'
    if journal.exists() and json.loads(journal.read_text())['status'] not in ('complete', 'rolled-back'):
        raise ValueError('Unfinished update exists; run ncodex rollback before another update')
    if proposed['layout'] or proposed['origin'] != 'public': idle(user, proposed['homes'])
    if fingerprint(proposed['watched']) != proposed['fingerprint']: raise ValueError('State changed since approval; re-plan')
    needed = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()) if proposed['layout'] else 0
    needed += sum(p.stat().st_size for p in (user/'.codex').glob('*.sqlite'))
    if shutil.disk_usage(user).free < needed + 10*1024*1024:
        raise ValueError('Insufficient free space for staged history and backups')
    installer = module(payload/'setup/install.py')
    backup = root/'update-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, mode=0o700)
    record = {'status': 'preparing', 'backup': str(backup), 'files': [], 'directories': [],
              'homes': [str(p) for p in proposed['homes']], 'layout': proposed['layout']}
    for index, path in enumerate(managed_files(user, payload)):
        if path.is_symlink(): raise ValueError(f'Symlinked managed file: {path}')
        saved = backup/f'file-{index}'
        if path.exists(): shutil.copy2(path, saved); os.chmod(saved, 0o600)
        record['files'].append({'path': str(path), 'saved': str(saved), 'existed': path.exists(), 'mode': path.stat().st_mode & 0o777 if path.exists() else 0o600})
    write(journal, record)
    canonical = user/'.codex'
    try:
        if proposed['layout']:
            # Database snapshots are evidence only: migrations do not replace or edit SQLite.
            for db in canonical.glob('*.sqlite'):
                with closing(sqlite3.connect(db.as_uri()+'?mode=ro', uri=True)) as source:
                    with closing(sqlite3.connect(backup/db.name)) as destination: source.backup(destination)
            if fingerprint(proposed['watched']) != proposed['fingerprint']: raise ValueError('State changed during backup')
            idle(user, proposed['homes'])
            for home in [canonical, *[h for h in proposed['homes'] if h != canonical]]:
                for part in PARTS:
                    path = home/part
                    if path.is_symlink() or (home == canonical and part == 'thread-writer-locks'): continue
                    saved = backup/f'directory-{len(record["directories"])}'
                    record['directories'].append({'path': str(path), 'saved': str(saved), 'existed': path.exists()})
                    write(journal, record)  # Durable intent before each rename.
                    if path.exists(): path.rename(saved)
                    if home == canonical: shutil.copytree(stage/part, path)
                    else: path.symlink_to(canonical/part)
        record['status'] = 'installing'; write(journal, record)
        if proposed['origin'] != 'public':
            (user/'.zshrc').write_text(proposed['remainder'])
            write(user/'.config/n-codex-accounts/accounts.json', proposed['registry'])
        installer.install(user, Path(proposed['binary']), login=False, managed_update=True)
        accounts = installer.load_accounts(user)
        if accounts.read_registry()['accounts'] != proposed['registry']['accounts']:
            raise ValueError('Account mappings changed unexpectedly')
        for home in proposed['homes']:
            module(payload/'setup/preflight.py').check(home.resolve(), canonical.resolve())
        if proposed['layout']:
            subprocess.run([sys.executable, str(payload/'setup/validate_lineage.py'),
                            str(canonical/'sessions'), str(canonical/'archived_sessions')], check=True, stdout=subprocess.DEVNULL)
        # Authentication files are never updater targets; verify their content remained unchanged.
        for home in proposed['homes']:
            p = home/'auth.json'
            if fingerprint([p]) != {str(p): proposed['fingerprint'][str(p)]}:
                raise ValueError('Credential changed concurrently; no credential will be restored by updater')
        record['status'] = 'complete'
        record['after'] = fingerprint([Path(e['path']) for e in record['files']])
        write(journal, record)
    except BaseException:
        # If a writer appeared, preserve every original and the journal rather than undoing live data.
        if proposed['layout']: idle(user, proposed['homes'])
        restore(record); record['status'] = 'rolled-back'; write(journal, record)
        raise
    return backup


def rollback(user):
    journal = runtime(user)/'update-journal.json'
    record = json.loads(journal.read_text())
    if record['status'] == 'rolled-back': raise ValueError('Already rolled back')
    idle(user, [Path(h) for h in record['homes']])
    if record['layout']:
        raise ValueError('History migration journal exists; automatic later rollback is blocked to protect new chats. Review retained originals and current history manually.')
    if record['status'] == 'complete' and fingerprint([Path(e['path']) for e in record['files']]) != record['after']:
        raise ValueError('Managed files changed after update; refusing to overwrite newer configuration')
    if input('Restore the saved helper/config files? [y/N]: ').strip().lower() != 'y': return
    restore(record); record['status'] = 'rolled-back'; write(journal, record)
    print('Rollback complete. Open a fresh terminal.')


@contextmanager
def lock(user, name):
    root = runtime(user); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root/name).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: yield
        finally: fcntl.flock(handle, fcntl.LOCK_UN)


def notice(user):
    journal = runtime(user)/'update-journal.json'
    if journal.exists():
        try:
            if json.loads(journal.read_text())['status'] not in ('complete', 'rolled-back'):
                print('An interrupted or active update needs recovery before launching.', file=sys.stderr)
                return 21
        except (OSError, ValueError, KeyError):
            print('Cannot read update journal; inspect it before launching.', file=sys.stderr)
            return 21
    if not sys.stdin.isatty() or not sys.stdout.isatty(): return 0
    try:
        with lock(user, 'notice.lock'):
            path = runtime(user)/'update-check.json'
            cached = json.loads(path.read_text()) if path.exists() else {}
            today = datetime.now().date().isoformat()
            if cached.get('date') == today: return 0
            write(path, {'date': today})  # Claims today's check even when offline or interrupted.
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '_latest'],
                                    capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL)
            if result.returncode: return 0
            release = json.loads(result.stdout)
            if version(release['tag']) <= version(VERSION): return 0
            print(f"n-Codex Accounts {release['tag']} is available. Run ncodex update anytime.")
            answer = input('Update now? [u = update / Enter = not now]: ').strip().lower()
            if answer == 'u':
                try:
                    completed = update(user, check=False, plan_only=False)
                    return 20 if completed else 0
                except (Exception, KeyboardInterrupt) as error:
                    report_error(user, error)
                    journal = runtime(user)/'update-journal.json'
                    if journal.exists() and json.loads(journal.read_text())['status'] not in ('complete', 'rolled-back'):
                        print('An interrupted update needs recovery before launching.', file=sys.stderr)
                        return 21
                    return 0
    except (Exception, KeyboardInterrupt):
        return 0  # A notice must never prevent normal launches.
    return 0


def concise(value, limit=160):
    value = ' '.join(str(value).split())
    return value if len(value) <= limit else value[:limit-1] + '…'


def show_details(proposed, manifest, *, paths=False):
    print('\nUpdate details')
    print('Accounts: ' + ', '.join(proposed['registry']['accounts']))
    print('History: merge validated copies; retain original folders.' if proposed['layout'] else
          'History: keep the existing shared store.')
    print('Credentials: preserved; no login or logout.')
    print('CLI: ' + ', '.join(manifest['cli_versions']) + ' supported; CLI and extension are not upgraded.')
    print('Launches: normal Codex approvals. Backups: ~/.local/share/n-codex-accounts/update-backups/')
    print('Wait until installation finishes before starting another account launch.')
    if paths:
        print('Detected setup: ' + proposed['origin'])
        for name, entry in proposed['registry']['accounts'].items(): print(f"  {name}: {entry['home']}")
    print()


def report_error(user, error):
    print('Update stopped: ' + concise(error, 240), file=sys.stderr)
    try:
        write(runtime(user)/'update-error.json', {'time': datetime.now().isoformat(), 'error': str(error)})
        print('Diagnostic detail: ~/.local/share/n-codex-accounts/update-error.json', file=sys.stderr)
    except OSError:
        pass


def update(user, *, check=False, plan_only=False, bundle=None):
    release = latest() if bundle is None else {'tag': 'v'+json.loads((bundle/'release.json').read_text())['version'], 'notes': 'Downloaded release verified by bootstrap.'}
    installed_path = runtime(user)/'VERSION'
    installed = installed_path.read_text().strip() if installed_path.exists() else 'legacy/unversioned'
    print(f"n-Codex Accounts {installed} → {release['tag'].removeprefix('v')}")
    if check: return False
    if installed != 'legacy/unversioned' and version(installed) >= version(release['tag']):
        print('No newer helper release.'); return False
    with tempfile.TemporaryDirectory(prefix='ncodex-update-') as directory:
        temporary = Path(directory)
        payload = bundle or download(release['tag'], temporary)
        manifest = json.loads((payload/'release.json').read_text())
        stage = temporary/'history'
        proposed = plan(user, payload, stage)
        summary = manifest.get('summary', [])
        if not isinstance(summary, list): summary = []
        for item in summary[:3]: print('• ' + concise(item))
        print('Updates helpers and shared history; preserves credentials.' if proposed['layout'] else
              'Updates account helpers; preserves credentials and history.')
        print('Close affected Codex, editor, and daemon processes before updating.' if proposed['layout'] or proposed['origin']!='public' else
              'Existing chats may stay open. Wait before starting another account launch.')
        if proposed['origin'] != 'public': print('Migrated launches use normal Codex approvals.')
        if plan_only:
            show_details(proposed, manifest, paths=True)
            return False
        while True:
            answer = input('Apply update? [y/N] · d = details: ').strip().lower()
            if answer == 'd':
                show_details(proposed, manifest)
                continue
            if answer in ('y', 'yes'): break
            if answer in ('', 'n', 'no'):
                print('Not now. No installation changes applied.'); return False
            print('Enter y, n, or d.')
        with lock(user, 'update.lock'):
            accounts = module(SOURCE/'install.py').load_accounts(user) if (SOURCE/'install.py').exists() else module(SOURCE/'accounts.py')
            with accounts.registry_lock(): backup = apply(user, payload, stage, proposed)
        print(f"Updated to {manifest['version']}. Checks passed.\nOpen a fresh terminal or run: source ~/.zshrc")
        return True


def main():
    parser = argparse.ArgumentParser(prog='ncodex')
    parser.add_argument('command', choices=['update', 'doctor', 'rollback', 'notice', '_latest'])
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--bundle', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(); user = Path.home()
    try:
        if args.command == '_latest': print(json.dumps(latest()))
        elif args.command == 'notice': return notice(user)
        elif args.command == 'rollback':
            with lock(user, 'update.lock'): rollback(user)
        elif args.command == 'doctor':
            found = discover(user); check_databases(found['homes'], user/'.codex')
            for home in found['homes']: module(SOURCE/'preflight.py').check(home.resolve(), (user/'.codex').resolve())
            subprocess.run([sys.executable, str(SOURCE/'validate_lineage.py'), str(user/'.codex/sessions'), str(user/'.codex/archived_sessions')], check=True)
            print('Layout and SQLite checks passed. No live authentication test performed.')
        else: update(user, check=args.check, plan_only=args.plan, bundle=args.bundle)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        report_error(user, error); return 1


if __name__ == '__main__': raise SystemExit(main())
