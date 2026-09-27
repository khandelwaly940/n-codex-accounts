#!/usr/bin/env python3
"""Validate Codex paginated rollout ancestry without reading credentials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def rollout_files(roots: list[Path]):
    for root in roots:
        if root.exists():
            yield from root.rglob("*.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()

    sessions: dict[str, tuple[Path, dict]] = {}
    errors: list[str] = []

    for path in rollout_files(args.roots):
        try:
            with path.open("r", encoding="utf-8") as handle:
                record = json.loads(handle.readline())
            meta = record.get("payload", {})
            session_id = meta.get("id") or meta.get("session_id")
            if record.get("type") != "session_meta" or not session_id:
                errors.append(f"invalid session metadata: {path}")
                continue
            previous = sessions.get(session_id)
            if previous and previous[0].resolve() != path.resolve():
                errors.append(f"duplicate session id {session_id}: {previous[0]} and {path}")
                continue
            sessions[session_id] = (path, meta)
        except Exception as exc:
            errors.append(f"cannot read {path}: {exc}")

    for session_id, (path, meta) in sessions.items():
        parent = meta.get("forked_from_id")
        if meta.get("history_mode") == "paginated" and parent and parent not in sessions:
            errors.append(f"{session_id}: missing source rollout {parent} (from {path})")

    for start in sessions:
        seen: set[str] = set()
        current = start
        while current in sessions:
            if current in seen:
                errors.append(f"cycle in rollout ancestry at {current} (from {start})")
                break
            seen.add(current)
            current = sessions[current][1].get("forked_from_id")
            if not current:
                break

    if errors:
        print("Codex rollout lineage validation FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    paginated = sum(1 for _, meta in sessions.values() if meta.get("history_mode") == "paginated")
    print(f"Codex rollout lineage validation passed: {len(sessions)} sessions, {paginated} paginated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
