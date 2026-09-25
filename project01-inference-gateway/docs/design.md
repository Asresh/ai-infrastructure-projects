# Inference gateway: design and operating model

This document explains the system behind `project01-inference-gateway`. Start with the [architecture diagram](images/architecture.svg), then follow one request in the [request flow](images/request-flow.svg). The project README contains the exact commands for running the demo.

## 1. Problem and goal

An application often calls one model server directly. That is simple until the server is busy, slow, or down. Adding another server solves only part of the problem: the application then needs to decide which server to use, respect shared capacity, recover from failures, and explain what happened during an incident.

This gateway puts those decisions in one small HTTP service. A client sends a model name and prompt to one endpoint. The gateway validates the request, checks the caller's rate limit, chooses an eligible worker, sends the request within a deadline, and returns the result with a request ID. It also exposes health and metrics endpoints so an operator can distinguish a healthy gateway from healthy upstream model workers.

The implementation uses Python 3.9+ and the standard library. Its local mock workers **simulate inference** to exercise routing and failures without a GPU; they do not run a model. An optional Ollama adapter connects to a real local model. The core routing concepts are independent of either worker protocol.

### Goals

- Give clients one stable request and response contract while workers can change.
- Protect workers with per-tenant request admission and per-worker concurrency limits.
- Route around a slow or failing worker using recent latency and a circuit breaker.
- Give all worker attempts and retries one monotonic routing deadline.
- Make routing decisions and failures visible through logs, metrics, and backend state.
- Make the complete system runnable on a laptop without a cloud account or GPU.

### Scope and honest limits

The demo is a single gateway process with in-memory state. Rate-limit buckets, breaker state, latency estimates, and capacity counters reset on restart and are not coordinated across gateway replicas. The mock workers simulate inference; their outputs are not evidence of model quality. The gateway is a reference implementation, not a claim of production scale or measured throughput. For a deployment with several gateway instances, admission and worker state need a shared or consistently sharded control plane. The monotonic deadline is checked before each routing attempt and passed to the HTTP adapter as a timeout. Python's socket timeout is not a hard wall-clock cancellation for a worker that trickles response bytes, so the deadline is best effort during an active read.

## 2. System at a glance

![Architecture: clients, gateway control points, backend adapters, and telemetry](images/architecture.svg)

| Component | Responsibility | State it owns |
| --- | --- | --- |
| HTTP boundary | Parse requests; enforce body and field limits; return stable JSON and status codes | Per-request only |
| Tenant limiter | Admit requests at a bounded rate by `X-Tenant-ID` | In-memory token buckets |
| Backend registry | Track configured model support, in-flight capacity, latency estimate, and breaker state | In-memory worker records |
| Scheduler | Pick an eligible worker with the lowest current score | Per-request candidate set |
| Circuit breaker | Temporarily stop sending traffic to a repeatedly failing worker; probe recovery | Per-worker failure and recovery state |
| Adapters | Translate the stable gateway contract into a worker's HTTP protocol | Connection and response handling |
| Telemetry | Emit structured request logs and Prometheus-format counters, gauges, and a latency histogram | In-memory metric values |

The registry is configured explicitly. A worker is eligible only if it supports the requested model, has a free concurrency slot, and its circuit is closed or permits a recovery probe. Among eligible workers, the scheduler minimizes `EWMA latency × (1 + in-flight / capacity) / weight`; backend name breaks a tie. Successful attempts update the EWMA with 30% new latency and 70% previous latency. Failed attempts increment the breaker failure count but do not update the EWMA. A cooled-down worker gets one half-open probe with selection priority. The router releases a reserved capacity slot in a `finally` block, including after errors and timeouts.

## 3. Public HTTP contract

### `POST /v1/infer`

Request content type: `application/json`.

```json
{
  "model": "demo-text",
  "prompt": "Explain what an inference gateway does in one sentence.",
  "max_tokens": 64
}
```

`model` selects a configured model and must be a string of 1–128 characters. `prompt` must be a nonempty string within the configured character limit. `max_tokens` is optional, defaults to `256`, and must be an integer from `1` through the configured maximum. The server requires `Content-Type: application/json` and `Content-Length`, and rejects malformed JSON, invalid fields, unknown models, and oversized bodies before calling a worker. The optional `X-Tenant-ID` header selects an in-memory rate-limit bucket; if absent, the gateway uses `anonymous`. Tenant IDs must contain 1–64 letters, digits, hyphens, or underscores. The header is an accounting label, **not authentication**: a public deployment must establish tenant identity at a trusted authentication layer instead of accepting an untrusted header as identity.

Successful response:

