"""Release checks and bootstrap recovery without the REST API or real user data."""
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import test_install
import test_update

updater = test_update.updater
loader = importlib.machinery.SourceFileLoader('bootstrap', str(test_install.ROOT/'ncodex-bootstrap.command'))
spec = importlib.util.spec_from_loader(loader.name, loader)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
BASE = 'https://github.com/khandelwaly940/n-codex-accounts/releases/'
TAG = 'v0.2.11'


def redirect(location):
    response = io.BytesIO(b'')
    response.geturl = lambda: location
    return response


class DiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='ncodex-discovery-tests-')
        cls.assets = Path(cls.temporary.name)
        subprocess.run([sys.executable, str(test_install.ROOT/'tools/build-release.py'), str(cls.assets)],
                       check=True, stdout=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def remote(self, request, **kwargs):
        url = request.full_url if hasattr(request, 'full_url') else request
        if 'api.github.com' in url:
            raise urllib.error.HTTPError(url, 403, 'rate limit exceeded', {}, None)
        if url == BASE+'latest':
            self.assertEqual(request.get_method(), 'HEAD')
            return redirect(BASE+'tag/'+TAG)
        self.assertTrue(url.startswith(BASE+'download/'+TAG+'/'), url)
        return io.BytesIO((self.assets/url.rsplit('/', 1)[-1]).read_bytes())

    def test_updater_and_bootstrap_use_only_public_redirect(self):
        for function in (updater.latest, bootstrap.latest_tag):
            with self.subTest(function=function.__name__), patch.object(updater.urllib.request, 'urlopen', side_effect=self.remote) as network:
                result = function()
                self.assertEqual(result['tag'] if isinstance(result, dict) else result, TAG)
                self.assertEqual(network.call_count, 1)
                self.assertEqual(network.call_args.args[0].full_url, BASE+'latest')

    def test_foreign_or_nonstable_redirect_is_refused(self):
        locations = ['http://github.com/khandelwaly940/n-codex-accounts/releases/tag/'+TAG,
                     'https://example.com/releases/tag/'+TAG,
                     'https://github.com/other/repo/releases/tag/'+TAG,
                     BASE+'tag/v0.2.11-alpha', BASE+'tag/'+TAG+'?other=1', BASE+'latest']
        for function in (updater.latest, bootstrap.latest_tag):
            for location in locations:
                with self.subTest(function=function.__name__, location=location), patch.object(updater.urllib.request, 'urlopen', return_value=redirect(location)), self.assertRaisesRegex(ValueError, 'stable release'):
                    function()

    def test_web_throttling_is_clear_and_does_not_retry(self):
        for function in (updater.latest, bootstrap.latest_tag):
            for code in (403, 429):
                error = urllib.error.HTTPError(BASE+'latest', code, 'rate limited', {}, None)
                with self.subTest(function=function.__name__, code=code), patch.object(updater.urllib.request, 'urlopen', side_effect=error) as network, self.assertRaisesRegex(ValueError, 'Retry later; setup unchanged'):
                    function()
                self.assertEqual(network.call_count, 1)

    def test_network_failure_does_not_fall_back_to_api(self):
        for function in (updater.latest, bootstrap.latest_tag):
            with self.subTest(function=function.__name__), patch.object(updater.urllib.request, 'urlopen', side_effect=urllib.error.URLError('offline')) as network, self.assertRaises(urllib.error.URLError):
                function()
            self.assertEqual(network.call_count, 1)

    def test_bootstrap_recovery_verifies_bundle_and_preserves_plan_mode(self):
        with patch.object(updater.urllib.request, 'urlopen', side_effect=self.remote), patch.object(sys, 'platform', 'darwin'), patch.object(sys, 'argv', ['ncodex-bootstrap.command', '--plan']), patch.object(bootstrap.subprocess, 'call', return_value=0) as run:
            self.assertEqual(bootstrap.main(), 0)
        args = run.call_args.args[0]
        self.assertEqual(args[2], 'update')
        self.assertIn('--bundle', args)
        self.assertEqual(args[-1], '--plan')
        self.assertEqual(run.call_count, 1)

    def test_bootstrap_rejects_corrupt_download_before_running_any_code(self):
        def corrupt(request, **kwargs):
            if isinstance(request, str) and request.endswith('n-codex-accounts.tar.gz'):
                return io.BytesIO(b'corrupt')
            return self.remote(request, **kwargs)
        with patch.object(updater.urllib.request, 'urlopen', side_effect=corrupt), patch.object(sys, 'platform', 'darwin'), patch.object(sys, 'argv', ['ncodex-bootstrap.command']), patch.object(bootstrap.subprocess, 'call') as run, self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            bootstrap.main()
        run.assert_not_called()
