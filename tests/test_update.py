"""Updater regressions use only temporary homes and fabricated credentials."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_install
import test_safety

updater = test_install.module('updater')
installer = test_install.installer


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ncodex-migration-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.user = Path(self.temp.name).resolve()/'user'
        self.user.mkdir()
        self.canonical = self.user/'.codex'; self.canonical.mkdir()
        self.secondary = self.user/'.codex-second-login'; self.secondary.mkdir()
        for home, subject in ((self.canonical, 'first'), (self.secondary, 'second')):
            (home/'auth.json').write_bytes(test_safety.IdentitySafety.fixture(subject=subject))
            (home/'auth.json').chmod(0o600)
            for part in updater.PARTS: (home/part).mkdir()
        self.binary = self.user/'codex-real'
        self.binary.write_text('#!/bin/sh\nprintf "codex-cli 0.158.0\\n"\n'); self.binary.chmod(0o755)
        self.environment = patch.dict(os.environ, {'CODEX_REAL_BINARY': str(self.binary)})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.payload = self.user/'payload'; shutil.copytree(test_install.ROOT/'setup', self.payload/'setup')
        (self.payload/'release.json').write_text(json.dumps({'version': '0.2.5', 'cli_versions': ['0.158.0', '0.159.0', '0.159.2', '0.160.0', '0.160.1']}))
        self.shell = self.user/'.zshrc'
        self.shell.write_text(f'''# my settings
export MY_SETTING=yes
# BEGIN CODEX_DUAL_ACCOUNT_SWITCHER
export CODEX_REAL="{self.binary}"
export CODEX_PRIMARY_HOME="{self.canonical}"
export CODEX_SECOND_HOME="{self.secondary}"
export CODEX_SHARED_STATE="{self.canonical}"
codex() {{
 case "$1" in
  home|primary)
   export CODEX_HOME="$CODEX_PRIMARY_HOME"
   ;;
  work)
   export CODEX_HOME="$CODEX_SECOND_HOME"
   ;;
 esac
}}
# END CODEX_DUAL_ACCOUNT_SWITCHER
''')
        self.auth_before = {home: ((home/'auth.json').read_bytes(), (home/'auth.json').stat().st_mtime_ns)
                            for home in (self.canonical, self.secondary)}

    def rollout(self, home, session, parent=None):
        payload = {'id': session, 'history_mode': 'paginated'}
        if parent: payload['forked_from_id'] = parent
        p = home/'sessions'/f'{session}.jsonl'
        p.write_text(json.dumps({'type': 'session_meta', 'payload': payload})+'\n')
        return p

    def plan(self):
        self.stage = self.user/'stage'
        return updater.plan(self.user, self.payload, self.stage)

    def assert_auth_untouched(self):
        for home, original in self.auth_before.items():
            self.assertEqual(((home/'auth.json').read_bytes(), (home/'auth.json').stat().st_mtime_ns), original)

    def test_legacy_detects_labels_and_planning_is_readonly(self):
        before = updater.fingerprint([self.shell, self.canonical, self.secondary])
        proposed = self.plan()
        self.assertEqual(set(proposed['registry']['accounts']), {'home', 'primary', 'work'})
        self.assertTrue(proposed['layout'])
        self.assertEqual(before, updater.fingerprint([self.shell, self.canonical, self.secondary]))

    def test_plan_accepts_new_cli_without_touching_credentials(self):
        self.binary.write_text('#!/bin/sh\nprintf "codex-cli 0.160.1\\n"\n')
        proposed = self.plan()
        self.assertEqual(proposed['origin'], 'legacy two-account')
        self.assert_auth_untouched()

    def test_migrate_separate_history_preserves_originals_and_auth(self):
        self.rollout(self.canonical, 'one')
        self.rollout(self.secondary, 'two', 'one')
        (self.secondary/'attachments/note.txt').write_text('attachment')
        proposed = self.plan()
        with patch.object(updater, 'idle'):
            backup = updater.apply(self.user, self.payload, self.stage, proposed)
        self.assertTrue((self.canonical/'sessions/two.jsonl').is_file())
        self.assertTrue((self.secondary/'sessions').is_symlink())
        self.assertTrue(list(backup.glob('directory-*/two.jsonl')))
        self.assertIn('export MY_SETTING=yes', self.shell.read_text())
        self.assertNotIn('CODEX_DUAL_ACCOUNT_SWITCHER', self.shell.read_text())
        self.assert_auth_untouched()

    def test_conflict_blocks_before_any_live_change(self):
        (self.canonical/'attachments/x').write_text('first')
        (self.secondary/'attachments/x').write_text('second')
        before = self.shell.read_text()
        with self.assertRaisesRegex(ValueError, 'Conflicting file'): self.plan()
        self.assertEqual(self.shell.read_text(), before)
        self.assertFalse((self.secondary/'attachments').is_symlink())
        self.assert_auth_untouched()

    def test_unknown_layout_and_separate_database_block(self):
        self.shell.write_text('codex() { arbitrary-command; }')
        with self.assertRaises(ValueError): self.plan()

    def test_independent_account_database_is_never_merged(self):
        with contextlib.closing(sqlite3.connect(self.secondary/'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads (id TEXT)')
        with self.assertRaisesRegex(ValueError, 'Separate account database'): self.plan()
        self.assert_auth_untouched()

    def test_symlink_shell_refused(self):
        real = self.user/'shell-real'
        self.shell.rename(real); self.shell.symlink_to(real)
        with self.assertRaisesRegex(ValueError, 'Symlinked shell'): self.plan()

    def test_declined_plan_does_not_modify_accounts_or_shell(self):
        before = updater.fingerprint([self.shell, self.canonical, self.secondary])
        with patch('builtins.input', return_value='n'), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(updater.update(self.user, bundle=self.payload))
        self.assertEqual(before, updater.fingerprint([self.shell, self.canonical, self.secondary]))

    def test_details_are_concise_and_never_approve(self):
        manifest = json.loads((self.payload/'release.json').read_text())
        manifest['summary'] = ['Shorter update screen.', 'Supports both reviewed CLI versions.']
        (self.payload/'release.json').write_text(json.dumps(manifest))
        before = updater.fingerprint([self.shell, self.canonical, self.secondary])
        output = io.StringIO()
        with patch('builtins.input', side_effect=['d', 'n']) as prompt, contextlib.redirect_stdout(output):
            self.assertFalse(updater.update(self.user, bundle=self.payload))
        text = output.getvalue()
        self.assertIn('Shorter update screen.', text)
        self.assertIn('Update details', text)
        self.assertIn('Credentials: preserved; no login or logout.', text)
        self.assertNotIn(str(self.canonical), text)
        self.assertLess(len(text.splitlines()), 20)
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(before, updater.fingerprint([self.shell, self.canonical, self.secondary]))

    def test_details_then_approval_runs_update_once(self):
        with patch('builtins.input', side_effect=['d', 'yes']), patch.object(updater, 'apply', return_value=self.user/'backup') as apply, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(updater.update(self.user, bundle=self.payload))
        apply.assert_called_once()
        self.assert_auth_untouched()

    def test_inspection_plan_keeps_paths_and_never_prompts(self):
        output = io.StringIO()
        with patch('builtins.input') as prompt, contextlib.redirect_stdout(output):
            self.assertFalse(updater.update(self.user, bundle=self.payload, plan_only=True))
        prompt.assert_not_called()
        self.assertIn(str(self.canonical), output.getvalue())

    def test_failure_detail_is_private_and_screen_is_bounded(self):
        message = 'Conflict: ' + 'x' * 1000
        output = io.StringIO()
        with contextlib.redirect_stderr(output): updater.report_error(self.user, ValueError(message))
        self.assertLess(len(output.getvalue().splitlines()[0]), 260)
        log = updater.runtime(self.user)/'update-error.json'
        self.assertEqual(json.loads(log.read_text())['error'], message)
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_registry_based_legacy_keeps_custom_labels(self):
        path = self.user/'.config/dual-codex/accounts.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'version':1,'accounts':{'custom':{'home':str(self.canonical)},
                                                          'work':{'home':str(self.secondary)}}}))
        self.shell.write_text('# BEGIN CODEX_MULTI_ACCOUNT_SWITCHER\nsource "$HOME/.local/share/dual-codex/shell.zsh"\n# END CODEX_MULTI_ACCOUNT_SWITCHER\n')
        proposed = self.plan()
        with patch.object(updater, 'idle'):
            updater.apply(self.user, self.payload, self.stage, proposed)
        actual = json.loads((self.user/'.config/n-codex-accounts/accounts.json').read_text())
        self.assertEqual(set(actual['accounts']), {'custom','work'})
        self.assert_auth_untouched()

    def test_low_disk_stops_before_replacing_directories(self):
        proposed = self.plan()
        with patch.object(updater, 'idle'), patch.object(updater.shutil, 'disk_usage', return_value=type('Disk',(),{'free':0})()), self.assertRaisesRegex(ValueError, 'Insufficient'):
            updater.apply(self.user, self.payload, self.stage, proposed)
        self.assertFalse((self.secondary/'sessions').is_symlink())

    def test_missing_parent_blocks_migration(self):
        self.rollout(self.secondary, 'child', 'missing')
        with self.assertRaises(subprocess.CalledProcessError): self.plan()
        self.assert_auth_untouched()

    def test_changed_state_after_plan_blocks(self):
        proposed = self.plan(); self.shell.write_text(self.shell.read_text()+'# later edit\n')
        with patch.object(updater, 'idle'), self.assertRaisesRegex(ValueError, 'State changed'):
            updater.apply(self.user, self.payload, self.stage, proposed)
        self.assertFalse((self.secondary/'sessions').is_symlink())

    def test_active_writer_blocks(self):
        proposed = self.plan()
        with patch.object(updater, 'idle', side_effect=ValueError('Close affected')), self.assertRaises(ValueError):
            updater.apply(self.user, self.payload, self.stage, proposed)
        self.assert_auth_untouched()
        self.assertFalse((self.secondary/'sessions').is_symlink())

    def test_failure_restores_layout_shell_and_permissions(self):
        self.rollout(self.canonical, 'one'); self.rollout(self.secondary, 'two')
        before = updater.fingerprint([self.shell, self.canonical, self.secondary])
        proposed = self.plan()
        original_module = updater.module
        def failing_module(path):
            loaded = original_module(path)
            if path == self.payload/'setup/install.py':
                loaded.install = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('injected install failure'))
            return loaded
        with patch.object(updater, 'idle'), patch.object(updater, 'module', side_effect=failing_module), self.assertRaises(RuntimeError):
            updater.apply(self.user, self.payload, self.stage, proposed)
        self.assertEqual(before, updater.fingerprint([self.shell, self.canonical, self.secondary]))
        self.assert_auth_untouched()

    def test_attachment_index_merge_does_not_schedule_live_paths_for_removal(self):
        left = {'attachmentPaths':['a'], 'pendingRemovalPaths':['b'], 'textExcerptsByPath':{'a':'A'}}
        right = {'attachmentPaths':['b'], 'pendingRemovalPaths':[], 'textExcerptsByPath':{'b':'B'}}
        merged = updater.merge_index(left,right)
        self.assertEqual(merged['pendingRemovalPaths'], [])
        self.assertEqual(merged['attachmentPaths'], ['a','b'])

    def test_daily_decline_and_offline_check_only_once(self):
        release = subprocess.CompletedProcess([], 0, json.dumps({'tag':'v9.0.0','notes':'test'}))
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), \
             patch.object(updater.subprocess, 'run', return_value=release) as network, \
             patch('builtins.input', return_value='') as answer, contextlib.redirect_stdout(io.StringIO()):
            # redirect_stdout changes isatty, so patch the replacement too.
            with patch('sys.stdout.isatty', return_value=True):
                self.assertEqual(updater.notice(self.user), 0)
                self.assertEqual(updater.notice(self.user), 0)
            self.assertEqual(network.call_count, 1); self.assertEqual(answer.call_count, 1)
        (updater.runtime(self.user)/'update-check.json').write_text('{"date":"2000-01-01"}')
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), \
             patch.object(updater.subprocess, 'run', side_effect=subprocess.TimeoutExpired('network', 3)) as network:
            self.assertEqual(updater.notice(self.user), 0)
            self.assertEqual(updater.notice(self.user), 0)
            self.assertEqual(network.call_count, 1)

    def test_completed_history_rollback_refused(self):
        proposed = self.plan()
        with patch.object(updater, 'idle'):
            updater.apply(self.user, self.payload, self.stage, proposed)
            with self.assertRaisesRegex(ValueError, 'automatic later rollback'):
                updater.rollback(self.user)
        self.assert_auth_untouched()

    def test_noninteractive_notice_never_contacts_github(self):
        with patch('sys.stdin.isatty', return_value=False), patch.object(updater.subprocess,'run') as network:
            self.assertEqual(updater.notice(self.user),0)
            network.assert_not_called()

    def test_interrupted_update_blocks_launch_even_noninteractively(self):
        updater.write(updater.runtime(self.user)/'update-journal.json', {'status':'installing'})
        with patch('sys.stdin.isatty', return_value=False), patch.object(updater.subprocess,'run') as network, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(updater.notice(self.user),21)
            network.assert_not_called()

    def test_next_day_offers_declined_update_again(self):
        updater.write(updater.runtime(self.user)/'update-check.json', {'date':'2000-01-01'})
        release = subprocess.CompletedProcess([],0,json.dumps({'tag':'v9.0.0','notes':'test'}))
        with contextlib.redirect_stdout(io.StringIO()), patch('sys.stdin.isatty',return_value=True), \
             patch('sys.stdout.isatty',return_value=True), patch.object(updater.subprocess,'run',return_value=release), \
             patch('builtins.input',return_value='') as answer:
            self.assertEqual(updater.notice(self.user),0)
            answer.assert_called_once()

    def test_update_now_still_uses_full_update_flow(self):
        release = subprocess.CompletedProcess([],0,json.dumps({'tag':'v9.0.0','notes':'test'}))
        with contextlib.redirect_stdout(io.StringIO()), patch('sys.stdin.isatty',return_value=True), \
             patch('sys.stdout.isatty',return_value=True), patch.object(updater.subprocess,'run',return_value=release), \
             patch('builtins.input',return_value='u'), patch.object(updater,'update',return_value=True) as update:
            self.assertEqual(updater.notice(self.user),20)
            update.assert_called_once_with(self.user,check=False,plan_only=False)

    def test_public_helper_update_and_rollback(self):
        self.shell.write_text('# custom\n')
        installer.install(self.user, self.binary, login=False)
        (updater.runtime(self.user)/'VERSION').write_text('0.1.1\n')
        original = self.shell.read_text()
        proposed = self.plan()
        self.assertFalse(proposed['layout'])
        with patch.object(updater, 'idle'):
            updater.apply(self.user, self.payload, self.stage, proposed)
            with patch('builtins.input', return_value='y'):
                updater.rollback(self.user)
        self.assertEqual(self.shell.read_text(), original)
        self.assertEqual((updater.runtime(self.user)/'VERSION').read_text(), '0.1.1\n')
        self.assertEqual((updater.runtime(self.user)/'bin/run').stat().st_mode & 0o777, 0o755)
        self.assert_auth_untouched()


if __name__ == '__main__': unittest.main()
