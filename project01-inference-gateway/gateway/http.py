"""HTTP API for inference, health, registry state and metrics."""

import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .events import emit
from .metrics import Metrics
from .rate_limit import TokenBucket
from .registry import BackendRegistry
from .router import Router, RoutingError


TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class GatewayService:
    def __init__(self, config):
        self.config = config
        self.registry = BackendRegistry(config.backends, config.failure_threshold, config.circuit_cooldown_ms)
        self.rate_limit = TokenBucket(config.rate_per_minute, config.rate_burst)
        self.metrics = Metrics()
        self.router = Router(config, self.registry, self.metrics)


def create_server(config):
    service = GatewayService(config)

    class Handler(BaseHTTPRequestHandler):
        server_version = "InferenceGateway/1.0"

        def log_message(self, fmt, *args):
            # Every inference request gets one structured event below.
            pass

        def _send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if headers:
                for name, value in headers.items():
                    self.send_header(name, str(value))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _error(self, status, code, message, request_id, started, headers=None):
            outcome = "error" if status >= 500 else "rejected"
            self._send(status, {"request_id": request_id, "error": {"code": code, "message": message}}, headers=headers)
            elapsed = time.monotonic() - started
            service.metrics.request(outcome, elapsed)
            emit("request_finished", request_id=request_id, status=status, outcome=outcome, elapsed_ms=round(elapsed * 1000, 2))

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, {"status": "alive"})
            elif self.path == "/readyz":
                ready = service.registry.any_ready()
                self._send(200 if ready else 503, {"status": "ready" if ready else "unavailable"})
            elif self.path == "/v1/backends":
                self._send(200, {"backends": service.registry.snapshot()})
            elif self.path == "/metrics":
                self._send(200, service.metrics.render(service.registry.snapshot()), "text/plain; version=0.0.4; charset=utf-8")
            else:
                self._send(404, {"error": {"code": "not_found", "message": "Unknown endpoint"}})

        def do_POST(self):
            started = time.monotonic()
            request_id = uuid.uuid4().hex
            if self.path != "/v1/infer":
                self._error(404, "not_found", "Unknown endpoint", request_id, started)
                return
            content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self._error(415, "unsupported_media_type", "Use application/json", request_id, started)
                return
            length_raw = self.headers.get("Content-Length")
            if length_raw is None:
                self._error(411, "length_required", "Content-Length is required", request_id, started)
                return
            try:
                length = int(length_raw)
            except ValueError:
                self._error(400, "invalid_length", "Invalid Content-Length", request_id, started)
                return
            if length < 0 or length > service.config.max_body_bytes:
                self._error(413, "body_too_large", "Request body exceeds configured limit", request_id, started)
                return
            try:
                self.connection.settimeout(max(1.0, min(10.0, service.config.request_timeout_ms / 1000.0)))
                body = json.loads(self.rfile.read(length))
            except TimeoutError:
                self._error(408, "request_timeout", "Request body read timed out", request_id, started)
                return
            except (ValueError, UnicodeDecodeError):
                self._error(400, "invalid_json", "Body must be valid JSON", request_id, started)
                return
            if not isinstance(body, dict):
                self._error(400, "invalid_request", "Body must be a JSON object", request_id, started)
                return
            model = body.get("model")
            prompt = body.get("prompt")
            max_tokens = body.get("max_tokens", min(256, service.config.max_tokens))
            if not isinstance(model, str) or not 1 <= len(model) <= 128:
                self._error(400, "invalid_model", "model must be a nonempty string of at most 128 characters", request_id, started)
                return
            if not isinstance(prompt, str) or not 1 <= len(prompt) <= service.config.max_prompt_chars:
                self._error(400, "invalid_prompt", "prompt is empty or exceeds the configured limit", request_id, started)
                return
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= service.config.max_tokens:
                self._error(400, "invalid_max_tokens", "max_tokens is out of range", request_id, started)
                return
            tenant = self.headers.get("X-Tenant-ID", "anonymous")
            if not TENANT_RE.fullmatch(tenant):
                self._error(400, "invalid_tenant", "X-Tenant-ID must use 1-64 letters, digits, hyphens or underscores", request_id, started)
                return
            allowed, retry_after = service.rate_limit.allow(tenant)
            if not allowed:
                self._error(429, "rate_limited", "Tenant rate limit exceeded", request_id, started,
                            {"Retry-After": max(1, int(retry_after + 0.999))})
                return
            try:
                result = service.router.infer(model, prompt, max_tokens, request_id)
            except RoutingError as exc:
                self._error(exc.status, exc.code, exc.message, request_id, started)
                return
            self._send(200, result)
            elapsed = time.monotonic() - started
            service.metrics.request("success", elapsed)
            emit("request_finished", request_id=request_id, status=200, outcome="success",
                 backend=result["backend"], attempts=len(result["attempts"]), elapsed_ms=round(elapsed * 1000, 2))

    server = ThreadingHTTPServer((config.host, config.port), Handler)
    server.daemon_threads = True
    server.service = service
    return server
