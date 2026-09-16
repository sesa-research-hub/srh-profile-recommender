import copy
import unittest

from srh.profiles.capture import canonical_sha256
from srh.recommendation.deployment import recommend_deployment
import test_recommender


class DeploymentRecommenderTests(unittest.TestCase):
    def setUp(self):
        factory = test_recommender.RecommenderTests()
        factory.setUp()
        self.contract = factory.contract
        self.factory = factory

    def observed(self, seq, hardware_name, model_name, ttfa):
        value = self.factory.evidence(seq, ttfa=ttfa)
        identity = value["runtime_profile"]["execution_identity"]
        identity["hardware"]["gpu"]["name"] = hardware_name
        identity["effective_runtime"]["model"]["snapshot"] = model_name
        identity["effective_runtime"]["model"]["served_names"] = [model_name]
        identity["container_image"]["image_id"] = "sha256:" + model_name
        value["runtime"]["model"] = model_name
        digest = canonical_sha256(identity)
        value["runtime_profile"]["profile_sha256"] = digest
        value["runtime_profile"]["profile_id"] = "srh-" + digest[:12]
        value["runtime_verification"]["end_profile_sha256"] = digest
        return value

    def manifest(self, rows, *, policy="cost_first", licenses=None, costs=None):
        licenses = licenses or ["approved"] * len(rows)
        costs = costs or [10000 + index * 1000 for index in range(len(rows))]
        return {
            "schema": "srh.deployment-candidates.v1",
            "policy": policy,
            "candidates": [
                {
                    "profile_id": row["runtime_profile"]["profile_id"],
                    "label": f"Candidate {index + 1}",
                    "license_review_status": licenses[index],
                    "estimated_three_year_cost_eur": costs[index],
                    "cost_provenance": "supplier_quote" if costs[index] is not None else None,
                    "device_power_w": 300 + index * 100,
                }
                for index, row in enumerate(rows)
            ],
        }

    def test_cross_hardware_and_model_comparison_is_explicit(self):
        rows = [
            self.observed(1, "GB10", "model-a", 1.5),
            self.observed(2, "H100", "model-b", 1.0),
        ]
        report = recommend_deployment([(str(i), value) for i, value in enumerate(rows)], self.contract, self.manifest(rows))
        self.assertTrue(report["comparable"])
        self.assertEqual(report["verdict"], "RECOMMENDED")
        self.assertEqual(report["recommended_profile_id"], rows[0]["runtime_profile"]["profile_id"])
        self.assertIn("hardware", report["comparison_scope"])

    def test_license_review_is_a_mandatory_gate(self):
        rows = [
            self.observed(1, "GB10", "model-a", 1.5),
            self.observed(2, "H100", "model-b", 1.0),
        ]
        manifest = self.manifest(rows, licenses=["approved", "review_required"], costs=[20000, 10000])
        report = recommend_deployment([(str(i), value) for i, value in enumerate(rows)], self.contract, manifest)
        self.assertEqual(report["recommended_profile_id"], rows[0]["runtime_profile"]["profile_id"])
        self.assertIn("license_review_status", report["candidates"][1]["blocking_objectives"])

    def test_cost_policy_requires_cost_for_each_eligible_candidate(self):
        rows = [
            self.observed(1, "GB10", "model-a", 1.5),
            self.observed(2, "H100", "model-b", 1.0),
        ]
        manifest = self.manifest(rows, costs=[10000, None])
        report = recommend_deployment([(str(i), value) for i, value in enumerate(rows)], self.contract, manifest)
        self.assertEqual(report["verdict"], "INSUFFICIENT_EVIDENCE")

    def test_scenario_mismatch_fails_comparability(self):
        rows = [
            self.observed(1, "GB10", "model-a", 1.5),
            self.observed(2, "H100", "model-b", 1.0),
        ]
        rows[1]["scenario"] = copy.deepcopy(rows[1]["scenario"])
        rows[1]["scenario"]["id"] = "other"
        report = recommend_deployment([(str(i), value) for i, value in enumerate(rows)], self.contract, self.manifest(rows))
        self.assertEqual(report["verdict"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("scenario", report["incompatible_fields"])


if __name__ == "__main__":
    unittest.main()
