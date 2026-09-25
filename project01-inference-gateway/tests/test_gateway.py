"""Behavioral checks for the local inference gateway.

Run from the project directory with ``python -m unittest discover -s tests -v``.
All HTTP traffic stays on an ephemeral loopback port; no model download is needed.
"""

import copy
import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from gateway.adapters import BackendFailure, generate
from gateway.config import BackendConfig, GatewayConfig, load_config
from gateway.http import create_server
from gateway.mock_worker import WorkerState, create_worker_server
from gateway.rate_limit import TokenBucket
from gateway.registry import BackendRegistry


def config_dict(backends=None, **overrides):
    raw = {
        "host": "127.0.0.1",
        "port": 0,
        "request_timeout_ms": 1500,
        "max_body_bytes": 4096,
        "max_prompt_chars": 200,
        "max_tokens": 32,
        "rate_per_minute": 120,
        "rate_burst": 5,
        "failure_threshold": 1,
        "circuit_cooldown_ms": 500,
        "backends": backends or [{
            "name": "local",
            "url": "http://127.0.0.1:1",
            "adapter": "contract",
            "models": ["demo"],
            "capacity": 2,
            "weight": 1,
            "timeout_ms": 500,
        }],
    }
    raw.update(overrides)
    return raw


def load_raw_config(raw):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "gateway.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return load_config(path)


