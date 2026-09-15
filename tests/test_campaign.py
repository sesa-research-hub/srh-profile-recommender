import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from srh.recommendation import campaign


class CampaignTests(unittest.TestCase):
    def run_campaign(self, fail_variant=False):
        source = {'Id': 'srh-unit-test', 'State': {'Running': True}, 'Image': 'sha256:test',
                  'Config': {'Cmd': ['model', '--max-num-batched-tokens', '8192'], 'Env': ['TEST=1']},
                  'HostConfig': {'NetworkMode': 'bridge', 'PortBindings': {
                      '8000/tcp': [{'HostIp': '', 'HostPort': '18300'}]}}}
        original = copy.deepcopy(source)
        calls, measurements = [], []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / 'srh/experiments/results'
            results.mkdir(parents=True)
            out = root / 'campaign'

            def fake_docker(method, path, body=None):
                calls.append((method, path, copy.deepcopy(body)))
                if method == 'GET':
                    return source
                if '/create?' in path:
                    return {'Id': f'variant-{len(calls)}'}
                return None

            def fake_run(*args, **kwargs):
                measurements.append(args)
                if fail_variant and len(measurements) == 2:
                    raise subprocess.CalledProcessError(1, args[0])
                (results / f'paired-{len(measurements)}.json').write_text('{}')

            argv = ['campaign', 'plan.json', 'baseline-p50-c1', 'pack', '--execute', '--output-dir', str(out)]
            with patch.object(campaign, '__file__', str(root / 'srh/recommendation/campaign.py')), \
                 patch.object(campaign, 'docker', side_effect=fake_docker), \
                 patch.object(campaign, 'ready'), patch.object(campaign.subprocess, 'run', side_effect=fake_run), \
                 patch.object(campaign.signal, 'signal'), patch('sys.argv', argv), patch('builtins.print'):
                if fail_variant:
                    with self.assertRaises(subprocess.CalledProcessError):
                        campaign.main()
                else:
                    campaign.main()
            journal = json.loads((out / 'campaign.json').read_text())
            self.assertTrue(journal['restored'])
            self.assertEqual((out / 'source-container.private.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(source, original)
        self.assertEqual(calls[-1][:2], ('POST', '/containers/srh-unit-test/start'))
        self.assertFalse(any(method == 'DELETE' for method, _, _ in calls))
        return calls

    def test_restores_after_success(self):
        calls = self.run_campaign()
        configs = [body for _, path, body in calls if '/create?' in path]
        self.assertEqual([body['Cmd'][-1] for body in configs], ['4096', '16384'])
        self.assertTrue(all(body['Image'] == 'sha256:test' for body in configs))

    def test_restores_after_measurement_failure(self):
        self.run_campaign(fail_variant=True)
