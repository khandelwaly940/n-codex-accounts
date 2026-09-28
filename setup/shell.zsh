# Managed Codex multi-account shell integration.
source "$HOME/.local/share/n-codex-accounts/runtime.zsh"
export CODEX_REAL="$HOME/.local/share/n-codex-accounts/bin/run"
export CODEX_ACCOUNTS="$HOME/.local/share/n-codex-accounts/bin/accounts"

ncodex() {
  "$HOME/.local/share/n-codex-accounts/bin/ncodex" "$@"
}

_ncodex_update_notice() {
  local tool="$HOME/.local/share/n-codex-accounts/bin/ncodex"
  [[ -x "$tool" ]] || return 0
  "$tool" notice
  local result=$?
  (( result == 21 )) && return 1
  if (( result == 20 )); then
    source "$HOME/.local/share/n-codex-accounts/shell.zsh"
  fi
  return 0
}

_ncodex_offer_launch() {
  local reply
  printf "Open Codex now? [y/N]: "
  read -r reply
  case "${reply:l}" in
    y|yes)
      env CODEX_HOME="$CODEX_HOME" \
          CODEX_SQLITE_HOME="$CODEX_SQLITE_HOME" \
          "$CODEX_REAL"
      return $?
      ;;
    *)
      echo "Account selected; Codex was not opened."
      return 0
      ;;
  esac
}

codex() {
  local command="${1:-}"
  case "$command" in
    add)
      shift
      "$CODEX_ACCOUNTS" add "$@"
      return $?
      ;;
    accounts)
      "$CODEX_ACCOUNTS" list
      return $?
      ;;
    processes)
      shift
      "$CODEX_ACCOUNTS" processes "$@"
      return $?
      ;;
    status)
      local current_home="${CODEX_HOME:-$HOME/.codex}"
      local current_name
      current_name="$("$CODEX_ACCOUNTS" identify "$current_home" 2>/dev/null || true)"
      echo "CODEX_ACCOUNT=${current_name:-unregistered}"
      echo "CODEX_HOME=$current_home"
      echo "CODEX_SQLITE_HOME=${CODEX_SQLITE_HOME:-$HOME/.codex}"
      return 0
      ;;
  esac

  local selected_home
  selected_home="$("$CODEX_ACCOUNTS" resolve "$command" 2>/dev/null || true)"
  if [[ -n "$command" && -n "$selected_home" ]]; then
    if [[ "${2:-}" == relogin ]]; then
      if (( $# != 2 )); then
        print -u2 -- "Usage: codex $command relogin"
        return 2
      fi
      "$CODEX_ACCOUNTS" relogin "$command"
      return $?
    fi
    if (( $# != 1 )); then
      print -u2 -- "Usage: codex $command"
      return 2
    fi
    _ncodex_update_notice || return $?
    export CODEX_HOME="$selected_home"
    export CODEX_SQLITE_HOME="$HOME/.codex"
    echo "Codex account selected: ${command:u}"
    echo "Auth:   $CODEX_HOME/auth.json"
    echo "State:  $CODEX_SQLITE_HOME"
    _ncodex_offer_launch
    return $?
  fi

  env CODEX_HOME="${CODEX_HOME:-$HOME/.codex}" \
      CODEX_SQLITE_HOME="$HOME/.codex" \
      "$CODEX_REAL" "$@"
}


vscode-account() {
  "$HOME/.local/share/n-codex-accounts/bin/vscode-account" "$@"
}
