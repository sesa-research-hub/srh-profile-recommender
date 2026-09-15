import copy
import json
import hashlib
from pathlib import Path
import unittest

from srh.profiles.capture import canonical_sha256
from srh.recommendation.engine import recommend
from srh.workloads.planner import build_plan

ROOT = Path(__file__).resolve().parents[1]


class RecommenderTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / 'srh/workloads/examples/document-rag-sme.json').read_text())

    def evidence(self, seq, ttfa=1, quality=1):
        # Synthetic fixtures only; these are never deployment evidence.
        identity = {'hardware': {'architecture': 'test', 'gpu': {'name': 'test', 'driver': 'test'}}, 'container_image': {'image_id': 'sha256:test'},
                    'effective_runtime': {'engine': 'test', 'process_environment': {}, 'model': {'snapshot': 'test', 'served_names': ['test']},
                                          'scheduler': {'max_num_seqs': seq}}}
        digest = canonical_sha256(identity)
        truth = {'schema': 'srh.scenario-ground-truth.v1', 'expected': {
            'order': 'A102', 'activity': 'test', 'evidence_source_id': 'SRC-1'}}
        answer = 'SRH_RESULT: ' + json.dumps(truth['expected']) if quality == 1 else 'incorrect'
        batches = [{'repetition': i, 'concurrency': 1, 'cache_state': 'warm', 'batch_elapsed_seconds': 10,
                    'requests': [{'request_no': 1, 'success': True, 'answer': answer,
                                  'performance': {'ttfa_seconds': ttfa, 'elapsed_seconds': 10,
                                                  'answer_tokens_per_second': 20, 'completion_tokens': 100,
                                                  'finish_reason': 'stop'}}]}
                   for i in range(1, 6)]
        return {'batches': {'assistant': batches, 'probe': copy.deepcopy(batches)}, 'schema': 'srh.paired-response-experiment.v1', 'workload': {'id': self.contract['id']},
                'runtime_profile': {'execution_identity': identity, 'profile_sha256': digest,
                                    'profile_id': 'srh-' + digest[:12],
                                    'provenance': {'source': 'live-runtime', 'effective_process_observed': True}},
                'runtime_verification': {'stable': True, 'end_profile_sha256': digest},
                'runtime': {'model': 'test'}, 'experiment': build_plan(self.contract)['experiments'][0], 'scenario': {'id': 'test'},
                'protocol': {'type': 'paired-interleaved', 'repetitions': 5, 'reasoning_policy': 'disabled', 'resolved_model_mode': 'no-think'},
                'comparison': {'ground_truth': truth, 'evaluator_sha256': hashlib.sha256((ROOT / 'srh/scenarios/run_pack.py').read_bytes()).hexdigest(), 'pack_sha256': 'a' * 64, 'executor_version': 'test', 'seed': 42, 'response_contract': self.contract['response_contract'], 'quality_requirements': self.contract['quality']},
                'summaries': {'assistant': {'ttfa_seconds': {'p95': ttfa}, 'elapsed_seconds': {'p95': 10},
                                           'answer_tokens_per_second': {'min': 20}, 'request_count': 5,
                                           'failed_request_count': 0, 'quality': {'score_min': quality}}}}

    def run_report(self, rows):
        return recommend([(str(i), r) for i, r in enumerate(rows)], self.contract)

    def test_quality_before_speed(self):
        rows = [self.evidence(1, .1, .8), self.evidence(2, 1.5), self.evidence(3, 1)]
        report = self.run_report(rows)
        self.assertEqual(report['verdict'], 'RECOMMENDED')
        self.assertEqual(report['recommended_profile_id'], rows[2]['runtime_profile']['profile_id'])

    def test_stored_summary_cannot_override_raw_quality(self):
        rows = [self.evidence(i, 1, .8) for i in range(3)]
        for row in rows:
            row['summaries']['assistant']['quality']['score_min'] = 1
        self.assertEqual(self.run_report(rows)['verdict'], 'NO_PROFILE_MEETS_SLO')

    def test_required_citation_cannot_be_averaged_away(self):
        self.contract['quality']['minimum_score'] = .5
        rows = [self.evidence(i) for i in range(3)]
        for row in rows:
            for batch in row['batches']['assistant']:
                batch['requests'][0]['answer'] = 'SRH_RESULT: ' + json.dumps({
                    'order': 'A102', 'activity': 'test', 'evidence_source_id': 'WRONG'})
        report = self.run_report(rows)
        self.assertEqual(report['verdict'], 'NO_PROFILE_MEETS_SLO')
        self.assertIn('required_quality_checks', report['candidates'][0]['blocking_objectives'])

    def test_prefix_control_preserves_baseline_shape(self):
        plan = build_plan(self.contract)
        baseline = plan['experiments'][0]
        control = next(e for e in plan['experiments'] if e['experiment_id'] == 'shared-prefix-p50-c1')
        self.assertEqual(control['request'], baseline['request'])
        self.assertEqual(control['concurrency'], baseline['concurrency'])
        self.assertEqual(control['cache_state'], 'shared-prefix')

    def test_complete_failed_campaign_is_negative_evidence(self):
        rows = [self.evidence(i) for i in range(3)]
        for row in rows:
            for mode in ('probe', 'assistant'):
                for batch in row['batches'][mode]:
                    batch['requests'][0]['success'] = False
        report = self.run_report(rows)
        self.assertEqual(report['verdict'], 'NO_PROFILE_MEETS_SLO')
        self.assertEqual(report['candidates'][0]['checks']['error_rate_max']['actual'], 1.0)

    def test_no_profile_passes(self):
        self.assertEqual(self.run_report([self.evidence(i, 5) for i in range(3)])['verdict'], 'NO_PROFILE_MEETS_SLO')

    def test_insufficient(self):
        self.assertEqual(self.run_report([self.evidence(1)])['verdict'], 'INSUFFICIENT_EVIDENCE')

    def test_unsafe_evidence(self):
        for mutation in ['hash', 'cache', 'missing', 'nan', 'drift', 'duplicate', 'quality_policy', 'missing_sample', 'truncated', 'reasoning']:
            with self.subTest(mutation=mutation):
                rows = [self.evidence(i) for i in range(3)]
                if mutation == 'hash': rows[0]['runtime_profile']['profile_sha256'] = 'wrong'
                if mutation == 'cache': rows[0]['experiment']['cache_state'] = 'shared-prefix'
                if mutation == 'missing': del rows[0]['runtime_profile']
                if mutation == 'nan': rows[0]['batches']['assistant'][0]['requests'][0]['performance']['ttfa_seconds'] = float('nan')
                if mutation == 'drift': rows[0]['runtime_verification']['stable'] = False
                if mutation == 'duplicate': rows[0] = copy.deepcopy(rows[1])
                if mutation == 'missing_sample': rows[0]['batches']['assistant'].pop()
                if mutation == 'reasoning': rows[0]['protocol']['resolved_model_mode'] = 'think'
                if mutation == 'truncated':
                    for row in rows:
                        row['batches']['assistant'][0]['requests'][0]['performance']['finish_reason'] = 'length'
                    self.assertEqual(self.run_report(rows)['verdict'], 'NO_PROFILE_MEETS_SLO')
                    continue
                if mutation == 'quality_policy': rows[0]['comparison']['quality_requirements'] = {}
                self.assertEqual(self.run_report(rows)['verdict'], 'INSUFFICIENT_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
