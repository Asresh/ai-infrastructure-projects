# Verification and measured local result

This report records checks performed on **September 24, 2026 Pacific time** (September 25 UTC). All HTTP traffic stayed on loopback. The workers in the demo **simulate** model responses and delays; the results below are about the gateway request path, not AI model quality or GPU performance.

## Automated checks

From `project01-inference-gateway/`:

```bash
python3 -m unittest discover -s tests -v
```

**Observed result: 24 tests passed, 0 failed** on Python 3.9.6. The tests exercise configuration validation, token bucket refill and tenant isolation, backend capacity and latency-based selection, circuit opening and recovery, readiness after cooldown, request validation and body limits, rate limiting, HTTP 502/503/504 paths, retry metrics, malformed or oversized backend replies, failure and fallback across two real loopback HTTP workers, and translation to the optional local Ollama adapter contract. The integration tests open ephemeral ports on `127.0.0.1`.

The CI workflow repeats these checks with Python 3.9 and 3.12 on Linux and runs the demo. CI status is not claimed here; it will be visible after the repository is published and a workflow run completes.

## Reproducible failure experiment

```bash
python3 -m scripts.demo
python3 -m scripts.render_results
```

The demo starts two simulated workers and the gateway on ephemeral local ports. Worker A sleeps 12 ms per request; worker B sleeps 35 ms. It warms routing with eight requests, then makes **40 requests at concurrency 4** in each measured phase:

1. Normal operation.
2. Worker A returns HTTP 503, causing fallback and circuit opening.
3. Worker A is restored and receives a probe after the cooldown.

The recorded [machine-readable result](demo-results.json) and [latency chart](images/demo-latency.svg) were produced by those commands. This is one short local run on macOS arm64 and Python 3.9.6; repeat runs will differ with machine load.

| Phase | HTTP 200 | Client p50 | Client p95 | Retried requests | Final backend distribution |
| --- | ---: | ---: | ---: | ---: | --- |
| Normal | 40/40 | 17.16 ms | 18.21 ms | 0 | 40 fast |
| Worker A failing | 40/40 | 41.65 ms | 57.95 ms | 5 | 40 replica |
| Worker A restored | 40/40 | 16.51 ms | 38.94 ms | 0 | 37 fast, 3 replica |

The failure phase's higher latency is expected: some calls first hit the failing worker, then retry on the slower replica. All clients still received successful responses in this run. The recovered worker served requests again. These numbers establish observed behavior for this scenario only; they are not an availability guarantee or a comparison to another serving platform.

## What remains unverified

- The optional Ollama adapter's HTTP contract is tested against a local fake server. An actual Ollama model was not installed or benchmarked in this environment.
- No multi-host deployment, GPU scheduling, persistent metrics backend, or long-duration soak test was run.
- The tenant header is a demo identifier, not authentication. A production deployment needs authenticated identity, TLS at the edge, resource limits, and a security review before exposure beyond loopback.