```json
{
  "request_id": "d0a58a1b5b554927a9f1d8b6c9acac10",
  "output": "[simulated completion] Explain what an inference gateway does in one sentence.",
  "model": "demo-text",
  "backend": "worker-fast",
  "attempts": ["worker-fast"],
  "elapsed_ms": 25.7,
  "usage": {"input_tokens": 9, "output_tokens": 9}
}
```

`request_id` identifies the request in logs. `backend` names the worker that produced the response. `attempts` is an ordered array of backend names, including failed attempts before the successful one. `elapsed_ms` measures routing time from after validation and rate admission through the successful worker response. The gateway's request-duration metric covers the wider HTTP request. `usage` comes from the worker: the mock worker counts whitespace-separated words, the Ollama adapter maps Ollama's evaluation counts, and another contract worker may return an empty object. These values are not a billing record. The sample values above illustrate the response shape; actual latency and backend choice vary.

An inference error has this shape:

```json
{"request_id":"d0a58a1b5b554927a9f1d8b6c9acac10","error":{"code":"no_capacity","message":"No healthy backend has free capacity"}}
```

The implemented error classes are:

| HTTP status | Meaning | Expected client action |
| --- | --- | --- |
| `400` | Invalid JSON, content length, body shape, field, or tenant ID | Fix the request |
| `404` | Unknown model or endpoint | Select a configured model or valid endpoint |
| `411` | Missing `Content-Length` | Send a body length |
| `413` | Request body is too large | Send a smaller request |
| `415` | Request content type is not JSON | Send `application/json` |
| `429` | Tenant has no available rate-limit token | Honor `Retry-After` |
| `502` | All attempted backends failed | Retry later with backoff |
| `503` | No eligible worker: capacity is full or circuits are open | Retry later with backoff |
| `504` | The request deadline expired | Retry only if the operation is safe for the caller |

An upstream may have done work even if the client receives a timeout. This API does not promise exactly-once execution. A production client that may retry should attach an idempotency key supported by a durable request store, or accept duplicate work.

### Read-only endpoints

| Endpoint | Meaning |
| --- | --- |
| `GET /healthz` | The gateway process can serve HTTP. This does not assert worker health. |
| `GET /readyz` | Returns `200` when at least one backend circuit is closed, half-open, or open with its cooldown expired and therefore eligible for a recovery probe. Returns `503` only while every circuit is open and still cooling down. It does not probe network reachability, check model-specific capacity, or guarantee the next request can be served. |
| `GET /metrics` | Prometheus text exposition for request, error, latency, and worker state signals. |
| `GET /v1/backends` | Human-readable snapshot of configured workers and their current routing state. |

Do not expose diagnostics publicly without access control. Backend names and operational state can reveal infrastructure details. The current metrics use bounded labels for request outcome, backend name, attempt result, and breaker state. Prompt text and tenant IDs are never metric labels.

## 4. End-to-end request flow

![Request flow: validation, admission, selection, retries, and response](images/request-flow.svg)

1. **Receive and validate.** Assign a request ID, check the body size and JSON shape, validate model and token limit, and avoid logging prompt content.
2. **Admit the caller.** Refill the tenant's token bucket according to elapsed monotonic time. A request consumes a token only when admitted. Return `429` if none is available.
3. **Start the deadline.** Establish a monotonic end time for the routing phase after validation and admission. Each upstream attempt receives only the remaining budget. Retries never restart the clock.
4. **Select a worker.** Reject an unconfigured model with `404`. Filter workers by model support, breaker state, and free in-flight capacity. Reserve a slot atomically before dispatch. Score eligible workers with the EWMA/load/weight formula above, then use backend name as the tie break.
5. **Call an adapter.** The `contract` adapter sends `POST /infer` to a local HTTP worker. In the demo, those workers **simulate inference**. The optional `ollama` adapter sends `POST /api/generate` with streaming disabled and maps the reply into `output` and `usage`. Adapter calls have an upstream response limit of 1 MiB and a timeout no greater than the remaining request budget or backend timeout.
6. **Observe and decide.** On success, update latency state, release capacity, close an eligible recovery probe, log one completion record, increment metrics, and return JSON. On an upstream HTTP, transport, or invalid-contract error, release capacity, update the breaker, and try another untried eligible worker while time remains. Validation and rate-limit failures are not retried.
7. **Finish once.** If all attempted backends fail, return `502`; if none can be selected before an attempt, return `503`; if the request deadline expires, return `504`. Emit one request completion record even when several upstream attempts occurred.

### Why these routing controls fit together

The token bucket limits **arrival rate** per tenant. Capacity limits bound **concurrent work** at each worker. The EWMA favors workers that recently completed work quickly. The breaker removes a worker that repeatedly fails, then allows a controlled probe so it can return automatically. A deadline bounds the total user-visible wait. None of these controls replaces the others: a worker can be slow with spare capacity, full while healthy, or unreachable despite a low historical latency.

## 5. Failure behavior and recovery

