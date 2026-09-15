import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('publish_release', Path(__file__).resolve().parents[1] / 'scripts/publish_srh_release.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FakeAPI:
    def __init__(self, head='source', status='ahead', tree='tree', merged=False, conflict=False):
        self.head, self.status, self.tree = head, status, tree
        self.merged, self.conflict = merged, conflict
        self.writes = []

    def request(self, method, path, data=None):
        pr = {'number': 1, 'html_url': 'https://example.test/pr', 'state': 'open', 'head': {'sha': 'source', 'repo': {'full_name': mod.REPO}}, 'merged_at': 'today' if self.merged else None, 'merge_commit_sha': 'merge'}
        if method != 'GET':
            self.writes.append((method, path, data))
        if path.startswith('/commits/'): return {'sha': self.head}
        if path.startswith('/pulls?'): return [pr] if self.merged else []
        if path.startswith('/compare/'): return {'status': self.status}
        if path == '/pulls': return pr
        if method == 'PUT' and path.endswith('/merge'): return {'merged': True, 'sha': 'merge'}
        if path.startswith('/git/commits/'): return {'tree': {'sha': self.tree}}
        if path.startswith('/git/ref/tags/'): return {'object': {'type': 'commit', 'sha': 'other' if self.conflict else 'merge'}} if self.merged or self.conflict else None
        if path == '/git/refs': return {}
        if path.startswith('/releases/tags/'): return {'prerelease': True, 'draft': False, 'html_url': 'https://example.test/release'} if self.merged else None
        if path == '/releases': return {'html_url': 'https://example.test/release'}
        raise AssertionError(path)

class PublishingTests(unittest.TestCase):
    def run_publish(self, api):
        mod.publish(api, 'source', 'tree', 'PR body', 'Release body')

    def test_remote_mismatch_prevents_mutations(self):
        api = FakeAPI(head='unexpected')
        with self.assertRaises(RuntimeError): self.run_publish(api)
        self.assertEqual(api.writes, [])

    def test_divergence_prevents_mutations(self):
        api = FakeAPI(status='diverged')
        with self.assertRaises(RuntimeError): self.run_publish(api)
        self.assertEqual(api.writes, [])

    def test_publish_pins_sha_and_creates_prerelease(self):
        api = FakeAPI()
        self.run_publish(api)
        self.assertEqual(api.writes[1][2]['sha'], 'source')
        self.assertEqual(api.writes[2][2]['sha'], 'merge')
        self.assertTrue(api.writes[3][2]['prerelease'])

    def test_recovery_reuses_completed_release(self):
        api = FakeAPI(merged=True)
        self.run_publish(api)
        self.assertEqual(api.writes, [])

    def test_tree_mismatch_prevents_tag(self):
        api = FakeAPI(tree='unexpected')
        with self.assertRaises(RuntimeError): self.run_publish(api)
        self.assertFalse(any(path in ('/git/refs', '/releases') for _, path, _ in api.writes))

    def test_tag_conflict_never_overwrites(self):
        api = FakeAPI(merged=True, conflict=True)
        with self.assertRaises(RuntimeError): self.run_publish(api)
        self.assertEqual(api.writes, [])
