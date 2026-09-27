#!/bin/zsh
# Track n-Codex launcher processes without blocking mixed-account use.
# Exact-thread concurrency is coordinated by Codex's shared thread-writer-locks.
set -euo pipefail

action="${1:-}"
account="${2:-}"
pid="${3:-}"
extra="${4:-}"
guard="$HOME/.codex/thread-writer-locks/n-codex-guard"
mutex="$guard/mutex"
pids="$guard/pids"
mkdir -p "$pids"

accounts="$HOME/.local/share/n-codex-accounts/bin/accounts"
[[ -x "$accounts" ]] || { print -u2 -- "ERROR: Codex account registry tool is missing."; exit 1; }
registered_home="$("$accounts" resolve "$account" 2>/dev/null || true)"
[[ -n "$registered_home" ]] || { print -u2 -- "ERROR: invalid Codex account: $account"; exit 2; }

mutex_acquired=0
for _ in {1..100}; do
  if mkdir "$mutex" 2>/dev/null; then
    mutex_acquired=1
    break
  fi
  sleep 0.05
done
(( mutex_acquired )) || { print -u2 -- "ERROR: could not acquire the n-Codex account guard."; exit 1; }
cleanup_mutex() { rmdir "$mutex" 2>/dev/null || true; }
trap cleanup_mutex EXIT INT TERM HUP

for marker in "$pids"/*(N); do
  marker_pid="${marker:t}"
  if [[ "$marker_pid" != <-> ]] || ! kill -0 "$marker_pid" 2>/dev/null; then
    rm -f "$marker"
  fi
done
markers=("$pids"/*(N))

case "$action" in
  check)
    # Shared layout and lineage checks are performed by the preflight helper.
    # Different accounts may be active concurrently.
    ;;
  acquire)
    [[ "$pid" == <-> ]] || { print -u2 -- "ERROR: invalid guard PID."; exit 2; }
    print -r -- "$account:${extra:-codex}" > "$pids/$pid"
    ;;
  transfer)
    [[ "$pid" == <-> && "$extra" == <-> ]] || { print -u2 -- "ERROR: invalid guard transfer."; exit 2; }
    [[ -f "$pids/$pid" ]] || { print -u2 -- "ERROR: guard owner disappeared before transfer."; exit 1; }
    label="$(cat "$pids/$pid")"
    rm -f "$pids/$pid"
    print -r -- "$label" > "$pids/$extra"
    ;;
  release)
    [[ "$pid" == <-> ]] || exit 0
    rm -f "$pids/$pid"
    ;;
  *)
    print -u2 -- "Usage: n-codex-guard check|acquire|transfer|release account pid [label-or-new-pid]"
    exit 2
    ;;
esac
