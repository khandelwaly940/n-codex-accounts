#!/usr/bin/env python3
"""Manage isolated Codex account homes without copying credentials between accounts."""

from __future__ import annotations

import argparse
import base64
import fcntl
from contextlib import contextmanager
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


USER_ROOT = Path.home()
CANONICAL = USER_ROOT / ".codex"
REGISTRY_DIR = USER_ROOT / ".config" / "n-codex-accounts"
REGISTRY = REGISTRY_DIR / "accounts.json"
BACKUP_ROOT = USER_ROOT / ".local" / "share" / "n-codex-accounts" / "auth-backups"
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RESERVED = {
    "add", "accounts", "status", "help", "login", "logout", "resume", "fork",
    "exec", "review", "mcp", "plugin", "app", "agents", "queue", "archive",
    "delete", "unarchive", "cloud", "doctor", "debug", "apply", "features",
    "processes", "health", "update", "completion", "sandbox", "app-server",
    "exec-server", "remote-control", "migrate-rollouts",
}


def fail(message: str, code: int = 1) -> "NoReturn":
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


@contextmanager
def registry_lock():
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (REGISTRY_DIR / "mutation.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another account setup/recovery is in progress; retry when it finishes")
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_registry() -> dict:
    if not REGISTRY.exists():
        return {"version": 1, "accounts": {}}
    try:
        value = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot read account registry {REGISTRY}: {exc}")
    if value.get("version") != 1 or not isinstance(value.get("accounts"), dict):
        fail(f"unsupported account registry format: {REGISTRY}")
    return value


def write_registry(value: dict) -> None:
    ordered = {
        "version": 1,
        "accounts": {name: value["accounts"][name] for name in sorted(value["accounts"])},
    }
    atomic_write(REGISTRY, json.dumps(ordered, indent=2) + "\n")


def ensure_core() -> dict:
    value = read_registry()
    current = any(Path(entry["home"]).resolve() == CANONICAL.resolve()
                  for entry in value["accounts"].values())
    if not current:
        if not (CANONICAL / "auth.json").is_file():
            fail("primary credential is missing; run the installer first")
        value["accounts"]["primary"] = {"home": str(CANONICAL)}
        write_registry(value)
    return value


def validate_label(label: str, *, allow_existing: bool = True) -> str:
    label = label.lower()
    if not LABEL_RE.fullmatch(label):
        fail("account names must use lowercase letters, numbers, underscores, or hyphens")
    if label in RESERVED:
        fail(f"'{label}' is reserved by the Codex CLI")
    if not allow_existing and label in ensure_core()["accounts"]:
        fail(f"account '{label}' is already registered")
    return label


def account_home(label: str) -> Path:
    label = validate_label(label)
    entry = ensure_core()["accounts"].get(label)
    if not entry:
        fail(f"unknown Codex account '{label}'. Run: codex add {label}", 2)
    return Path(entry["home"])


def toml_sections(text: str) -> list[tuple[str | None, list[str]]]:
    header = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*(?:#.*)?$")
    lines = text.splitlines(keepends=True)
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = header.match(line.rstrip("\r\n"))
        if match:
            starts.append((index, match.group(1).strip()))
    first = starts[0][0] if starts else len(lines)
    result: list[tuple[str | None, list[str]]] = [(None, lines[:first])]
    for position, (start, name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        result.append((name, lines[start:end]))
    return result


def sync_mcp_config(destination: Path) -> None:
    import tomllib

    source = CANONICAL / "config.toml"
    if not source.is_file():
        return
    source_text = source.read_text(encoding="utf-8")
    source_value = tomllib.loads(source_text)
    source_mcp = source_value.get("mcp_servers", {})
    mcp_blocks = [
        lines for name, lines in toml_sections(source_text)
        if name == "mcp_servers" or (name and name.startswith("mcp_servers."))
    ]
    base_text = destination.read_text(encoding="utf-8") if destination.exists() else ""
    kept = [
        lines for name, lines in toml_sections(base_text)
        if not (name == "mcp_servers" or (name and name.startswith("mcp_servers.")))
    ]
    output = "".join("".join(lines) for lines in kept).rstrip()
    if mcp_blocks:
        output += "\n\n" + "".join("".join(lines) for lines in mcp_blocks).lstrip()
    output = output.rstrip() + "\n"
    parsed = tomllib.loads(output)
    if parsed.get("mcp_servers", {}) != source_mcp:
        fail("generated MCP configuration does not match the canonical account")
    atomic_write(destination, output)


def login_into(staging_home: Path) -> Path:
    real_codex = os.environ.get("CODEX_REAL_BINARY") or shutil.which("codex") or ""
    if not os.access(real_codex, os.X_OK):
        fail(f"Codex binary is missing or not executable: {real_codex}")
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(staging_home)
    environment["CODEX_SQLITE_HOME"] = str(staging_home)
    # The interactive browser flow must not inherit another authentication method.
    for key in ("CODEX_API_KEY", "OPENAI_API_KEY", "CODEX_ACCESS_TOKEN",
                "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE", "CODEX_REMOTE_AUTH_TOKEN"):
        environment.pop(key, None)
    result = subprocess.run(
        [real_codex, "-c", "cli_auth_credentials_store=file", "login"],
        env=environment,
        check=False,
    )
    auth = staging_home / "auth.json"
    if result.returncode != 0 or not auth.is_file():
        fail("Codex login did not complete; the existing account registry and credentials were unchanged")
    try:
        parsed = json.loads(auth.read_text(encoding="utf-8"))
    except Exception:
        fail("Codex login returned an invalid credential file; no existing credential was changed")
    if not isinstance(parsed, dict) or not parsed:
        fail("Codex login returned an empty credential file; no existing credential was changed")
    os.chmod(auth, 0o600)
    return auth


def create_staging(label: str) -> Path:
    root = USER_ROOT / ".local" / "share" / "n-codex-accounts" / "staging"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    path = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=root))
    os.chmod(path, 0o700)
    return path


