#!/bin/zsh
# Install the latest published public release; browser prompts read from the terminal.
set -euo pipefail
[[ "$(uname -s)" == Darwin ]] || { print -u2 'This installer supports macOS with zsh only.'; exit 1; }
command -v python3 >/dev/null || { print -u2 'Install Python 3.11+ first.'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
root="${0:A:h}"
if [[ -f "$root/setup/install.py" ]]; then
  exec python3 "$root/setup/install.py" "$@"
fi
task_dir="$(mktemp -d -t n-codex-install)"
trap 'rm -rf "$task_dir"' EXIT INT TERM HUP
release_url="https://github.com/khandelwaly940/n-codex-accounts/releases/latest/download"
curl --fail --silent --show-error --location "$release_url/SHA256SUMS" \
  --output "$task_dir/SHA256SUMS"
curl --fail --silent --show-error --location "$release_url/n-codex-accounts.tar.gz" \
  --output "$task_dir/n-codex-accounts.tar.gz"
python3 - "$task_dir" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile

root = Path(sys.argv[1])
archive = root / 'n-codex-accounts.tar.gz'
checksums = [line.split()[0] for line in (root / 'SHA256SUMS').read_text().splitlines()
             if line.split()[-1:] == [archive.name]]
if len(checksums) != 1 or not re.fullmatch(r'[0-9a-f]{64}', checksums[0]):
    raise SystemExit('Invalid release checksum file')
if archive.stat().st_size > 15 * 1024 * 1024 or hashlib.sha256(archive.read_bytes()).hexdigest() != checksums[0]:
    raise SystemExit('Release download exceeds the size limit or checksum differs')
with tarfile.open(archive, 'r:gz') as bundle:
    members = bundle.getmembers()
    if sum(member.size for member in members) > 30 * 1024 * 1024:
        raise SystemExit('Expanded release exceeds the size limit')
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
            raise SystemExit('Unsafe release archive')
    bundle.extractall(root / 'release', filter='data')
payload = root / 'release'
manifest = json.loads((payload / 'release.json').read_text())
if not re.fullmatch(r'\d+\.\d+\.\d+', manifest['version']):
    raise SystemExit('Invalid release version')
expected = manifest['files']
if {str(path.relative_to(payload)) for path in payload.rglob('*') if path.is_file()} != set(expected) | {'release.json'}:
    raise SystemExit('Unexpected release files')
for name, checksum in expected.items():
    path = payload / name
    if (Path(name).is_absolute() or '..' in Path(name).parts or
            not re.fullmatch(r'[0-9a-f]{64}', checksum) or
            hashlib.sha256(path.read_bytes()).hexdigest() != checksum):
        raise SystemExit('Release payload checksum differs')
PY
if [[ -t 0 || " $* " == *" --skip-login "* ]]; then
  python3 "$task_dir/release/setup/install.py" "$@"
else
  python3 "$task_dir/release/setup/install.py" "$@" </dev/tty
fi
