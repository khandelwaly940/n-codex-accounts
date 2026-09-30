"""Offline checks for physical rollout references and same-chat continuation."""
import importlib.util
import json
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

if __name__=='__main__':unittest.main()
