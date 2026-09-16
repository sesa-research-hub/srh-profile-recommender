import copy
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import urllib.error
import urllib.request
import unittest

from srh.capacity.catalog import load_catalogs
from srh.capacity.planner import CapacityPlanningError, build_capacity_plan, translate_intake
from srh.capacity.server import CapacityHandler
from srh.workloads.contract import validate_contract


ROOT = Path(__file__).resolve().parents[1]


class CapacityPlannerTests(unittest.TestCase):
    def setUp(self):
        self.intake = json.loads(
            (ROOT / "srh/capacity/examples/construction-tenders-intake.json").read_text()
        )

    def test_business_inputs_translate_to_valid_contract(self):
        contract, trace = translate_intake(self.intake)
        errors, _ = validate_contract(contract)
        self.assertEqual(errors, [])
        self.assertEqual(contract["traffic"]["concurrent_users"], [1, 4])
        self.assertEqual(contract["request_profile"]["input_tokens"]["p50"], 5080)
        self.assertTrue(all(item["provenance"] in {"CALCULATED", "VALIDATION_WARNING"} for item in trace))

    def test_retrieval_context_is_distinct_from_document_size(self):
        retrieval, _ = translate_intake(self.intake)
        full = copy.deepcopy(self.intake)
        full["documents"]["context_mode"] = "full-document"
        full_contract, _ = translate_intake(full)
        self.assertLess(
            retrieval["request_profile"]["input_tokens"]["p95"],
            full_contract["request_profile"]["input_tokens"]["p95"],
        )

    def test_estimates_never_become_measured_recommendation(self):
        plan = build_capacity_plan(self.intake)
        self.assertEqual(plan["decision_boundary"]["status"], "PRELIMINARY_CAPACITY_SCREENING")
        self.assertFalse(plan["decision_boundary"]["compatible_with_profile_recommender"])
        self.assertTrue(plan["decision_boundary"]["measured_evidence_required"])
        self.assertTrue(all(row["final_recommendation_status"] == "MEASUREMENT_REQUIRED" for row in plan["candidates"]))
        self.assertTrue(plan["decision_boundary"]["compatible_with_deployment_recommender_after_measurement"])

    def test_dossier_hashes_inputs_and_declares_measurement_kpis(self):
        plan = build_capacity_plan(self.intake)
        self.assertEqual(len(plan["integrity"]["client_intake_sha256"]), 64)
        self.assertEqual(len(plan["integrity"]["translated_contract_sha256"]), 64)
        self.assertIn("critical_requirement_recall", plan["benchmark_handoff"]["measurement_kpis"]["domain_quality"])
        self.assertEqual(plan["projection_model"]["provenance"], "MIXED_VENDOR_ANCHOR_AND_SRH_HEURISTIC")

    def test_lower_precision_requires_less_capacity(self):
        plan = build_capacity_plan(self.intake)
        key = lambda bits: next(
            row for row in plan["candidates"]
            if row["hardware"]["id"] == "nvidia-gb10-128gb"
            and row["model"]["id"] == "dense-32b-class"
            and row["weight_bits"] == bits
        )
        self.assertLess(key(4)["capacity"]["estimated_total_required_gib"], key(8)["capacity"]["estimated_total_required_gib"])

    def test_power_limit_is_a_hard_screening_constraint(self):
        intake = copy.deepcopy(self.intake)
        intake["deployment"]["maximum_device_power_w"] = 300
        plan = build_capacity_plan(intake)
        rejected = [row for row in plan["candidates"] if row["hardware"]["power_w"] > 300]
        self.assertTrue(rejected)
        self.assertTrue(all("POWER_LIMIT_EXCEEDED" in row["blockers"] for row in rejected))

    def test_shortlist_never_trades_down_a_screening_tier_for_diversity(self):
        plan = build_capacity_plan(self.intake)
        ids = set(plan["screening_summary"]["shortlist_candidate_ids"])
        rows = [row for row in plan["candidates"] if row["candidate_id"] in ids]
        feasible_models = {
            row["model"]["id"] for row in plan["candidates"]
            if row["screening_status"] != "NOT_FEASIBLE"
        }
        self.assertEqual({row["model"]["id"] for row in rows}, feasible_models)
        rank = {"STRONG_FIT": 0, "CONDITIONAL_FIT": 1, "BORDERLINE_FIT": 2, "UNLIKELY_FIT": 3}
        for row in rows:
            best_rank = min(
                rank[value["screening_status"]]
                for value in plan["candidates"]
                if value["model"]["id"] == row["model"]["id"]
                and value["screening_status"] != "NOT_FEASIBLE"
            )
            self.assertEqual(rank[row["screening_status"]], best_rank)

    def test_screening_tiers_are_distinct_and_explainable(self):
        plan = build_capacity_plan(self.intake)
        rows = plan["candidates"]
        statuses = {row["screening_status"] for row in rows}
        self.assertIn("STRONG_FIT", statuses)
        self.assertTrue(statuses & {"BORDERLINE_FIT", "UNLIKELY_FIT"})
        for row in rows:
            self.assertIn("gates", row["assessment"])
            self.assertTrue(row["assessment"]["rationale"])

    def test_invalid_meeting_input_fails_closed(self):
        intake = copy.deepcopy(self.intake)
        intake["traffic"]["concurrent_users"] = 20
        with self.assertRaises(CapacityPlanningError):
            build_capacity_plan(intake)

    def test_catalog_distinguishes_vendor_specs_and_srh_evidence(self):
        catalogs = load_catalogs()
        self.assertTrue(all(item["specification_provenance"]["type"] == "VENDOR_REPORTED" for item in catalogs["hardware"]["items"]))
        self.assertEqual(catalogs["evidence"]["items"][0]["provenance"], "SRH_MEASURED")
        self.assertEqual(catalogs["hardware"]["version"], "1.1.0")

    def test_performance_coefficients_are_explicit_srh_assumptions(self):
        catalogs = load_catalogs()
        self.assertTrue(all(item["performance_assumptions"]["provenance"] == "SRH_HEURISTIC" for item in catalogs["hardware"]["items"]))

    def test_moe_projection_exposes_runtime_overhead_and_extra_uncertainty(self):
        intake = copy.deepcopy(self.intake)
        intake["exploration"]["model_ids"] = ["dense-3b-class", "moe-30b-3b-active-class"]
        plan = build_capacity_plan(intake)
        dense = next(row for row in plan["candidates"] if row["hardware"]["id"] == "nvidia-rtx-pro-6000-blackwell-96gb" and row["model"]["id"] == "dense-3b-class" and row["weight_bits"] == 4)
        moe = next(row for row in plan["candidates"] if row["hardware"]["id"] == "nvidia-rtx-pro-6000-blackwell-96gb" and row["model"]["id"] == "moe-30b-3b-active-class" and row["weight_bits"] == 4)
        self.assertGreater(moe["performance_projection"]["uncertainty_fraction"], dense["performance_projection"]["uncertainty_fraction"])
        self.assertGreater(moe["performance_projection"]["architecture_adjustment"]["effective_decode_weights_gib"], dense["performance_projection"]["architecture_adjustment"]["effective_decode_weights_gib"])
        self.assertGreater(moe["performance_projection"]["metrics"]["end_to_end_p95_ms"]["point"], dense["performance_projection"]["metrics"]["end_to_end_p95_ms"]["point"])
        self.assertEqual(moe["performance_projection"]["calibration_status"], "UNCALIBRATED_FOR_THIS_HARDWARE_MODEL_PAIR")

    def test_h100_dense_projection_scales_a_vendor_interactive_anchor(self):
        intake = copy.deepcopy(self.intake)
        intake["exploration"]["model_ids"] = ["dense-14b-class"]
        intake["exploration"]["weight_bits"] = [4]
        plan = build_capacity_plan(intake)
        row = next(value for value in plan["candidates"] if value["hardware"]["id"] == "nvidia-h100-sxm-80gb")
        projection = row["performance_projection"]
        self.assertEqual(projection["projection_method"], "VENDOR_INTERACTIVE_ANCHOR_SCALED")
        self.assertEqual(projection["assumption_provenance"], "VENDOR_BENCHMARK_SCALED")
        self.assertEqual(projection["metric_basis"]["decode"], "VENDOR_INTERACTIVE_DECODE_ANCHOR_SCALED")
        self.assertEqual(projection["metric_basis"]["ttfa"], "SRH_PREFILL_HEURISTIC")
        self.assertGreater(projection["metrics"]["answer_tokens_per_second_per_user"]["point"], 100)
        self.assertLess(projection["metrics"]["end_to_end_p95_ms"]["point"], 20000)

    def test_continuous_batching_retains_per_request_rate(self):
        intake = copy.deepcopy(self.intake)
        intake["traffic"]["concurrent_users"] = 1
        intake["exploration"]["model_ids"] = ["dense-8b-class"]
        intake["exploration"]["weight_bits"] = [4]
        single = build_capacity_plan(intake)
        intake["traffic"]["concurrent_users"] = 4
        concurrent = build_capacity_plan(intake)
        pick = lambda plan: next(value for value in plan["candidates"] if value["hardware"]["id"] == "nvidia-h100-sxm-80gb")
        single_rate = pick(single)["performance_projection"]["metrics"]["answer_tokens_per_second_per_user"]["point"]
        concurrent_rate = pick(concurrent)["performance_projection"]["metrics"]["answer_tokens_per_second_per_user"]["point"]
        self.assertGreater(concurrent_rate, single_rate * 0.85)
        self.assertLess(concurrent_rate, single_rate)


