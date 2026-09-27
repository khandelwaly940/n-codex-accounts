#!/bin/zsh
set -euo pipefail
root="$HOME/.local/share/n-codex-accounts"
source "$root/runtime.zsh"
[[ $# -ge 1 ]] || { print -u2 'Usage: vscode-account <name> [folder]'; exit 2; }
account="$1"
shift
selected_home="$("$root/bin/accounts" resolve "$account")"
electron="/Applications/Visual Studio Code.app/Contents/MacOS/Code"
[[ -x "$electron" ]] || { print -u2 'Install Visual Studio Code in /Applications first.'; exit 1; }
if pgrep -f '^/Applications/Visual Studio Code.app/Contents/MacOS/Code( |$)' >/dev/null; then
  print -u2 'Fully quit VS Code before selecting an account.'
  exit 1
fi
"$root/bin/preflight" "$selected_home"
"$root/bin/guard" acquire "$account" "$$" vscode
log_dir="$root/logs"
mkdir -p "$log_dir"
chmod 700 "$log_dir"
# The private log directory avoids predictable shared /tmp output paths.
umask 077
nohup env CODEX_HOME="$selected_home" CODEX_SQLITE_HOME="$HOME/.codex" \
  "$electron" --new-window "$@" >"$log_dir/vscode-$account.log" 2>&1 </dev/null &
app_pid="$!"
"$root/bin/guard" transfer "$account" "$$" "$app_pid"
( while kill -0 "$app_pid" 2>/dev/null; do sleep 5; done; "$root/bin/guard" release "$account" "$app_pid" ) \
  >/dev/null 2>&1 </dev/null &!
print "VS Code launched using account: $account"
