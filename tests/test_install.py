"""Temporary-directory tests; never invoke a real login or relogin flow."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import test_safety

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'setup' / f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module('install')
preflight = module('preflight')


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n-codex-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'user with spaces'
        self.root.mkdir()
        self.canonical = self.root / '.codex'
        self.canonical.mkdir()
        self.auth = self.canonical / 'auth.json'
        self.auth.write_bytes(test_safety.IdentitySafety.fixture())
        self.auth.chmod(0o600)
        self.binary = self.root / 'fake-codex'
        self.binary.write_text('#!/bin/sh\nprintf "codex-cli 0.158.0\\n"\n')
        self.binary.chmod(0o755)

    def test_install_repeat_preserves_auth_shell_and_accounts(self):
        shell = self.root / '.zshrc'
        shell.write_text('# user customization\nexport MY_SETTING=yes\n')
        before = (self.auth.read_bytes(), self.auth.stat().st_mtime_ns)
        installer.install(self.root, self.binary, login=False)
        accounts = installer.load_accounts(self.root)
        registry = accounts.read_registry()
        registry['accounts']['work'] = {'home': str(self.root / '.codex-accounts/work')}
        accounts.write_registry(registry)
        installer.install(self.root, self.binary, login=False)
        self.assertEqual((self.auth.read_bytes(), self.auth.stat().st_mtime_ns), before)
        self.assertEqual(shell.read_text().count(installer.START), 1)
        self.assertIn('export MY_SETTING=yes', shell.read_text())
        self.assertIn('work', accounts.read_registry()['accounts'])
        result = subprocess.run([str(self.root / '.local/share/n-codex-accounts/bin/accounts'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('processes', result.stdout)

    def test_missing_login_does_not_modify_existing_state(self):
        self.auth.unlink()
        with self.assertRaisesRegex(ValueError, 'No primary'):
            installer.install(self.root, self.binary, login=False)
        self.assertFalse((self.root / '.zshrc').exists())
        self.assertFalse((self.root / '.local').exists())

    def test_foreign_shell_integration_refused(self):
        for text in ('codex() { command codex "$@"; }', 'source "$HOME/.local/share/account-switcher/shell.zsh"'):
            (self.root / '.zshrc').write_text(text)
            with self.assertRaises(ValueError):
                installer.install(self.root, self.binary, login=False)
            self.assertEqual((self.root / '.zshrc').read_text(), text)

    def test_keychain_configuration_refused_without_changes(self):
        config = self.canonical / 'config.toml'
        config.write_text('cli_auth_credentials_store="keyring"\n')
        with self.assertRaisesRegex(ValueError, 'file-based'):
            installer.install(self.root, self.binary, login=False)
        self.assertFalse((self.root / '.local').exists())

    def test_add_isolated_account_and_copy_mcp_definitions(self):
        installer.install(self.root, self.binary, login=False)
        accounts = installer.load_accounts(self.root)
        (self.canonical / 'config.toml').write_text('[mcp_servers.example]\ncommand="example-mcp"\n')
        original = self.auth.read_bytes()
        def fake_login(stage):
            auth = stage / 'auth.json'
            auth.write_bytes(test_safety.IdentitySafety.fixture(subject='second-user'))
            return auth
        with patch.object(accounts, 'confirm'), patch.object(accounts, 'login_into', side_effect=fake_login), contextlib.redirect_stdout(io.StringIO()):
            accounts.add_account('work')
        target = self.root / '.codex-accounts/work'
        self.assertEqual(self.auth.read_bytes(), original)
        for item in ('sessions', 'archived_sessions', 'attachments', 'thread-writer-locks'):
            self.assertTrue((target / item).is_symlink())
            self.assertEqual((target / item).resolve(), self.canonical / item)
        self.assertIn('[mcp_servers.example]', (target / 'config.toml').read_text())
        self.assertNotEqual((target / 'auth.json').read_bytes(), original)

    def test_cancelled_add_preserves_registry(self):
        installer.install(self.root, self.binary, login=False)
        accounts = installer.load_accounts(self.root)
        before = accounts.REGISTRY.read_bytes()
        with patch.object(accounts, 'confirm', side_effect=SystemExit(0)), self.assertRaises(SystemExit):
            accounts.add_account('work')
        self.assertEqual(accounts.REGISTRY.read_bytes(), before)
        self.assertFalse((self.root / '.codex-accounts/work').exists())

    def test_readonly_preflight_fresh_home_and_database(self):
        installer.install(self.root, self.binary, login=False)
        # No database is required until the first actual Codex session.
        with patch.dict(os.environ, {}, clear=True):
            preflight.check(self.canonical, self.canonical)
        db = self.canonical / 'state_5.sqlite'
        with contextlib.closing(sqlite3.connect(db)) as con:
            con.execute('CREATE TABLE sample (value INTEGER)')
        original = db.read_bytes()
        with patch.dict(os.environ, {}, clear=True):
            preflight.check(self.canonical, self.canonical)
        self.assertEqual(db.read_bytes(), original)

    def test_broken_link_and_auth_override_fail_closed(self):
        installer.install(self.root, self.binary, login=False)
        target = self.root / '.codex-accounts/work'
        target.mkdir(parents=True)
        (target / 'auth.json').write_bytes(test_safety.IdentitySafety.fixture())
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, 'Incorrect shared link'):
            preflight.check(target, self.canonical)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake'}, clear=True), self.assertRaisesRegex(ValueError, 'Unset'):
            preflight.check(self.canonical, self.canonical)

    def test_installed_launchers_three_accounts(self):
        installer.install(self.root, self.binary, login=False)
        accounts = installer.load_accounts(self.root)
        for name in ('work', 'personal'):
            target = self.root / '.codex-accounts' / name
            target.mkdir(parents=True)
            (target / 'auth.json').write_bytes(test_safety.IdentitySafety.fixture(subject=name))
            accounts.install_shared_links(target)
            registry = accounts.read_registry()
            registry['accounts'][name] = {'home': str(target)}
            accounts.write_registry(registry)
        installed = self.root / '.local/share/n-codex-accounts'
        # Retarget fixture copies without altering HOME or touching the actual user's setup.
        for name in ('shell.zsh', 'guard.zsh', 'run.zsh'):
            p = installed / name
            p.write_text(p.read_text().replace('$HOME', str(self.root)))
        for name in ('accounts.py', 'preflight.py'):
            p = installed / name
            p.write_text(p.read_text().replace('Path.home()', f'Path({str(self.root)!r})'))
        script = 'source "$1"; codex "$2" <<< n; codex --version'
        for name in ('primary', 'work', 'personal'):
            result = subprocess.run(['zsh', '-f', '-c', script, 'fixture', str(installed / 'shell.zsh'), name],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('codex-cli 0.158.0', result.stdout)

    def test_download_bootstrap_installs_only_fixture_home(self):
        archive_root = self.root / 'archive'
        shutil.copytree(ROOT / 'setup', archive_root / 'setup')
        source = archive_root / 'setup/install.py'
        source.write_text(source.read_text().replace('Path.home()', f'Path({str(self.root)!r})'))
        files = {str(path.relative_to(archive_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (archive_root / 'setup').iterdir() if path.is_file()}
        (archive_root / 'release.json').write_text(json.dumps({'version': '0.2.7', 'files': files}))
        archive = self.root / 'n-codex-accounts.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            for name in sorted(files):
                bundle.add(archive_root / name, arcname=name)
            bundle.add(archive_root / 'release.json', arcname='release.json')
        checksums = self.root / 'SHA256SUMS'
        checksums.write_text(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
        bootstrap = self.root / 'install.sh'
        shutil.copy2(ROOT / 'install.sh', bootstrap)
        mock_bin = self.root / 'mock-bin'
        mock_bin.mkdir()
        curl = mock_bin / 'curl'
        curl.write_text('#!' + os.sys.executable + '\nimport shutil,sys\n'
                        + f'root={str(self.root)!r}\n'
                        + 'url=sys.argv[sys.argv.index("--output")-1]\n'
                        + 'if not url.startswith("https://github.com/khandelwaly940/n-codex-accounts/releases/latest/download/"): sys.exit(10)\n'
                        + 'shutil.copyfile(root+"/"+url.rsplit("/",1)[-1],sys.argv[sys.argv.index("--output")+1])\n')
        curl.chmod(0o755)
        environment = os.environ.copy()
        environment['PATH'] = str(mock_bin) + os.pathsep + environment['PATH']
        result = subprocess.run(['zsh', str(bootstrap), '--skip-login', '--cli', str(self.binary)],
                                env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.local/share/n-codex-accounts/bin/accounts').is_file())
        self.assertIn(installer.START, (self.root / '.zshrc').read_text())
        for version in ('0.159.0', '0.159.2', '0.160.0', '0.160.1', '0.161.0', '0.162.1'):
            self.binary.write_text(f'#!/bin/sh\nprintf "codex-cli {version}\\n"\n')
            result = subprocess.run(['zsh', str(bootstrap), '--skip-login', '--cli', str(self.binary)],
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.root / '.local/share/n-codex-accounts/VERSION').read_text().strip(), '0.2.7')


if __name__ == '__main__':
    unittest.main()
