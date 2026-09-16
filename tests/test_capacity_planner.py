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
        self.assertEqual(plan["projection_model"]["provenance"], "SRH_HEURISTIC")

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

    def test_shortlist_spans_hardware_when_feasible(self):
        plan = build_capacity_plan(self.intake)
        ids = set(plan["screening_summary"]["shortlist_candidate_ids"])
        rows = [row for row in plan["candidates"] if row["candidate_id"] in ids]
        self.assertEqual(
            len({row["hardware"]["id"] for row in rows}),
            min(len(rows), len(self.intake["exploration"]["hardware_ids"])),
        )
        feasible_models = {
            row["model"]["id"] for row in plan["candidates"]
            if row["screening_status"] != "NOT_FEASIBLE"
        }
        self.assertEqual({row["model"]["id"] for row in rows}, feasible_models)

    def test_invalid_meeting_input_fails_closed(self):
        intake = copy.deepcopy(self.intake)
        intake["traffic"]["concurrent_users"] = 20
        with self.assertRaises(CapacityPlanningError):
            build_capacity_plan(intake)

    def test_catalog_distinguishes_vendor_specs_and_srh_evidence(self):
        catalogs = load_catalogs()
        self.assertTrue(all(item["specification_provenance"]["type"] == "VENDOR_REPORTED" for item in catalogs["hardware"]["items"]))
        self.assertEqual(catalogs["evidence"]["items"][0]["provenance"], "SRH_MEASURED")

    def test_performance_coefficients_are_explicit_srh_assumptions(self):
        catalogs = load_catalogs()
        self.assertTrue(all(item["performance_assumptions"]["provenance"] == "SRH_HEURISTIC" for item in catalogs["hardware"]["items"]))


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


if __name__ == "__main__":
    unittest.main()
