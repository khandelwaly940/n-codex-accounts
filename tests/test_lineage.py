"""Offline checks for physical rollout references and same-chat continuation."""
import importlib.util
import json
import shutil
import subprocess
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'SETUP/validate-codex-lineage.py' if (ROOT/'SETUP/validate-codex-lineage.py').exists() else ROOT/'setup/validate_lineage.py'
spec = importlib.util.spec_from_file_location('lineage', SOURCE)
lineage = importlib.util.module_from_spec(spec); spec.loader.exec_module(lineage)
A = '00000000-0000-0000-0000-000000000001'
B = '00000000-0000-0000-0000-000000000002'
C = '00000000-0000-0000-0000-000000000003'

class LineageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def rollout(self, node, session=A, base=None, ordinal=0):
        meta={'id':session,'history_mode':'paginated'}
        if base is not None:meta['history_base']=base
        p=self.root/f'rollout-2026-09-30T12-00-00-{session}_{node}.jsonl'
        p.write_text(json.dumps({'type':'session_meta','ordinal':ordinal,'payload':meta})+'\n')
        return p
    def base(self,p,node,cutoff=1):
        return {'thread_id':node,'end_byte_offset':p.stat().st_size,'end_ordinal_exclusive':cutoff}
    def test_same_chat_continuation_is_valid(self):
        p=self.rollout(A);self.rollout(B,base=self.base(p,A),ordinal=1)
        nodes,groups,errors=lineage.validate([self.root]);self.assertFalse(errors);self.assertEqual(len(nodes),2);self.assertEqual(len(groups),1)
    def test_continuation_chain_uses_physical_ids(self):
        p=self.rollout(A);q=self.rollout(B,base=self.base(p,A),ordinal=1)
        self.rollout(C,base=self.base(q,B,2),ordinal=2)
        self.assertFalse(lineage.validate([self.root])[2])
    def test_unrelated_duplicates_are_rejected(self):
        self.rollout(A);self.rollout(B)
        self.assertIn('duplicate session id',' '.join(lineage.validate([self.root])[2]))
    def test_missing_physical_parent_is_rejected(self):
        p=self.rollout(A);self.rollout(B,base=self.base(p,C),ordinal=1)
        self.assertIn('missing source rollout',' '.join(lineage.validate([self.root])[2]))
    def test_bad_offset_and_ordinal_are_rejected(self):
        p=self.rollout(A);base=self.base(p,A);base['end_byte_offset']-=1
        self.rollout(B,base=base,ordinal=1)
        self.assertIn('boundary',' '.join(lineage.validate([self.root])[2]))
        self.rollout(B,base=self.base(p,A,2),ordinal=2)
        self.assertIn('ordinal',' '.join(lineage.validate([self.root])[2]))
    def test_source_prefix_may_have_later_appends(self):
        p=self.rollout(A);base=self.base(p,A)
        with p.open('a') as f:f.write(json.dumps({'type':'event_msg','ordinal':1,'payload':{}})+'\n')
        self.rollout(B,base=base,ordinal=1)
        self.assertFalse(lineage.validate([self.root])[2])
    def test_physical_cycle_is_rejected(self):
        p=self.rollout(A);q=self.rollout(B,base=self.base(p,A),ordinal=1)
        self.rollout(A,base=self.base(q,B,2),ordinal=2)
        self.assertIn('cycle',' '.join(lineage.validate([self.root])[2]))
    def test_repeated_root_paths_are_deduplicated(self):
        self.rollout(A)
        self.assertFalse(lineage.validate([self.root,self.root])[2])

    def compress(self, path):
        data = path.read_bytes()
        target = path.with_suffix('.jsonl.zst')
        try:
            from compression.zstd import compress
            target.write_bytes(compress(data))
        except ImportError:
            executable = shutil.which('zstd')
            if not executable: self.skipTest('zstd decoder is not installed')
            result = subprocess.run([executable, '-q', '-c'], input=data, capture_output=True, check=True)
            target.write_bytes(result.stdout)
        path.unlink()
        return target
    def test_compressed_parent_uses_uncompressed_byte_offsets(self):
        p=self.rollout(A)
        with p.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'type':'event_msg','ordinal':1,'payload':{'text':'漢字🙂'}},ensure_ascii=False)+'\n')
        base=self.base(p,A,2)
        with p.open('a') as handle:handle.write(json.dumps({'ordinal':2,'type':'event_msg'})+'\n')
        self.compress(p);self.rollout(B,base=base,ordinal=2)
        self.assertFalse(lineage.validate([self.root])[2])
    def test_all_compressed_continuations_are_discovered(self):
        p=self.rollout(A);q=self.rollout(B,base=self.base(p,A),ordinal=1)
        self.compress(p);self.compress(q)
        nodes,groups,errors=lineage.validate([self.root])
        self.assertFalse(errors);self.assertEqual(len(nodes),2);self.assertEqual(len(groups),1)
    def test_zstd_command_fallback_checks_parent_prefix(self):
        if not shutil.which('zstd'):self.skipTest('zstd command is not installed')
        p=self.rollout(A);self.rollout(B,base=self.base(p,A),ordinal=1);self.compress(p)
        with patch.object(lineage,'zstd_open',None):
            self.assertFalse(lineage.validate([self.root])[2])
    def test_missing_decoder_fails_closed(self):
        p=self.rollout(A);p.rename(p.with_suffix('.jsonl.zst'))
        with patch.object(lineage,'zstd_open',None),patch.object(lineage.shutil,'which',return_value=None):
            nodes,_,errors=lineage.validate([self.root])
        self.assertFalse(nodes);self.assertIn('Compressed history requires',' '.join(errors))
    def test_invalid_compressed_file_is_not_silently_skipped(self):
        p=self.rollout(A);p.with_suffix('.jsonl.zst').write_bytes(b'not a zstd frame');p.unlink()
        self.assertTrue(lineage.validate([self.root])[2])
    def test_plain_and_compressed_representations_are_one_rollout(self):
        p=self.rollout(A);data=p.read_bytes();self.compress(p);p.write_bytes(data)
        nodes,_,errors=lineage.validate([self.root])
        self.assertFalse(errors);self.assertEqual(len(nodes),1)
    def test_compressed_prefix_boundary_and_range_are_checked(self):
        p=self.rollout(A);base=self.base(p,A);self.compress(p)
        bad=dict(base);bad['end_byte_offset']-=1
        self.rollout(B,base=bad,ordinal=1)
        self.assertIn('boundary',' '.join(lineage.validate([self.root])[2]))
        bad['end_byte_offset']=base['end_byte_offset']+100000
        self.rollout(B,base=bad,ordinal=1)
        self.assertIn('outside source',' '.join(lineage.validate([self.root])[2]))
    def test_reader_accepts_compression_after_discovery(self):
        p=self.rollout(A);base=self.base(p,A);self.compress(p)
        self.assertEqual(lineage.prefix_record(p,base['end_byte_offset'])['ordinal'],0)

if __name__=='__main__':unittest.main()