def start_server(test, server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    test.addCleanup(thread.join, 2)
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


def post_json(server, payload, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    try:
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        data = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
        connection.request("POST", "/v1/infer", body=data, headers=request_headers)
        response = connection.getresponse()
        body = response.read()
        return response.status, dict(response.getheaders()), json.loads(body)
    finally:
        connection.close()


def get(server, path):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        return response.status, body.decode("utf-8")
    finally:
        connection.close()


def start_reply_server(test, reply):
    """Start a backend with a fixed, intentionally malformed or oversized reply."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    return start_server(test, ThreadingHTTPServer(("127.0.0.1", 0), Handler))


class ConfigTests(unittest.TestCase):
    def test_valid_config_preserves_backend_contract(self):
        config = load_raw_config(config_dict())
        self.assertEqual(config.port, 0)
        self.assertEqual(config.backends[0].models, ("demo",))
        self.assertEqual(config.backends[0].adapter, "contract")

    def test_rejects_unsafe_or_ambiguous_settings(self):
        cases = {
            "public_bind": ("host", "0.0.0.0"),
            "boolean_port": ("port", True),
            "zero_timeout": ("request_timeout_ms", 0),
            "no_backends": ("backends", []),
        }
        for name, (field, value) in cases.items():
            with self.subTest(name=name):
                raw = config_dict()
                raw[field] = value
                with self.assertRaises(ValueError):
                    load_raw_config(raw)

    def test_rejects_duplicate_names_and_invalid_backend_values(self):
        base = config_dict()["backends"][0]
        bad_values = {
            "unsupported_adapter": ("adapter", "mystery"),
            "empty_models": ("models", []),
            "boolean_capacity": ("capacity", True),
            "negative_weight": ("weight", -1),
            "non_http_url": ("url", "file:///tmp/model"),
        }
        for name, (field, value) in bad_values.items():
            with self.subTest(name=name):
                backend = copy.deepcopy(base)
                backend[field] = value
                with self.assertRaises(ValueError):
                    load_raw_config(config_dict(backends=[backend]))
        with self.assertRaises(ValueError):
            load_raw_config(config_dict(backends=[base, copy.deepcopy(base)]))


class TokenBucketTests(unittest.TestCase):
    def test_burst_refill_and_tenant_isolation(self):
        now = [100.0]
        bucket = TokenBucket(rate_per_minute=60, burst=2, clock=lambda: now[0])
        self.assertEqual(bucket.allow("alice"), (True, 0.0))
        self.assertEqual(bucket.allow("alice"), (True, 0.0))
        allowed, retry = bucket.allow("alice")
        self.assertFalse(allowed)
        self.assertAlmostEqual(retry, 1.0)
        self.assertEqual(bucket.allow("bob"), (True, 0.0))
        now[0] += 0.5
        self.assertAlmostEqual(bucket.allow("alice")[1], 0.5)
        now[0] += 0.5
        self.assertEqual(bucket.allow("alice"), (True, 0.0))

    def test_backward_clock_does_not_create_tokens(self):
        now = [100.0]
        bucket = TokenBucket(rate_per_minute=60, burst=1, clock=lambda: now[0])
        self.assertTrue(bucket.allow("tenant")[0])
        now[0] -= 10
        self.assertFalse(bucket.allow("tenant")[0])


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.backend = BackendConfig("worker", "http://127.0.0.1:1", "contract", ("demo",), 1, 1.0, 500)
        self.registry = BackendRegistry((self.backend,), failure_threshold=2, cooldown_ms=1000,
                                        clock=lambda: self.now)

    def test_capacity_and_reservation_release(self):
        self.assertEqual(self.registry.reserve("demo", set()).name, "worker")
        self.assertIsNone(self.registry.reserve("demo", set()))
        self.assertIsNone(self.registry.reserve("other", set()))
        self.registry.finish("worker", success=True, elapsed_ms=20)
        self.assertEqual(self.registry.snapshot()[0]["inflight"], 0)
        self.assertAlmostEqual(self.registry.snapshot()[0]["ewma_ms"], 41.0)
        self.assertIsNotNone(self.registry.reserve("demo", set()))
        self.registry.finish("worker", success=True, elapsed_ms=20)

    def test_circuit_opens_then_allows_one_probe_and_recovers(self):
        for expected_failures in (1, 2):
            self.assertIsNotNone(self.registry.reserve("demo", set()))
            self.registry.finish("worker", success=False, elapsed_ms=10)
            self.assertEqual(self.registry.snapshot()[0]["failures"], expected_failures)
        self.assertEqual(self.registry.snapshot()[0]["circuit"], "open")
        self.assertIsNone(self.registry.reserve("demo", set()))
        self.now += 1.0
        self.assertIsNotNone(self.registry.reserve("demo", set()))
        self.assertEqual(self.registry.snapshot()[0]["circuit"], "half_open")
        self.assertIsNone(self.registry.reserve("demo", set()))
        self.registry.finish("worker", success=True, elapsed_ms=8)
        self.assertEqual(self.registry.snapshot()[0]["circuit"], "closed")
        self.assertEqual(self.registry.snapshot()[0]["failures"], 0)

    def test_failed_probe_reopens_circuit(self):
        self.assertIsNotNone(self.registry.reserve("demo", set()))
        self.registry.finish("worker", success=False, elapsed_ms=10)
        self.assertIsNotNone(self.registry.reserve("demo", set()))
        self.registry.finish("worker", success=False, elapsed_ms=10)
        self.now += 1.0
        self.assertIsNotNone(self.registry.reserve("demo", set()))
        self.registry.finish("worker", success=False, elapsed_ms=10)
        self.assertEqual(self.registry.snapshot()[0]["circuit"], "open")
        self.assertIsNone(self.registry.reserve("demo", set()))

    def test_readiness_returns_after_cooldown_without_traffic(self):
        for _ in range(2):
            self.assertIsNotNone(self.registry.reserve("demo", set()))
            self.registry.finish("worker", success=False, elapsed_ms=10)
        self.assertFalse(self.registry.any_ready())
        self.now += 1.0
        self.assertTrue(self.registry.any_ready())

    def test_weight_preference_yields_to_measured_latency(self):
        fast_weight = BackendConfig("weighted", "http://127.0.0.1:1", "contract",
                                    ("demo",), 1, 2.0, 500)
        normal = BackendConfig("normal", "http://127.0.0.1:2", "contract",
                               ("demo",), 1, 1.0, 500)
        registry = BackendRegistry((normal, fast_weight), failure_threshold=2,
                                   cooldown_ms=1000, clock=lambda: self.now)
        first = registry.reserve("demo", set())
        self.assertEqual(first.name, "weighted")
        registry.finish(first.name, success=True, elapsed_ms=1000)
        second = registry.reserve("demo", set())
        self.assertEqual(second.name, "normal")
        registry.finish(second.name, success=True, elapsed_ms=10)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.worker = start_server(self, create_worker_server("127.0.0.1", 0, WorkerState("local", 0)))
        url = "http://127.0.0.1:%d" % self.worker.server_address[1]
        backend = config_dict()["backends"][0]
        backend["url"] = url
        self.gateway = start_server(self, create_server(load_raw_config(config_dict(backends=[backend]))))

    def test_rejects_invalid_request_fields_before_routing(self):
        valid = {"model": "demo", "prompt": "hello", "max_tokens": 2}
        cases = [
            ({"model": "demo", "prompt": "", "max_tokens": 2}, {}, 400, "invalid_prompt"),
            ({"model": "demo", "prompt": "hello", "max_tokens": True}, {}, 400, "invalid_max_tokens"),
            ({"model": "demo", "prompt": "hello", "max_tokens": 33}, {}, 400, "invalid_max_tokens"),
            (valid, {"X-Tenant-ID": "space tenant"}, 400, "invalid_tenant"),
            (b"{bad-json", {}, 400, "invalid_json"),
            (valid, {"Content-Type": "text/plain"}, 415, "unsupported_media_type"),
        ]
        for payload, headers, expected_status, expected_code in cases:
            with self.subTest(code=expected_code):
                status, _, body = post_json(self.gateway, payload, headers)
                self.assertEqual(status, expected_status)
                self.assertEqual(body["error"]["code"], expected_code)
                self.assertEqual(len(body["request_id"]), 32)
        self.assertEqual(self.gateway.service.registry.snapshot()[0]["inflight"], 0)

    def test_success_response_health_and_metrics(self):
        status, _, body = post_json(self.gateway, {"model": "demo", "prompt": "one two", "max_tokens": 1})
        self.assertEqual(status, 200)
        self.assertEqual(body["output"], "[simulated completion] one")
        self.assertEqual(body["backend"], "local")
        self.assertEqual(body["attempts"], ["local"])
        self.assertEqual(body["usage"], {"input_tokens": 2, "output_tokens": 1})
        status, health = get(self.gateway, "/healthz")
        self.assertEqual((status, json.loads(health)), (200, {"status": "alive"}))
        status, metrics = get(self.gateway, "/metrics")
        self.assertEqual(status, 200)
        self.assertIn('gateway_requests_total{outcome="success"} 1', metrics)
        self.assertIn('gateway_backend_attempts_total{backend="local",result="success"} 1', metrics)

    def test_readiness_recovers_after_circuit_cooldown_before_probe(self):
        registry = self.gateway.service.registry
        self.assertEqual(get(self.gateway, "/readyz")[0], 200)
        backend = registry.reserve("demo", set())
        self.assertIsNotNone(backend)
        registry.finish(backend.name, success=False, elapsed_ms=1)
        status, body = get(self.gateway, "/readyz")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["status"], "unavailable")
        registry.clock = lambda: time.monotonic() + 1.0
        status, body = get(self.gateway, "/readyz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ready")
        self.assertEqual(registry.snapshot()[0]["circuit"], "open")

    def test_unknown_model_has_explicit_error(self):
        status, _, body = post_json(self.gateway, {"model": "missing", "prompt": "hello", "max_tokens": 2})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "unknown_model")

    def test_default_max_tokens_respects_configured_cap(self):
        status, _, body = post_json(self.gateway, {"model": "demo", "prompt": "hello"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["output"], "[simulated completion] hello")

    def test_rate_limit_isolated_by_tenant(self):
        self.gateway.service.rate_limit = TokenBucket(rate_per_minute=1, burst=1)
        payload = {"model": "demo", "prompt": "hello", "max_tokens": 2}
        self.assertEqual(post_json(self.gateway, payload, {"X-Tenant-ID": "alice"})[0], 200)
        status, headers, body = post_json(self.gateway, payload, {"X-Tenant-ID": "alice"})
        self.assertEqual(status, 429)
        self.assertEqual(body["error"]["code"], "rate_limited")
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        self.assertEqual(post_json(self.gateway, payload, {"X-Tenant-ID": "bob"})[0], 200)

    def test_rejects_body_above_configured_limit(self):
        status, _, body = post_json(self.gateway, b"x" * 4097)
        self.assertEqual(status, 413)
        self.assertEqual(body["error"]["code"], "body_too_large")
        self.assertEqual(self.worker.worker_state.requests, 0)

    def test_reports_no_capacity_when_all_slots_are_reserved(self):
        registry = self.gateway.service.registry
        first = registry.reserve("demo", set())
        second = registry.reserve("demo", set())
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        try:
            status, _, body = post_json(self.gateway, {"model": "demo", "prompt": "hello", "max_tokens": 2})
            self.assertEqual(status, 503)
            self.assertEqual(body["error"]["code"], "no_capacity")
            self.assertEqual(self.worker.worker_state.requests, 0)
        finally:
            registry.finish(first.name, success=True, elapsed_ms=1)
            registry.finish(second.name, success=True, elapsed_ms=1)

    def test_all_backends_failed_returns_502(self):
        with self.worker.worker_state.lock:
            self.worker.worker_state.fail = True
        status, _, body = post_json(self.gateway, {"model": "demo", "prompt": "hello", "max_tokens": 2})
        self.assertEqual(status, 502)
        self.assertEqual(body["error"]["code"], "all_backends_failed")
        status, metrics = get(self.gateway, "/metrics")
        self.assertEqual(status, 200)
        self.assertIn('gateway_requests_total{outcome="error"} 1', metrics)
        self.assertIn('gateway_backend_attempts_total{backend="local",result="failure"} 1', metrics)

    def test_deadline_exceeded_returns_504_after_attempt(self):
        now = [100.0]
        self.gateway.service.router.clock = lambda: now[0]

        def expired_backend(*args):
            now[0] += 2.0
            raise BackendFailure("simulated slow transport")

        with patch("gateway.router.generate", side_effect=expired_backend):
            status, _, body = post_json(self.gateway, {"model": "demo", "prompt": "hello", "max_tokens": 2})
        self.assertEqual(status, 504)
        self.assertEqual(body["error"]["code"], "deadline_exceeded")
        self.assertEqual(self.gateway.service.registry.snapshot()[0]["inflight"], 0)


class FailoverTests(unittest.TestCase):
    def test_failed_worker_fails_over_then_recovers_via_probe(self):
        failing_state = WorkerState("first", 0)
        failing_state.fail = True
        first = start_server(self, create_worker_server("127.0.0.1", 0, failing_state))
        second = start_server(self, create_worker_server("127.0.0.1", 0, WorkerState("second", 0)))
        backends = []
        for name, server in (("a-first", first), ("b-second", second)):
            backend = copy.deepcopy(config_dict()["backends"][0])
            backend["name"] = name
            backend["url"] = "http://127.0.0.1:%d" % server.server_address[1]
            backends.append(backend)
        gateway = start_server(self, create_server(load_raw_config(config_dict(backends=backends))))
        payload = {"model": "demo", "prompt": "route this", "max_tokens": 2}

        status, _, response = post_json(gateway, payload)
        self.assertEqual(status, 200)
        self.assertEqual(response["backend"], "b-second")
        self.assertEqual(response["attempts"], ["a-first", "b-second"])
        states = {item["name"]: item for item in gateway.service.registry.snapshot()}
        self.assertEqual(states["a-first"]["circuit"], "open")
        self.assertEqual(states["a-first"]["inflight"], 0)
        status, metrics = get(gateway, "/metrics")
        self.assertEqual(status, 200)
        self.assertIn('gateway_requests_total{outcome="success"} 1', metrics)
        self.assertIn('gateway_requests_total{outcome="error"} 0', metrics)
        self.assertIn('gateway_backend_attempts_total{backend="a-first",result="failure"} 1', metrics)
        self.assertIn('gateway_backend_attempts_total{backend="b-second",result="success"} 1', metrics)

        status, _, response = post_json(gateway, payload)
        self.assertEqual(status, 200)
        self.assertEqual(response["attempts"], ["b-second"])

        with failing_state.lock:
            failing_state.fail = False
        gateway.service.registry.clock = lambda: time.monotonic() + 1.0
        status, _, response = post_json(gateway, payload)
        self.assertEqual(status, 200)
        self.assertEqual(response["backend"], "a-first")
        self.assertEqual(response["attempts"], ["a-first"])
        states = {item["name"]: item for item in gateway.service.registry.snapshot()}
        self.assertEqual(states["a-first"]["circuit"], "closed")
        self.assertEqual(states["a-first"]["failures"], 0)


class AdapterFailureTests(unittest.TestCase):
    def test_malformed_backend_json_is_rejected(self):
        server = start_reply_server(self, b"{not valid json")
        backend = BackendConfig("invalid", "http://127.0.0.1:%d" % server.server_address[1],
                                "contract", ("demo",), 1, 1.0, 500)
        with self.assertRaisesRegex(BackendFailure, "invalid JSON contract"):
            generate(backend, "demo", "hello", 2, 0.5)

    def test_oversized_backend_reply_is_rejected(self):
        server = start_reply_server(self, b"x" * (1024 * 1024 + 1))
        backend = BackendConfig("oversized", "http://127.0.0.1:%d" % server.server_address[1],
                                "contract", ("demo",), 1, 1.0, 500)
        with self.assertRaisesRegex(BackendFailure, "exceeds 1 MiB"):
            generate(backend, "demo", "hello", 2, 0.5)


class OllamaAdapterTests(unittest.TestCase):
    def test_local_ollama_contract_translation(self):
        captured = []

        class OllamaHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, data))
                response = json.dumps({"response": "sample answer", "prompt_eval_count": 3, "eval_count": 2}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        server = start_server(self, ThreadingHTTPServer(("127.0.0.1", 0), OllamaHandler))
        backend = BackendConfig("model", "http://127.0.0.1:%d" % server.server_address[1],
                                "ollama", ("tiny",), 1, 1.0, 500)
        output, usage = generate(backend, "tiny", "say hello", 7, 0.5)
        self.assertEqual(output, "sample answer")
        self.assertEqual(usage, {"input_tokens": 3, "output_tokens": 2})
        path, payload = captured[0]
        self.assertEqual(path, "/api/generate")
        self.assertEqual(payload, {"model": "tiny", "prompt": "say hello", "stream": False,
                                   "options": {"num_predict": 7}})


if __name__ == "__main__":
    unittest.main()
