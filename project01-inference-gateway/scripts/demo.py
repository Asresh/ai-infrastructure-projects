"""Run local steady-state, failure, and recovery experiments."""

import argparse
import json
import platform
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from gateway.config import load_config
from gateway.http import create_server
from gateway.mock_worker import WorkerState, create_worker_server
from .benchmark import run_load


ROOT = Path(__file__).resolve().parents[1]


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return thread


def run_demo():
    fast_state = WorkerState("worker-fast", latency_ms=12)
    replica_state = WorkerState("worker-replica", latency_ms=35)
    fast = create_worker_server("127.0.0.1", 0, fast_state)
    replica = create_worker_server("127.0.0.1", 0, replica_state)
    servers = [fast, replica]
    threads = [_serve(fast), _serve(replica)]
    try:
        base = load_config(ROOT / "config" / "local.json")
        backends = (
            replace(base.backends[0], url="http://127.0.0.1:%d" % fast.server_address[1], capacity=4),
            replace(base.backends[1], url="http://127.0.0.1:%d" % replica.server_address[1], capacity=4),
        )
        config = replace(base, port=0, backends=backends, rate_per_minute=100000,
                         rate_burst=1000, circuit_cooldown_ms=250)
        gateway = create_server(config)
        servers.append(gateway)
        threads.append(_serve(gateway))
        url = "http://127.0.0.1:%d" % gateway.server_address[1]
        run_load(url, 8, 2)  # Warm the latency estimates before measuring.
        steady = run_load(url, 40, 4)
        fast_state.fail = True
        failure = run_load(url, 40, 4)
        fast_state.fail = False
        time.sleep(0.3)  # Let the open circuit reach its next probe window.
        recovery = run_load(url, 40, 4)
        results = {
            "measured_at_utc": datetime.now(timezone.utc).isoformat(),
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "note": "Loopback HTTP, simulated workers; not a model or GPU benchmark."},
            "scenario": {"fast_worker_ms": 12, "replica_worker_ms": 35,
                         "failure": "fast worker returns HTTP 503", "requests_per_phase": 40},
            "phases": {"steady": steady, "failure": failure, "recovery": recovery},
            "final_backends": gateway.service.registry.snapshot(),
        }
        if any(phase["successes"] != phase["requests"] for phase in results["phases"].values()):
            raise RuntimeError("demo had unsuccessful client requests; inspect results")
        if failure["retried_requests"] < 1:
            raise RuntimeError("failure phase did not exercise failover")
        if recovery["backend_counts"].get("worker-fast", 0) < 1:
            raise RuntimeError("recovery phase did not exercise the half-open probe")
        return results
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=1)


def main():
    parser = argparse.ArgumentParser(description="Run the self-contained local demo")
    parser.add_argument("--output", default=str(ROOT / "docs" / "demo-results.json"))
    args = parser.parse_args()
    results = run_demo()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("All three phases completed successfully. Results: %s" % path)
    for name, phase in results["phases"].items():
        print("%s: %s/%s HTTP 200, p95 %.2f ms, %.2f req/s, retries %s" % (
            name, phase["successes"], phase["requests"], phase["latency_ms"]["p95"],
            phase["throughput_rps"], phase["retried_requests"]))


if __name__ == "__main__":
    main()
