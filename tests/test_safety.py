"""Offline helper regressions. Never invoke login or relogin, even with mocks."""

import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SETUP = Path(__file__).resolve().parents[1] / "setup"
loader = importlib.machinery.SourceFileLoader("accounts", str(SETUP / "accounts.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
accounts = importlib.util.module_from_spec(spec)
loader.exec_module(accounts)


class ProcessSafety(unittest.TestCase):
    def test_complete_home_values_and_default(self):
        for home in ("/tmp/.codex", "/tmp/.codex-accounts/work", "/tmp/.codex-accounts/personal",
                     "/tmp/account with spaces"):
            self.assertEqual(accounts.process_home(f"codex CODEX_HOME={home} TERM=xterm"),
                             Path(home).resolve())
        self.assertEqual(accounts.process_home("codex TERM=xterm"), accounts.CANONICAL.resolve())

    def test_executables_including_daemon_and_editor(self):
        self.assertTrue(accounts.is_codex_process("/tmp/packages/app-server-daemon/bin/codex"))
        self.assertTrue(accounts.is_codex_process("/Applications/Visual Studio Code.app/Contents/MacOS/Code"))
        self.assertFalse(accounts.is_codex_process("/bin/zsh"))
        self.assertFalse(accounts.is_codex_process("/tmp/not-codex"))

    def test_only_selected_account_is_active(self):
        with tempfile.TemporaryDirectory() as temporary:
            canonical = (Path(temporary) / ".codex").resolve()
            work = (Path(temporary) / ".codex-accounts/work").resolve()
            snapshot = [(101, "/bin/codex", canonical), (102, "/bin/codex", work)]
            with patch.object(accounts, "CANONICAL", canonical), \
                 patch.object(accounts, "process_snapshot", return_value=snapshot):
                self.assertEqual(accounts.active_account_processes("primary", canonical), [101])
                self.assertEqual(accounts.active_account_processes("work", work), [102])

    def test_process_inspection_failure_is_closed(self):
        with patch.object(accounts.subprocess, "check_output", side_effect=OSError), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            accounts.process_snapshot()

    def test_snapshot_does_not_match_command_text(self):
        rows = "101 /bin/codex\n102 /bin/zsh\n"
        result = subprocess.CompletedProcess([], 0, "codex CODEX_HOME=/tmp/.codex-accounts/personal TERM=xterm")
        with patch.object(accounts.subprocess, "check_output", return_value=rows), \
             patch.object(accounts.subprocess, "run", return_value=result) as inspect:
            self.assertEqual(accounts.process_snapshot(),
                             [(101, "/bin/codex", Path("/tmp/.codex-accounts/personal").resolve())])
            self.assertEqual(inspect.call_count, 1)


class IdentitySafety(unittest.TestCase):
    @staticmethod
    def fixture(subject="user-a", workspace="workspace-a", refresh="fake-refresh"):
        payload = base64.urlsafe_b64encode(json.dumps({"sub": subject}).encode()).decode().rstrip("=")
        return json.dumps({"tokens": {"id_token": f"fake.{payload}.fake", "access_token": "fake-access",
                                      "refresh_token": refresh, "account_id": workspace}}).encode()

    def test_refresh_does_not_change_identity(self):
        self.assertEqual(accounts.credential_identity(self.fixture()),
                         accounts.credential_identity(self.fixture(refresh="different-fake-refresh")))

    def test_wrong_user_and_workspace_are_distinguishable(self):
        identity = accounts.credential_identity(self.fixture())
        self.assertNotEqual(identity, accounts.credential_identity(self.fixture(subject="user-b")))
        self.assertNotEqual(identity, accounts.credential_identity(self.fixture(workspace="workspace-b")))

    def test_malformed_identity_is_closed(self):
        for data in (b"{}", b'{"tokens": []}', b"invalid-json"):
            with self.subTest(data=data), contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit):
                accounts.credential_identity(data)


class GuardSafety(unittest.TestCase):
    def test_contended_guard_never_removes_another_mutex(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guard = root / "guard"
            mutex = guard / "mutex"
            mutex.mkdir(parents=True)
            registry = root / "registry"
            registry.write_text("#!/bin/sh\nprintf '/tmp/account\\n'\n")
            registry.chmod(0o700)
            script = (SETUP / "guard.zsh").read_text()
            script = script.replace('guard="$HOME/.codex/thread-writer-locks/n-codex-guard"',
                                    f'guard="{guard}"')
            script = script.replace('accounts="$HOME/.local/share/n-codex-accounts/bin/accounts"',
                                    f'accounts="{registry}"')
            result = subprocess.run(["zsh", "-c", script, "test-guard", "check", "primary"],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertIn("could not acquire", result.stderr)
            self.assertTrue(mutex.is_dir())


class SelectorSafety(unittest.TestCase):
    def test_three_selectors_launch_and_resume_in_selected_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shell = root / "shell.zsh"
            shell.write_text((SETUP / "shell.zsh").read_text().replace('source "$HOME/.local/share/n-codex-accounts/runtime.zsh"', ":"))
            registry = root / "registry"
            registry.write_text('#!/bin/sh\n[ "$1" = resolve ] || exit 2\n'
                                'case "$2" in primary|work|personal) printf "/tmp/test-%s\\n" "$2";; *) exit 2;; esac\n')
            registry.chmod(0o700)
            launcher = root / "launcher"
            launcher.write_text('#!/bin/sh\nprintf "LAUNCH:%s|%s|%s\\n" "$CODEX_HOME" "$CODEX_SQLITE_HOME" "$*"\n')
            launcher.chmod(0o700)
            script = 'source "$1"; CODEX_ACCOUNTS="$2"; CODEX_REAL="$3"; codex "$4"; codex resume --all --dangerously-bypass-approvals-and-sandbox'
            for name in ("primary", "work", "personal"):
                for answer in ("y", "n"):
                    with self.subTest(account=name, answer=answer):
                        result = subprocess.run(["zsh", "-f", "-c", script, "test-selector",
                                                 str(shell),
                                                 str(registry), str(launcher), name], input=answer+'\n',
                                                capture_output=True, text=True, timeout=10)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        launches = [line for line in result.stdout.splitlines() if "LAUNCH:" in line]
                        self.assertEqual(len(launches), 2 if answer == "y" else 1)
                        for line in launches:
                            self.assertIn(f'LAUNCH:/tmp/test-{name}|{Path.home() / ".codex"}|', line)
                        if answer == "y":
                            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", launches[0])
                        self.assertIn("resume --all", launches[-1])

    def test_processes_dispatch_does_not_launch_codex(self):
        with tempfile.TemporaryDirectory() as temporary:
            shell = Path(temporary) / "shell.zsh"
            shell.write_text((SETUP / "shell.zsh").read_text().replace('source "$HOME/.local/share/n-codex-accounts/runtime.zsh"', ':'))
            script = 'source "$1"; CODEX_ACCOUNTS=/bin/echo; CODEX_REAL=/usr/bin/false; codex processes primary'
            result = subprocess.run(["zsh", "-f", "-c", script, "test-selector", str(shell)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "processes primary\n")


if __name__ == "__main__":
    unittest.main()
