import unittest
from srh.experiments.paired_modes import decomposition, slo_assessment


class PairedEvidenceTests(unittest.TestCase):
    def test_failed_requests_preserve_reportable_timing(self):
        summary = {'ttfa_seconds': None, 'elapsed_seconds': None}
        result = decomposition(summary, summary)
        self.assertIsNone(result['comparison']['ttfa_delta_seconds'])
        self.assertIsNone(result['assistant']['end_to_end_seconds'])

    def test_empty_metrics_fail_slo(self):
        assessment = slo_assessment({}, {'ttfa_p95_ms': 2000, 'error_rate_max': .01}, {'minimum_score': .95})
        self.assertFalse(assessment['pass'])
        self.assertTrue(all(not check['pass'] for check in assessment['checks'].values()))
