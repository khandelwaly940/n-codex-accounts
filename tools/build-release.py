#!/usr/bin/env python3
"""Build only allowlisted public files into a checksummed GitHub asset."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
files = [ROOT/n for n in ('README.md', 'LICENSE', 'install.sh', '.gitignore')]
files += sorted((ROOT/'setup').glob('*.py')) + sorted((ROOT/'setup').glob('*.zsh'))
assert all(p.is_file() and not p.is_symlink() for p in files)
manifest = {'version': '0.2.3', 'cli_versions': ['0.158.0', '0.159.0', '0.159.2'],
            'summary': ['Recognizes valid same-chat rollout continuations.',
                        'Supports Codex CLI 0.159.2; earlier reviewed versions still work.'],
            'files': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
archive = out/'n-codex-accounts.tar.gz'
with tarfile.open(archive, 'w:gz') as bundle:
    for p in files: bundle.add(p, arcname=str(p.relative_to(ROOT)))
    data = json.dumps(manifest, indent=2).encode(); info = tarfile.TarInfo('release.json')
    info.size = len(data); info.mode = 0o644; bundle.addfile(info, io.BytesIO(data))
helper = ROOT/'ncodex-bootstrap.command'
(out/helper.name).write_bytes(helper.read_bytes()); (out/helper.name).chmod(0o755)
(out/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
                                   for p in (archive, out/helper.name)))
print(out)
