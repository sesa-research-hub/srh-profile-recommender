import copy
import json
from pathlib import Path
import unittest

from srh.recommendation.live import run_live_comparison


ROOT = Path(__file__).resolve().parents[1]


class LiveComparisonTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((ROOT / "srh/workloads/examples/document-rag-sme.json").read_text())
        self.runtimes = [
            {"id": "alpha", "label": "Alpha", "endpoint": "http://127.0.0.1:11434/v1", "provider": "ollama", "root": "ollama://alpha@sha256:aaa", "runtime_fingerprint": "ollama:aaa", "digest": "aaa", "quantization": "Q4"},
            {"id": "beta", "label": "Beta", "endpoint": "http://127.0.0.1:18300/v1", "provider": "vllm", "root": "/models/beta", "runtime_fingerprint": "/models/beta", "quantization": "FP8"},
        ]
        self.candidates = [
            {"runtime_fingerprint": "ollama:aaa", "label": "Alpha", "license_review_status": "approved", "estimated_three_year_cost_eur": 5000, "cost_provenance": "calculated"},
            {"runtime_fingerprint": "/models/beta", "label": "Beta", "license_review_status": "approved", "estimated_three_year_cost_eur": 7000, "cost_provenance": "supplier_quote"},
        ]

    def observation(self, model, *, ttfa=1.0, quality=1.0):
        passed = quality >= self.contract["quality"]["minimum_score"]
        return {
            "schema": "srh.local-readiness-benchmark.v1", "model": model, "profile": "representative", "concurrency": 1,
            "summary": {"quality_pass_rate": 1 if passed else 0, "ttfa_seconds": {"p95": ttfa}, "elapsed_seconds": {"p95": 10}, "answer_tokens_per_second": {"min": 30}, "quality_score": {"min": quality}},
            "checks": {
                "ttfa_p95": {"actual": ttfa, "target": 2, "pass": ttfa <= 2},
                "end_to_end_p95": {"actual": 10, "target": 45, "pass": True},
                "answer_speed_min": {"actual": 30, "target": 15, "pass": True},
                "error_rate": {"actual": 0, "target": .01, "pass": True},
                "quality_minimum": {"actual": quality, "target": .95, "pass": passed},
            },
        }

    def compare(self, **kwargs):
        def benchmark(**call): return self.observation(call["model"], ttfa=0.8 if call["model"] == "alpha" else 1.2)
        return run_live_comparison(
            contract=self.contract, candidates=kwargs.pop("candidates", self.candidates), profile="representative",
            repetitions=kwargs.pop("repetitions", 5), concurrency=1, policy=kwargs.pop("policy", "performance_first"),
            discovery=lambda: self.runtimes, benchmark=benchmark, **kwargs,
        )

    def test_five_run_campaign_recommends_best_eligible_runtime(self):
        report = self.compare()
        self.assertEqual(report["verdict"], "RECOMMENDED")
        self.assertEqual(report["recommended_label"], "Alpha")
        self.assertEqual(report["closest_candidate_label"], "Alpha")
        self.assertEqual(report["decision_scope"], "LAB_SYNTHETIC_SCENARIO")
        self.assertEqual(len(report["ranking"]), 2)

    def test_three_runs_remain_preliminary(self):
        self.assertEqual(self.compare(repetitions=3)["verdict"], "INSUFFICIENT_EVIDENCE")

    def test_commercial_metadata_does_not_block_local_performance_result(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[0]["license_review_status"] = "review_required"
        report = self.compare(candidates=candidates)
        self.assertEqual(report["recommended_label"], "Alpha")
        self.assertNotIn("license_review_status", report["candidates"][0]["blocking_objectives"])

    def test_live_comparison_rejects_cost_first_policy(self):
        with self.assertRaisesRegex(ValueError, "performance_first"):
            self.compare(policy="cost_first")

    def test_declared_cost_does_not_change_performance_ranking(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[0].pop("cost_provenance")
        candidates[0]["estimated_three_year_cost_eur"] = 999999
        self.assertEqual(self.compare(candidates=candidates)["recommended_label"], "Alpha")

    def test_runtime_identity_is_rechecked_before_measurement(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[1]["runtime_fingerprint"] = "missing"
        with self.assertRaisesRegex(ValueError, "no longer available"):
            self.compare(candidates=candidates)


if __name__ == "__main__":
    unittest.main()
