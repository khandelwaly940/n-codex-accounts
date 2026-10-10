"""Check public release integrity and archive extraction defenses offline."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import test_install
import test_update

updater = test_update.updater


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='ncodex-release-tests-')
        cls.assets = Path(cls.temporary.name)
        subprocess.run([sys.executable,str(test_install.ROOT/'tools/build-release.py'),str(cls.assets)],check=True,stdout=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def remote(self, url, **kwargs):
        return io.BytesIO((self.assets/url.rsplit('/',1)[-1]).read_bytes())

    def test_checked_download_extracts_exact_public_payload(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater.urllib.request,'urlopen',side_effect=self.remote):
            payload = updater.download('v0.2.7',Path(tmp))
            self.assertTrue((payload/'setup/updater.py').is_file())
            self.assertFalse((payload/'.git').exists())
            self.assertEqual(json.loads((payload/'release.json').read_text())['version'],'0.2.7')

    def test_checksum_mismatch_refuses_extraction(self):
        def bad(url,**kwargs):
            if url.endswith('SHA256SUMS'): return io.BytesIO(('0'*64+'  n-codex-accounts.tar.gz\n').encode())
            return self.remote(url,**kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater.urllib.request,'urlopen',side_effect=bad), self.assertRaisesRegex(ValueError,'checksum mismatch'):
            updater.download('v0.2.7',Path(tmp))

    def test_path_traversal_refused_even_with_matching_checksum(self):
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
            member=tarfile.TarInfo('../escape');member.size=1;archive.addfile(member,io.BytesIO(b'x'))
        data=buffer.getvalue()
        def malicious(url,**kwargs):
            if url.endswith('SHA256SUMS'):return io.BytesIO((hashlib.sha256(data).hexdigest()+'  n-codex-accounts.tar.gz\n').encode())
            return io.BytesIO(data)
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater.urllib.request,'urlopen',side_effect=malicious), self.assertRaisesRegex(ValueError,'Unsafe archive'):
            updater.download('v0.2.7',Path(tmp))


if __name__=='__main__':unittest.main()
