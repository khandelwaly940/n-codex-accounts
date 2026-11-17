#!/usr/bin/env python3
"""One-time macOS updater bootstrap; changes require the downloaded planner's approval."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import urllib.error


def latest_tag():
    base = 'https://github.com/khandelwaly940/n-codex-accounts/releases/'
    request = urllib.request.Request(base + 'latest', method='HEAD',
                                     headers={'User-Agent': 'n-codex-bootstrap'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            location = response.geturl()
    except urllib.error.HTTPError as error:
        error.close()
        if error.code in (403, 429):
            raise ValueError(f'GitHub temporarily blocked the release check (HTTP {error.code}). Retry later; setup unchanged.') from error
        raise
    match = re.fullmatch(re.escape(base + 'tag/') + r'(v\d+\.\d+\.\d+)', location)
    if not match: raise ValueError('GitHub did not resolve a stable release for this repository')
    return match.group(1)


def main():
    if sys.platform != 'darwin' or sys.version_info < (3, 11):
        raise ValueError('Requires macOS and Python 3.11+. Run: python3 ncodex-bootstrap.command')
    tag = latest_tag()
    url = f'https://github.com/khandelwaly940/n-codex-accounts/releases/download/{tag}/'
    with urllib.request.urlopen(url+'SHA256SUMS', timeout=10) as r:
        lines = r.read(16384).decode().splitlines()
    checksums = [line.split()[0] for line in lines if line.split()[-1:] == ['n-codex-accounts.tar.gz']]
    if len(checksums) != 1 or not re.fullmatch('[0-9a-f]{64}', checksums[0]): raise ValueError('Invalid checksum file')
    with urllib.request.urlopen(url+'n-codex-accounts.tar.gz', timeout=20) as r:
        data = r.read(15*1024*1024+1)
    if len(data)>15*1024*1024 or hashlib.sha256(data).hexdigest()!=checksums[0]: raise ValueError('Release checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='ncodex-bootstrap-') as directory:
        root = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            members = archive.getmembers()
            if sum(m.size for m in members)>30*1024*1024: raise ValueError('Archive too large')
            for member in members:
                p = Path(member.name)
                if p.is_absolute() or '..' in p.parts or not (member.isfile() or member.isdir()): raise ValueError('Unsafe archive')
            archive.extractall(root, filter='data')
        manifest = json.loads((root/'release.json').read_text())
        if manifest['version'] != tag[1:]: raise ValueError('Version mismatch')
        if {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()} != set(manifest['files'])|{'release.json'}:
            raise ValueError('Unexpected release files')
        for name, checksum in manifest['files'].items():
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=checksum: raise ValueError('Payload checksum mismatch')
        if sys.argv[1:] not in ([], ['--plan'], ['--rollback']): raise ValueError('Usage: python3 ncodex-bootstrap.command [--plan|--rollback]')
        command = 'rollback' if '--rollback' in sys.argv else 'update'
        flags = ['--plan'] if '--plan' in sys.argv else []
        return subprocess.call([sys.executable, str(root/'setup/updater.py'), command, '--bundle', str(root), *flags])


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(f'Bootstrap stopped: {error}', file=sys.stderr); raise SystemExit(1)