class QuietCapacityHandler(CapacityHandler):
    def log_message(self, fmt, *args):
        pass


class CapacityServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietCapacityHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health_and_capacity_api(self):
        with urllib.request.urlopen(self.base + "/health") as response:
            self.assertEqual(json.load(response)["status"], "ok")
        with urllib.request.urlopen(self.base + "/") as response:
            page = response.read()
        self.assertIn(b"Benchmark Lab", page)
        self.assertIn(b"recommendation-workflow", page)
        self.assertIn(b"consulting-report", page)
        with urllib.request.urlopen(self.base + "/responsive-tables.css") as response:
            self.assertEqual(response.headers.get_content_type(), "text/css")
            self.assertIn(b".comparison-table", response.read())
        with urllib.request.urlopen(self.base + "/workflow.js") as response:
            self.assertEqual(response.headers.get_content_type(), "text/javascript")
            workflow = response.read()
        self.assertIn(b"buildConsultingReport", workflow)
        self.assertIn(b"currentRecommendationIsForAssessment", workflow)
        with urllib.request.urlopen(self.base + "/workflow.css") as response:
            self.assertIn(b"@page", response.read())
        body = (ROOT / "srh/capacity/examples/construction-tenders-intake.json").read_bytes()
        request = urllib.request.Request(self.base + "/api/plan", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            plan = json.load(response)
        self.assertEqual(plan["schema"], "srh.capacity-plan.v1")
        self.assertTrue(plan["screening_summary"]["shortlist_candidate_ids"])

    def test_invalid_api_input_returns_400(self):
        request = urllib.request.Request(self.base + "/api/plan", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)

    def test_deployment_api_keeps_empty_observation_set_insufficient(self):
        intake = json.loads((ROOT / "srh/capacity/examples/construction-tenders-intake.json").read_text())
        contract, _ = translate_intake(intake)
        manifest = json.loads((ROOT / "srh/capacity/examples/deployment-candidates.example.json").read_text())
        body = json.dumps({"contract": contract, "manifest": manifest, "evidences": []}).encode()
        request = urllib.request.Request(
            self.base + "/api/deployment-recommendation",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            report = json.load(response)
        self.assertEqual(report["verdict"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(report["recommended_profile_id"])

    def test_bundled_measured_demo_runs_end_to_end(self):
        with urllib.request.urlopen(self.base + "/api/measured-demo") as response:
            demo = json.load(response)
        self.assertEqual(len(demo["evidences"]), 3)
        request = urllib.request.Request(
            self.base + "/api/deployment-recommendation",
            data=json.dumps({
                "contract": demo["contract"],
                "manifest": demo["manifest"],
                "evidences": demo["evidences"],
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            report = json.load(response)
        self.assertTrue(report["comparable"])
        self.assertEqual(len(report["candidates"]), 3)
        self.assertEqual(report["rejected_evidence"], [])
        self.assertEqual(report["verdict"], "NO_DEPLOYMENT_MEETS_REQUIREMENTS")

    def test_reference_model_api_preserves_non_measured_boundary(self):
        intake = json.loads((ROOT / "srh/capacity/examples/construction-tenders-intake.json").read_text())
        plan = build_capacity_plan(intake)
        request = urllib.request.Request(
            self.base + "/api/reference-simulation",
            data=json.dumps({
                "contract": plan["translated_workload_contract"],
                "model_id": "deepseek-v4-flash-0731",
                "hardware_ids": ["nvidia-gb10-128gb"],
                "weight_bits": 4,
                "maximum_power_w": 750,
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            report = json.load(response)
        self.assertEqual(report["schema"], "srh.reference-model-simulation.v1")
        self.assertFalse(report["evidence_boundary"]["deployment_recommendation"])
        self.assertEqual(report["model"]["source_type"], "VENDOR_MODEL_CARD")


if __name__ == "__main__":
    unittest.main()
