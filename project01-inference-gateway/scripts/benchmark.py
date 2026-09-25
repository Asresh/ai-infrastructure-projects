"""Replayable HTTP load generator; results describe this machine only."""

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib import error, request


def _percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, (len(ordered) * percent + 99) // 100 - 1))
    return round(ordered[index], 2)


def run_load(url, count, concurrency, model="demo-text", timeout=10.0):
    payload = json.dumps({"model": model, "prompt": "Explain why reliable inference matters", "max_tokens": 32}).encode("utf-8")

    def one(index):
        req = request.Request(url + "/v1/infer", data=payload,
                              headers={"Content-Type": "application/json", "X-Tenant-ID": "benchmark"}, method="POST")
        started = time.monotonic()
        try:
            with request.urlopen(req, timeout=timeout) as response:
                result = json.load(response)
                status = response.status
        except error.HTTPError as exc:
            status = exc.code
            try:
                result = json.load(exc)
            except ValueError:
                result = {}
        except (error.URLError, OSError, TimeoutError):
            status = 0
            result = {}
        return {
            "index": index,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "backend": result.get("backend"),
            "attempts": result.get("attempts", []),
        }

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        outcomes = list(pool.map(one, range(count)))
    elapsed = time.monotonic() - started
    latencies = [row["latency_ms"] for row in outcomes]
    statuses = Counter(str(row["status"]) for row in outcomes)
    backends = Counter(row["backend"] for row in outcomes if row["backend"])
    return {
        "requests": count,
        "concurrency": concurrency,
        "successes": statuses.get("200", 0),
        "status_counts": dict(sorted(statuses.items())),
        "backend_counts": dict(sorted(backends.items())),
        "retried_requests": sum(len(row["attempts"]) > 1 for row in outcomes),
        "throughput_rps": round(count / elapsed, 2),
        "elapsed_seconds": round(elapsed, 3),
        "latency_ms": {"p50": _percentile(latencies, 50),
                       "p95": _percentile(latencies, 95),
                       "p99": _percentile(latencies, 99)},
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark a running gateway")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--model", default="demo-text")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("requests and concurrency must be positive")
    print(json.dumps(run_load(args.url, args.requests, args.concurrency, args.model), indent=2))


if __name__ == "__main__":
    main()
