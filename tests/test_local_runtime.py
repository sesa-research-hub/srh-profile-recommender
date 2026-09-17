import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from srh.capacity.local_runtime import discover_local_models, discover_ollama_models, run_local_benchmark, validate_local_endpoint
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

    def test_ollama_discovery_keeps_identity_and_excludes_embeddings(self):
        class Response:
            def __init__(self, value): self.value = value
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self): return json.dumps(self.value).encode()
        tags = {"models": [
            {"name": "chat:latest", "digest": "abc123", "size": 42, "details": {"family": "qwen", "parameter_size": "8B", "quantization_level": "Q4_K_M"}},
            {"name": "nomic-embed-text:latest", "digest": "def456", "details": {"family": "nomic-bert"}},
        ]}
        running = {"models": [{"name": "chat:latest", "digest": "abc123"}]}
        from unittest.mock import patch
        with patch("srh.capacity.local_runtime.urllib.request.urlopen", side_effect=[Response(tags), Response(running)]):
            rows = discover_ollama_models()
        self.assertEqual([row["id"] for row in rows], ["chat:latest"])
        self.assertEqual(rows[0]["runtime_fingerprint"], "ollama:abc123")
        self.assertTrue(rows[0]["loaded"])

    def test_readiness_benchmark_scores_raw_local_response(self):
        answer = json.dumps({
            "deadline": "30/11/2026", "guarantee_percent": "2%",
            "inspection_deadline": "15/10/2026", "category": "OG1, classifica III",
            "citations": ["CAP-17", "CAP-22", "CAP-31", "CAP-44"],
        })

        def runner(endpoint, model, config, seed):
            return {"answer": answer, "ttfa_seconds": 1.0, "elapsed_seconds": 10.0,
                    "answer_tokens_per_second": 20.0, "answer_tokens": 100,
                    "prompt_tokens": 2000}

        report = run_local_benchmark(
            endpoint="http://127.0.0.1:18300/v1", model="test", contract=self.contract,
            profile="quick", repetitions=2, concurrency=2, runner=runner,
        )
        self.assertEqual(report["summary"]["request_count"], 4)
        self.assertEqual(report["summary"]["quality_pass_rate"], 1.0)
        self.assertEqual(report["summary"]["answer_tokens"]["p95"], 100.0)
        self.assertEqual(report["objective_summary"], {"met": 5, "total": 5})
        self.assertEqual(
            report["planning_bridge"]["generation_seconds_for_contract_p95_output_at_observed_min_rate"],
            self.contract["request_profile"]["output_tokens"]["p95"] / 20,
        )
        self.assertTrue(report["all_checks_pass"])

    def test_ollama_benchmark_preserves_digest_tuning_and_energy(self):
        answer = json.dumps({
            "deadline": "30/11/2026", "guarantee_percent": 2,
            "inspection_deadline": "15/10/2026", "category": "OG1 III",
            "citations": ["CAP-17", "CAP-22", "CAP-31", "CAP-44"],
        })
        runtime = {"id": "ollama-test", "label": "ollama-test", "provider": "ollama", "root": "ollama://ollama-test@sha256:abc", "runtime_fingerprint": "ollama:abc", "digest": "abc", "quantization": "Q4_K_M", "family": "test", "parameter_size": "8B", "maximum_context_tokens": None}
        shown = {"parameters": "temperature 0.2\ntop_k 40", "model_info": {"general.architecture": "test", "general.parameter_count": 8_000_000_000, "test.context_length": 32768}, "capabilities": ["completion"]}
        energy = {"supported": True, "average_power_w": 42.0, "energy_wh": 0.1}
        def runner(*_): return {"answer": answer, "ttfa_seconds": 1, "elapsed_seconds": 2, "answer_tokens_per_second": 30, "answer_tokens": 60, "prompt_tokens": 2000}
        from unittest.mock import patch
        with patch("srh.capacity.local_runtime.discover_ollama_models", return_value=[runtime]), patch("srh.capacity.local_runtime._ollama_show", return_value=shown), patch("srh.capacity.local_runtime._power_measurement", side_effect=lambda work: (work(), energy)):
            report = run_local_benchmark(endpoint="http://127.0.0.1:11434/v1", model="ollama-test", contract=self.contract, profile="quick", repetitions=1, concurrency=1, runner=runner)
        self.assertEqual(report["runtime_root"], runtime["root"])
        self.assertEqual(report["runtime_configuration"]["quantization"], "Q4_K_M")
        self.assertEqual(report["runtime_configuration"]["model_defaults"]["top_k"], "40")
        self.assertEqual(report["runtime_configuration"]["effective_test_protocol"]["top_k"], -1)
        self.assertEqual(report["energy_observation"]["average_power_w"], 42.0)

    def test_named_reference_preserves_source_and_fails_hard_capacity(self):
        report = simulate_reference_model(
            "deepseek-v4-pro", self.contract, ["nvidia-gb10-128gb"], 4, 750,
        )
        self.assertEqual(report["model"]["source_type"], "VENDOR_MODEL_CARD")
        self.assertEqual(report["candidates"][0]["screening_status"], "NOT_FEASIBLE")
        self.assertFalse(report["evidence_boundary"]["deployment_recommendation"])


if __name__ == "__main__":
    unittest.main()
