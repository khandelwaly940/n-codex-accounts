#!/bin/zsh
# Bootstrap only this public release; browser prompts read from the terminal.
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
curl --fail --silent --show-error --location \
  https://github.com/khandelwaly940/n-codex-accounts/archive/refs/tags/v0.2.0.tar.gz \
  --output "$task_dir/release.tar.gz"
tar -xzf "$task_dir/release.tar.gz" -C "$task_dir"
if [[ -t 0 || " $* " == *" --skip-login "* ]]; then
  python3 "$task_dir/n-codex-accounts-0.2.0/setup/install.py" "$@"
else
  python3 "$task_dir/n-codex-accounts-0.2.0/setup/install.py" "$@" </dev/tty
fi
