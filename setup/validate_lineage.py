#!/usr/bin/env python3
"""Validate physical Codex rollout ancestry, including same-chat continuations."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from collections import defaultdict
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

try:
    from compression.zstd import open as zstd_open
except ImportError:  # Python 3.11-3.13 can use the optional zstd executable.
    zstd_open = None

UUID = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'


def logical_path(path):
    return path.with_suffix('') if path.name.endswith('.jsonl.zst') else path


@contextmanager
def rollout_reader(path):
    plain = logical_path(path)
    compressed = plain.with_suffix('.jsonl.zst')
    # Codex may compress or restore a rollout between discovery and opening it.
    for attempt in range(3):
        selected = plain if plain.exists() else compressed
        try:
            handle = selected.open('rb')
            break
        except FileNotFoundError:
            if attempt == 2: raise
    if selected == plain:
        with handle: yield handle, False
        return
    handle.close()
    if zstd_open is not None:
        with zstd_open(selected, 'rb') as decoded: yield decoded, True
        return
    executable = shutil.which('zstd')
    if not executable:
        raise ValueError('Compressed history requires Python 3.14+ with zstd support or the zstd command (brew install zstd)')
    process = subprocess.Popen([executable, '-q', '-d', '-c', '--', str(selected)],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        yield process.stdout, True
    finally:
        process.stdout.close()
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()


def physical_id(path, session_id):
    path = logical_path(path)
    match = re.search(r'(' + UUID + r')(?:_(' + UUID + r'))?$', path.stem)
    return (match.group(2) or match.group(1)) if match else session_id


def prefix_record(path, end):
    with rollout_reader(path) as (handle, compressed):
        if compressed:
            # Offsets refer to JSONL bytes, not compressed file sizes. Keep only
            # the final record in memory, without materializing history on disk.
            remaining = end; last = b''
            while remaining > 0:
                block = handle.read(min(remaining, 65536))
                if not block: raise ValueError('history prefix extends outside source rollout')
                remaining -= len(block)
                if remaining == 0:
                    if not block.endswith(b'\n'): raise ValueError('history prefix does not end at a JSONL boundary')
                    block = block[:-1]
                last = (last + block).rsplit(b'\n', 1)[-1]
            return json.loads(last)
        if end < 1 or end > handle.seek(0, 2):
            raise ValueError('history prefix extends outside source rollout')
        handle.seek(end - 1)
        if handle.read(1) != b'\n': raise ValueError('history prefix does not end at a JSONL boundary')
        cursor = end - 1
        chunks = []
        while cursor:
            start = max(0, cursor - 65536)
            handle.seek(start); block = handle.read(cursor - start)
            index = block.rfind(b'\n')
            if index >= 0:
                chunks.append(block[index+1:]); break
            chunks.append(block); cursor = start
        return json.loads(b''.join(reversed(chunks)))


def validate(roots):
    nodes = {}; groups = defaultdict(list); errors = []
    paths = {logical_path(p.resolve()) for root in roots if root.exists()
             for pattern in ('*.jsonl', '*.jsonl.zst') for p in root.rglob(pattern)}
    for path in sorted(paths):
        try:
            with rollout_reader(path) as (handle, _): record = json.loads(handle.readline())
            meta = record.get('payload', {})
            session = meta.get('id') or meta.get('session_id')
            if record.get('type') != 'session_meta' or not isinstance(session, str) or not session:
                raise ValueError('invalid session metadata')
            node = physical_id(path, session)
            if node in nodes:
                raise ValueError(f'duplicate physical rollout id {node}: {nodes[node][0]} and {path}')
            nodes[node] = (path, meta, record.get('ordinal'))
            groups[session].append(node)
        except Exception as exc: errors.append(f'cannot validate {path}: {exc}')

    parents = {}
    for node, (path, meta, ordinal) in nodes.items():
        if meta.get('history_mode') != 'paginated': continue
        base = meta.get('history_base')
        try:
            if base is not None:
                if not isinstance(base, dict) or not isinstance(base.get('thread_id'), str):
                    raise ValueError('invalid history_base')
                parent = base['thread_id']
            else: parent = meta.get('forked_from_id')
            if not parent: continue
            parents[node] = parent
            if parent not in nodes: raise ValueError(f'missing source rollout {parent}')
            if base is not None:
                end = base.get('end_byte_offset'); cutoff = base.get('end_ordinal_exclusive')
                if type(end) is not int or type(cutoff) is not int or end < 1 or cutoff < 1:
                    raise ValueError('invalid history prefix position')
                last = prefix_record(nodes[parent][0], end)
                if last.get('ordinal') != cutoff - 1:
                    raise ValueError('history prefix ordinal does not match source rollout')
                if ordinal != cutoff:
                    raise ValueError('continuation ordinal does not match history prefix')
        except Exception as exc: errors.append(f'{node}: {exc} (from {path})')

    for session, members in groups.items():
        if len(members) > 1:
            origins = [node for node in members if parents.get(node) not in members]
            if len(origins) != 1:
                errors.append(f'duplicate session id {session}: unrelated rollout files; expected one connected continuation history')
    for start in nodes:
        seen = set(); current = start
        while current in nodes:
            if current in seen:
                errors.append(f'cycle in rollout ancestry at {current} (from {start})'); break
            seen.add(current); current = parents.get(current)
            if not current: break
    return nodes, groups, errors


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('roots', nargs='+', type=Path)
    args = parser.parse_args(); nodes, groups, errors = validate(args.roots)
    if errors:
        print('Codex rollout lineage validation FAILED', file=sys.stderr)
        for error in errors: print('- ' + error, file=sys.stderr)
        return 1
    paginated = sum(meta.get('history_mode') == 'paginated' for _, meta, _ in nodes.values())
    print(f'Codex rollout lineage validation passed: {len(groups)} sessions, {len(nodes)} rollouts, {paginated} paginated')
    return 0


if __name__ == '__main__': raise SystemExit(main())