| Failure | Gateway behavior | Visible signal |
| --- | --- | --- |
| Invalid JSON or fields | Reject before rate or worker work | `4xx` and validation log/metric |
| Tenant burst | Reject once bucket is empty | `429` and `Retry-After`; request outcome `rejected` metric |
| Worker at capacity | Choose another eligible worker; fail if none exists | Backend snapshot and `503` if exhausted |
| Worker timeout | Record failure, update breaker, retry within remaining deadline if possible | Attempt failure and eventual `502` or `504` |
| Repeated worker failures | Open breaker; skip worker for a recovery interval | Breaker state and skipped routing |
| Worker recovers | Permit a limited probe and close after success | Breaker transition and successful request |
| Gateway restarts | Rebuild in-memory control state from configuration | Process restart; transient cold latency estimates |

Retrying can increase load during an incident. The router attempts each backend at most once per request; therefore the configured backend count and original deadline bound retries. Retries use a different eligible worker and never restart the deadline. The caller receives one final response, while attempt-level telemetry preserves intermediate failures.

The gateway enforces a 1 MiB upstream response limit and rejects malformed worker replies. A successful HTTP status alone does not prove the worker returned a valid output. Backend HTTP errors, transport failures, and invalid contracts are classified as backend failures. The current HTTP handler explicitly converts `RoutingError` to JSON; unexpected server exceptions are outside this contract and remain a hardening gap.

## 6. Observability and operations

Every completed inference request produces a `request_finished` JSON log with UTC time, request ID, outcome, HTTP status, and elapsed time. A successful request also includes selected backend and attempt count. Failed backend attempts produce `backend_attempt_failed` records with the same request ID, backend name, and a classified reason. The current logs do not include tenant IDs, models, prompts, outputs, or upstream response bodies. This is safer for prompt privacy, but limits per-tenant diagnosis.

Prometheus metrics expose `gateway_requests_total{outcome=...}`, `gateway_backend_attempts_total{backend=...,result=...}`, `gateway_request_duration_seconds` histogram, `gateway_backend_inflight`, `gateway_backend_ewma_latency_seconds`, and `gateway_backend_circuit{backend=...,state=...}`. Request outcomes distinguish `success`, `error`, and `rejected`; they do not break rejections down by HTTP status. The histogram retains every observed request duration in memory, so a long-running process needs bounded or streaming bucket accumulation before production use.

For local diagnosis, first check `/healthz` and `/readyz`, then inspect `/v1/backends`, then read `/metrics` and the request's structured log line. A liveness success with readiness failure means every configured breaker is open and still in its cooldown. A readiness success can mean only that a recovery probe is due; it still cannot prove workers are reachable. `/v1/backends` can show an expired breaker as `open` until the next routing attempt starts that probe.

## 7. Security and deployment considerations

- Bind the local demo to loopback by default. In a shared network, place authentication, TLS termination, and tenant identity enforcement in front of the gateway.
- Validate all externally supplied fields, bound body and output sizes, and use explicit HTTP timeouts so a worker cannot hold a request indefinitely.
- Treat prompts and outputs as sensitive. Do not put them in logs, metrics, URLs, or error messages.
- Accept worker addresses only from trusted configuration. Do not let request fields select arbitrary URLs; that would create a server-side request forgery path.
- Keep outbound worker access on a restricted network where possible. A local Ollama server may expose models and resource-intensive operations.
- In-memory tenant limits are educational and useful for one process; distributed limits require shared, authenticated identity and state. The current bucket map grows with distinct tenant IDs, so an exposed service also needs bucket eviction or an upstream tenant allowlist.

## 8. Design tradeoffs and next steps

| Decision | Benefit | Cost / next step |
| --- | --- | --- |
| Python standard library HTTP stack | Simple, portable, no third-party service requirement | Add a hardened server and load tests before internet exposure |
| Explicit backend registry | Predictable demo and no discovery dependency | Add service discovery and authenticated health reporting at larger scale |
| In-memory token buckets and breakers | Easy to inspect and reason about | Coordinate state across replicas or shard tenants/workers |
| EWMA and capacity-based scheduling | Adapts to latency while respecting concurrency | Compare against least-loaded routing under controlled load |
| Each backend tried at most once under one deadline | Gives another healthy worker a chance to complete a request | Add a configurable attempt cap and idempotency support for side-effecting model tools |
| Simulated mock workers for default demo | Deterministic, GPU-free exercise of control paths | Use the optional Ollama adapter for real local inference, then publish measured results separately |

The natural production extension is a repeatable load and fault-injection study: use a real model adapter, record p50/p95/p99 latency and error rate at several arrival rates, inject worker slowdowns and restarts, and compare scheduling policies. Any published result must include hardware, model, input sizes, concurrency, and the exact measurement method.
