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
        self.assertEqual(report["decision_scope"], "LAB_SYNTHETIC_SCENARIO")
        self.assertEqual(len(report["ranking"]), 2)

    def test_three_runs_remain_preliminary(self):
        self.assertEqual(self.compare(repetitions=3)["verdict"], "INSUFFICIENT_EVIDENCE")

    def test_license_is_a_hard_gate(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[0]["license_review_status"] = "review_required"
        report = self.compare(candidates=candidates)
        self.assertEqual(report["recommended_label"], "Beta")
        self.assertIn("license_review_status", report["candidates"][0]["blocking_objectives"])

    def test_cost_first_requires_cost_for_every_eligible_candidate(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[1]["estimated_three_year_cost_eur"] = None
        self.assertEqual(self.compare(candidates=candidates, policy="cost_first")["verdict"], "INSUFFICIENT_EVIDENCE")

    def test_declared_cost_requires_provenance(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[0].pop("cost_provenance")
        with self.assertRaisesRegex(ValueError, "cost provenance"):
            self.compare(candidates=candidates)

    def test_runtime_identity_is_rechecked_before_measurement(self):
        candidates = copy.deepcopy(self.candidates)
        candidates[1]["runtime_fingerprint"] = "missing"
        with self.assertRaisesRegex(ValueError, "no longer available"):
            self.compare(candidates=candidates)


if __name__ == "__main__":
    unittest.main()