def confirm(prompt: str) -> None:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        print("Cancelled. No login or credential change was performed.")
        raise SystemExit(0)


def install_shared_links(target: Path) -> None:
    for name in ("sessions", "archived_sessions", "attachments", "thread-writer-locks"):
        source = CANONICAL / name
        destination = target / name
        if not source.exists():
            fail(f"canonical shared path is missing: {source}")
        if destination.exists() or destination.is_symlink():
            fail(f"new account path unexpectedly exists: {destination}")
        destination.symlink_to(source)


def add_account(label: str) -> None:
    label = validate_label(label, allow_existing=False)
    target = USER_ROOT / ".codex-accounts" / label
    if target.exists() or target.is_symlink():
        fail(f"refusing to overwrite existing path: {target}")
    confirm(f"Open browser login for new Codex account '{label}'? [y/N]: ")
    staging = create_staging(label)
    created_target = False
    try:
        staged_auth = login_into(staging)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.mkdir(mode=0o700)
        created_target = True
        shutil.copy2(staged_auth, target / "auth.json")
        os.chmod(target / "auth.json", 0o600)
        staged_config = staging / "config.toml"
        if staged_config.is_file():
            shutil.copy2(staged_config, target / "config.toml")
        sync_mcp_config(target / "config.toml")
        install_shared_links(target)
        value = ensure_core()
        value["accounts"][label] = {"home": str(target)}
        write_registry(value)
    except BaseException:
        if created_target:
            shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"Codex account '{label}' is ready.")
    print(f"Use: codex {label}")
    print(f"VS Code: vscode-account {label}")


def process_home(environment: str) -> Path:
    """Read a complete environment value, never a prefix of another home."""
    match = re.search(
        r"(?:^|\s)CODEX_HOME=(.*?)(?=\s+[A-Za-z_][A-Za-z0-9_]*=|$)",
        environment.strip(),
    )
    return Path(match.group(1)).expanduser().resolve() if match else CANONICAL.resolve()


def is_codex_process(executable: str) -> bool:
    path = Path(executable)
    if path.name in {"codex", "codex-code-mode-host"}:
        return True
    return executable.endswith((
        "/Visual Studio Code.app/Contents/MacOS/Code",
    ))


def process_snapshot() -> list[tuple[int, str, Path]]:
    """Read process metadata only; never print the environment (it may hold secrets)."""
    try:
        rows = subprocess.check_output(["ps", "-axo", "pid=,comm="], text=True)
    except (OSError, subprocess.CalledProcessError):
        fail("cannot inspect processes; refusing to assume the account is idle")
    result = []
    for row in rows.splitlines():
        parts = row.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit() or not is_codex_process(parts[1]):
            continue
        pid, executable = int(parts[0]), parts[1]
        try:
            env = subprocess.run(
                ["ps", "eww", "-p", str(pid), "-o", "command="],
                check=False, text=True, capture_output=True,
            )
        except OSError:
            fail(f"cannot inspect process {pid}; refusing to assume the account is idle")
        if env.returncode != 0 or not env.stdout.strip():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                pass
            fail(f"cannot read environment of process {pid}; close it before relogin")
        result.append((pid, executable, process_home(env.stdout)))
    return result


