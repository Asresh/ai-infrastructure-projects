"""End-to-end routing and failover under a single request deadline."""

import time

from .adapters import BackendFailure, generate
from .events import emit


class RoutingError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class Router:
    def __init__(self, config, registry, metrics, clock=time.monotonic):
        self.config = config
        self.registry = registry
        self.metrics = metrics
        self.clock = clock

    def infer(self, model, prompt, max_tokens, request_id):
        if not self.registry.supports(model):
            raise RoutingError("unknown_model", "No backend serves this model", 404)
        total_started = self.clock()
        deadline = total_started + self.config.request_timeout_ms / 1000.0
        attempted = []
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise RoutingError("deadline_exceeded", "Request deadline exceeded", 504)
            backend = self.registry.reserve(model, set(attempted))
            if backend is None:
                if attempted:
                    raise RoutingError("all_backends_failed", "Every available backend failed", 502)
                raise RoutingError("no_capacity", "No healthy backend has free capacity", 503)
            attempted.append(backend.name)
            started = self.clock()
            success = False
            try:
                timeout = min(remaining, backend.timeout_ms / 1000.0)
                output, usage = generate(backend, model, prompt, max_tokens, timeout)
                success = True
                self.metrics.attempt(backend.name, "success")
                return {
                    "request_id": request_id,
                    "model": model,
                    "output": output,
                    "backend": backend.name,
                    "attempts": attempted,
                    "elapsed_ms": round((self.clock() - total_started) * 1000, 2),
                    "usage": usage,
                }
            except BackendFailure as exc:
                self.metrics.attempt(backend.name, "failure")
                emit("backend_attempt_failed", request_id=request_id, backend=backend.name, reason=str(exc))
            finally:
                self.registry.finish(backend.name, success, (self.clock() - started) * 1000.0)
