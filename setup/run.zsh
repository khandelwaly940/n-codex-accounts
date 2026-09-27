#!/bin/zsh
# Launch Codex with validated shared history and isolated account authentication.
set -euo pipefail

preflight="$HOME/.local/share/n-codex-accounts/bin/preflight"
guard="$HOME/.local/share/n-codex-accounts/bin/guard"
real_codex="${CODEX_REAL_BINARY:?Source the installed n-Codex shell integration first}"
selected_home="${CODEX_HOME:-$HOME/.codex}"
accounts="$HOME/.local/share/n-codex-accounts/bin/accounts"
[[ -x "$accounts" ]] || { print -u2 -- "ERROR: Codex account registry tool is missing."; exit 1; }
account="$("$accounts" identify "$selected_home")" || exit 1

[[ -x "$preflight" ]] || { print -u2 -- "ERROR: n-Codex preflight is missing."; exit 1; }
[[ -x "$real_codex" ]] || { print -u2 -- "ERROR: Codex binary is missing: $real_codex"; exit 1; }
"$preflight" "$selected_home"
"$guard" acquire "$account" "$$" cli
cleanup() { "$guard" release "$account" "$$" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM HUP

env CODEX_HOME="$selected_home" \
    CODEX_SQLITE_HOME="$HOME/.codex" \
    "$real_codex" "$@"
