#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sesa Research Hub
"""Local, dependency-free meeting interface for SRH Capacity Planner."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .catalog import load_catalogs
from .planner import CapacityPlanningError, build_capacity_plan
from ..recommendation.deployment import recommend_deployment


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
EXAMPLE = ROOT / "examples" / "construction-tenders-intake.json"
MEASURED_DEMO = ROOT / "examples" / "qwen-measured-demo"
MEASURED_DEMO_MANIFEST = ROOT / "examples" / "qwen-measured-demo-manifest.json"
MEASURED_DEMO_CONTRACT = ROOT.parent / "workloads" / "examples" / "document-rag-sme.json"
MEASURED_DEMO_EVIDENCE = ("baseline-8192.json", "prefill-4096.json", "prefill-16384.json")
MAX_BODY_BYTES = 32 * 1024 * 1024
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/measured.css": ("measured.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class CapacityHandler(BaseHTTPRequestHandler):
    server_version = "SRHCapacity/0.1"

    def _headers(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8")
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {"status": "ok"})
            return
        if path == "/api/catalog":
            self._json(200, load_catalogs())
            return
        if path == "/api/example":
            self._json(200, json.loads(EXAMPLE.read_text(encoding="utf-8")))
            return
        if path == "/api/measured-demo":
            self._json(200, {
                "notice": "Previously observed SRH evidence supplied only to exercise the interface; it is not evidence for the current client workload.",
                "contract": json.loads(MEASURED_DEMO_CONTRACT.read_text(encoding="utf-8")),
                "manifest": json.loads(MEASURED_DEMO_MANIFEST.read_text(encoding="utf-8")),
                "evidences": [
                    {"source": name, "evidence": json.loads((MEASURED_DEMO / name).read_text(encoding="utf-8"))}
                    for name in MEASURED_DEMO_EVIDENCE
                ],
            })
            return
        item = STATIC.get(path)
        if item is None:
            self._json(404, {"error": "not found"})
            return
        filename, content_type = item
        body = (WEB / filename).read_bytes()
        self._headers(200, content_type)
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/api/plan", "/api/deployment-recommendation"}:
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid content length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "request body must be between 1 byte and 32 MiB"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise CapacityPlanningError("request root must be an object")
            if path == "/api/plan":
                self._json(200, build_capacity_plan(payload))
            else:
                contract = payload.get("contract")
                manifest = payload.get("manifest")
                raw_evidences = payload.get("evidences")
                if not isinstance(contract, dict) or not isinstance(manifest, dict) or not isinstance(raw_evidences, list):
                    raise CapacityPlanningError("contract, manifest and evidences are required")
                evidences = []
                for index, item in enumerate(raw_evidences):
                    if not isinstance(item, dict) or not isinstance(item.get("evidence"), dict):
                        raise CapacityPlanningError("each evidence item must contain a JSON evidence object")
                    evidences.append((str(item.get("source", f"upload-{index + 1}")), item["evidence"]))
                self._json(200, recommend_deployment(evidences, contract, manifest))
        except ValueError as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local SRH Capacity Planner meeting interface")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host; defaults to local-only 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the local interface in the default browser")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), CapacityHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"SRH Capacity Planner: {url}")
    print("Client inputs remain in browser memory unless the user downloads the dossier.")
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
