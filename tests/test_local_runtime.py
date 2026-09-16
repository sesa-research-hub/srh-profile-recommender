import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from srh.capacity.local_runtime import discover_local_models, run_local_benchmark, validate_local_endpoint
from srh.capacity.reference import simulate_reference_model
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ModelHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"data": [{"id": "local-test", "owned_by": "test", "max_model_len": 8192}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class LocalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.contract = json.loads((ROOT / "srh/workloads/examples/document-rag-sme.json").read_text())

    def test_discovery_reads_openai_compatible_local_endpoint(self):
        endpoint = f"http://127.0.0.1:{self.server.server_port}/v1"
        rows = discover_local_models((endpoint,))
        self.assertEqual(rows[0]["id"], "local-test")
        self.assertTrue(rows[0]["can_run_now"])

    def test_external_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_local_endpoint("https://example.com/v1")

    def test_readiness_benchmark_scores_raw_local_response(self):
        answer = json.dumps({
            "deadline": "30/11/2026", "guarantee_percent": "2%",
            "inspection_deadline": "15/10/2026", "category": "OG1, classifica III",
            "citations": ["CAP-17", "CAP-22", "CAP-31", "CAP-44"],
        })

        def runner(endpoint, model, config, seed):
            return {"answer": answer, "ttfa_seconds": 1.0, "elapsed_seconds": 10.0,
                    "answer_tokens_per_second": 20.0, "prompt_tokens": 2000}

        report = run_local_benchmark(
            endpoint="http://127.0.0.1:18300/v1", model="test", contract=self.contract,
            profile="quick", repetitions=2, concurrency=2, runner=runner,
        )
        self.assertEqual(report["summary"]["request_count"], 4)
        self.assertEqual(report["summary"]["quality_pass_rate"], 1.0)
        self.assertTrue(report["all_checks_pass"])

    def test_named_reference_preserves_source_and_fails_hard_capacity(self):
        report = simulate_reference_model(
            "deepseek-v4-pro", self.contract, ["nvidia-gb10-128gb"], 4, 750,
        )
        self.assertEqual(report["model"]["source_type"], "VENDOR_MODEL_CARD")
        self.assertEqual(report["candidates"][0]["screening_status"], "NOT_FEASIBLE")
        self.assertFalse(report["evidence_boundary"]["deployment_recommendation"])


if __name__ == "__main__":
    unittest.main()
