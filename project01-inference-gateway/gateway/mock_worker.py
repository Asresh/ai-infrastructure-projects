"""Deterministic local worker for routing and fault-injection demos.

This intentionally simulates inference. It does not claim to run an AI model.
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WorkerState:
    def __init__(self, name, latency_ms=20, fail_every=0):
        self.name = name
        self.latency_ms = latency_ms
        self.fail_every = fail_every
        self.fail = False
        self.requests = 0
        self.lock = threading.Lock()

    def next_request(self):
        with self.lock:
            self.requests += 1
            return self.latency_ms, self.fail or (self.fail_every > 0 and self.requests % self.fail_every == 0)


def create_worker_server(host, port, state):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MockInferenceWorker/1.0"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, payload):
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, {"status": "alive", "worker": state.name})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/infer":
                self._send(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("invalid size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload.get("prompt"), str):
                    raise ValueError("invalid prompt")
                if not isinstance(payload.get("model"), str):
                    raise ValueError("invalid model")
            except (ValueError, TypeError, AttributeError):
                self._send(400, {"error": "invalid_request"})
                return
            latency_ms, fail = state.next_request()
            time.sleep(latency_ms / 1000.0)
            if fail:
                self._send(503, {"error": "injected_failure"})
                return
            words = payload["prompt"].split()
            max_tokens = payload.get("max_tokens", 256)
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
                self._send(400, {"error": "invalid_max_tokens"})
                return
            selected = words[:max_tokens]
            output = "[simulated completion] " + " ".join(selected)
            self._send(200, {
                "output": output,
                "usage": {"input_tokens": len(words), "output_tokens": len(selected)},
            })

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.worker_state = state
    return server


def main():
    parser = argparse.ArgumentParser(description="Run a simulated inference worker")
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--latency-ms", type=int, default=20)
    parser.add_argument("--fail-every", type=int, default=0)
    args = parser.parse_args()
    if args.latency_ms < 0 or args.fail_every < 0:
        parser.error("latency-ms and fail-every must be nonnegative")
    server = create_worker_server("127.0.0.1", args.port,
                                  WorkerState(args.name, args.latency_ms, args.fail_every))
    print("%s listening on http://127.0.0.1:%d" % (args.name, server.server_address[1]), flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
