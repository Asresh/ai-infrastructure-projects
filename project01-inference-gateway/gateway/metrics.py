"""Small Prometheus text exposition without a runtime dependency."""

import threading


BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.requests = {"success": 0, "error": 0, "rejected": 0}
        self.attempts = {}
        self.duration_count = 0
        self.duration_sum = 0.0
        self.duration_buckets = {bucket: 0 for bucket in BUCKETS}

    def request(self, outcome, seconds):
        with self._lock:
            self.requests[outcome] += 1
            self.duration_count += 1
            self.duration_sum += seconds
            for bucket in BUCKETS:
                if seconds <= bucket:
                    self.duration_buckets[bucket] += 1

    def attempt(self, backend, result):
        with self._lock:
            key = (backend, result)
            self.attempts[key] = self.attempts.get(key, 0) + 1

    def render(self, backend_snapshot):
        with self._lock:
            requests = dict(self.requests)
            attempts = dict(self.attempts)
            duration_count = self.duration_count
            duration_sum = self.duration_sum
            duration_buckets = dict(self.duration_buckets)
        lines = [
            "# HELP gateway_requests_total Completed client requests by outcome.",
            "# TYPE gateway_requests_total counter",
        ]
        for outcome, count in sorted(requests.items()):
            lines.append('gateway_requests_total{outcome="%s"} %d' % (outcome, count))
        lines += [
            "# HELP gateway_backend_attempts_total Attempts sent to a backend.",
            "# TYPE gateway_backend_attempts_total counter",
        ]
        for (backend, result), count in sorted(attempts.items()):
            safe = backend.replace("\\", "\\\\").replace('"', '\\"')
            lines.append('gateway_backend_attempts_total{backend="%s",result="%s"} %d' % (safe, result, count))
        lines += [
            "# HELP gateway_request_duration_seconds End-to-end request latency.",
            "# TYPE gateway_request_duration_seconds histogram",
        ]
        for bucket in BUCKETS:
            lines.append('gateway_request_duration_seconds_bucket{le="%s"} %d' % (
                bucket, duration_buckets[bucket]))
        lines.append('gateway_request_duration_seconds_bucket{le="+Inf"} %d' % duration_count)
        lines.append("gateway_request_duration_seconds_count %d" % duration_count)
        lines.append("gateway_request_duration_seconds_sum %.6f" % duration_sum)
        lines += [
            "# HELP gateway_backend_inflight Active requests sent to a backend.",
            "# TYPE gateway_backend_inflight gauge",
        ]
        for backend in backend_snapshot:
            safe = backend["name"].replace("\\", "\\\\").replace('"', '\\"')
            lines.append('gateway_backend_inflight{backend="%s"} %d' % (safe, backend["inflight"]))
            lines.append('gateway_backend_ewma_latency_seconds{backend="%s"} %.6f' % (safe, backend["ewma_ms"] / 1000.0))
            for state in ("closed", "open", "half_open"):
                lines.append('gateway_backend_circuit{backend="%s",state="%s"} %d' % (
                    safe, state, int(backend["circuit"] == state)))
        return "\n".join(lines) + "\n"