def active_account_processes(label: str, home: Path) -> list[int]:
    active: set[int] = set()
    marker_dir = CANONICAL / "thread-writer-locks" / "n-codex-guard" / "pids"
    if marker_dir.is_dir():
        for marker in marker_dir.iterdir():
            if not marker.name.isdigit():
                continue
            try:
                marker_label = marker.read_text(encoding="utf-8").split(":", 1)[0]
                os.kill(int(marker.name), 0)
            except (OSError, ValueError):
                continue
            if marker_label == label:
                active.add(int(marker.name))
    for pid, _, selected_home in process_snapshot():
        if selected_home == home.resolve():
            active.add(pid)
    return sorted(active)


def credential_identity(data: bytes) -> tuple[str, str]:
    """Read identity locally for comparison, without exposing claims or tokens."""
    try:
        value = json.loads(data)
        tokens = value["tokens"]
        if not all(tokens.get(key) for key in ("access_token", "refresh_token", "id_token")):
            raise ValueError("missing tokens")
        segment = tokens["id_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        subject, account = claims.get("sub"), tokens.get("account_id")
        if not subject or not account:
            raise ValueError("missing identity")
        return subject, account
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        fail("cannot verify the existing and staged account identities; no credential was replaced")


def relogin_account(label: str) -> None:
    label = validate_label(label)
    target = account_home(label)
    auth = target / "auth.json"
    if not auth.is_file():
        fail(f"credential file is missing: {auth}")
    if auth.is_symlink():
        fail("refusing to replace a symlinked credential file")
    original_auth = auth.read_bytes()
    original_identity = credential_identity(original_auth)
    active = active_account_processes(label, target)
    if active:
        fail(f"account '{label}' still has active Codex processes ({', '.join(map(str, active))}); close them before relogin")
    confirm(
        f"Relogin '{label}' using an isolated staging home? "
        "Its current auth remains unchanged unless login succeeds. [y/N]: "
    )
    staging = create_staging(label)
    try:
        staged_auth = login_into(staging)
        if credential_identity(staged_auth.read_bytes()) != original_identity:
            fail("browser login selected a different user or workspace; original credential retained")
        # A daemon or IDE could have started while the browser was open.
        if active_account_processes(label, target):
            fail("account processes started during login; original credential retained")
        if auth.is_symlink() or auth.read_bytes() != original_auth:
            fail("credential changed during login; refusing to overwrite the newer credential")
        backup_dir = BACKUP_ROOT / label
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(backup_dir, 0o700)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_dir / f"auth-{stamp}.json"
        shutil.copy2(auth, backup)
        os.chmod(backup, 0o600)
        fd, temporary = tempfile.mkstemp(prefix=".auth.json.", dir=target)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(staged_auth.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, auth)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"Codex account '{label}' was relogged successfully.")
    print(f"Previous credential backup: {backup}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="codex-accounts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure-core")
    sub.add_parser("list")
    processes = sub.add_parser("processes")
    processes.add_argument("name", nargs="?")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("name")
    identify = sub.add_parser("identify")
    identify.add_argument("home")
    add = sub.add_parser("add")
    add.add_argument("name")
    relogin = sub.add_parser("relogin")
    relogin.add_argument("name")
    args = parser.parse_args()

    if args.command == "ensure-core":
        ensure_core()
    elif args.command == "list":
        for name, entry in ensure_core()["accounts"].items():
            print(f"{name}\t{entry['home']}")
    elif args.command == "resolve":
        print(account_home(args.name))
    elif args.command == "identify":
        requested = Path(args.home).expanduser().resolve()
        for name, entry in ensure_core()["accounts"].items():
            if Path(entry["home"]).resolve() == requested:
                print(name)
                break
        else:
            fail(f"unregistered Codex home: {requested}", 2)
    elif args.command == "add":
        with registry_lock():
            add_account(args.name)
    elif args.command == "relogin":
        with registry_lock():
            relogin_account(args.name)
    elif args.command == "processes":
        # Unlike account creation, diagnostics must not seed or update the registry.
        entries = read_registry()["accounts"]
        if args.name and args.name not in entries:
            fail(f"unknown account '{args.name}'", 2)
        selected = {Path(v["home"]).resolve(): n for n, v in entries.items()
                    if not args.name or n == args.name}
        for pid, executable, home in process_snapshot():
            if home in selected:
                print(f"{selected[home]}\t{pid}\t{executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
