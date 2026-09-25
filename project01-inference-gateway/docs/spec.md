# Inference Gateway: specification and acceptance plan

## 1. Problem and scope

A client should not need to know which inference worker is fast, full, or temporarily failing. The gateway presents one local HTTP contract and chooses among explicitly configured workers that serve the requested model. It bounds request time and worker concurrency, rejects excessive tenant traffic, exposes operational signals, and retries a different worker after a failed attempt.

The default runnable system consists of one gateway and two **simulated** inference workers. It demonstrates the infrastructure request path without a real model. An optional Ollama adapter connects the same client contract to one local model server. The gateway is intended as a portfolio reference implementation and local experiment, not an internet-facing service or a distributed production control plane.

The [project README](../README.md) gives step-by-step usage, the [design document](design.md) explains component choices, and [role alignment](role-alignment.md) connects the work to the researched positions.

## 2. Actors and interfaces

| Actor | What it needs |
| --- | --- |
| Application client | Stable `POST /v1/infer` contract, bounded response time, actionable errors. |
| Gateway operator | Health, backend state, structured events, and metrics to locate failures. |
| Backend worker | A configured HTTP adapter, bounded concurrency, and a finite timeout. |
| Project reviewer | One-command demo, reproducible tests, diagrams, and honest measurement notes. |

The client sends JSON with `model`, `prompt`, and optional `max_tokens`, plus optional `X-Tenant-ID`. The gateway returns a result with the selected backend and attempted backend names, or an error with request ID and code. The `contract` worker receives the same three fields at `/infer`; the Ollama adapter translates to `/api/generate` with `stream:false`.

## 3. Functional requirements

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| F1 | Load a JSON configuration with at least one uniquely named backend. Backend names are 1–64 letters, digits, underscores, or hyphens; the gateway port is 0–65,535, with 0 reserved for an ephemeral local port. Reject unsupported adapters, invalid model lists, nonpositive capacity/timeouts, and non-loopback gateway bind addresses. | Configuration tests reject invalid examples; a valid local config starts the gateway. |
| F2 | Accept only JSON object inference requests with bounded body, model, prompt, and token count. Reject invalid input before backend work. | HTTP tests cover missing/invalid fields, bad JSON, content type, and body limit. |
| F3 | Apply a per-tenant token bucket before dispatch, with default tenant `anonymous` and `Retry-After` on `429`. | Rate-limit tests show independent tenants, refill, and a rejected burst. |
| F4 | Select only workers serving the model that have a free capacity slot and permit traffic under the circuit state. Reserve/release slots safely under concurrent requests. | Registry tests exercise model filtering, capacity, and release after success/failure. |
| F5 | Score eligible closed-circuit workers using latency estimate, load, and weight; permit one recovery probe after cooldown. | Deterministic registry tests check selection and half-open behavior. |
| F6 | Keep retries within one overall request deadline; use a different eligible worker after a failed attempt. | Router/integration tests check failover and deadline/error behavior. |
| F7 | Normalize `contract` and Ollama backend responses to string `output` and object `usage`; reject malformed or oversized backend replies. | Adapter tests or integration checks cover valid and invalid replies. |
| F8 | Return a stable success body with request ID, model, output, selected backend, ordered attempts array, elapsed time, and usage. Return an error object with request ID, code, and message for inference POST failures. | HTTP integration tests assert response shape and status. |
| F9 | Expose `/healthz`, `/readyz`, `/v1/backends`, and Prometheus text at `/metrics`. | Endpoint tests and manual inspection of metrics. |
| F10 | Record one final outcome per inference POST and a separate counter for each backend attempt. Log operational events without prompt or output text. | Metrics/log tests or inspection after a routed failure. |
| F11 | Provide a self-contained normal/failure/recovery demo with saved JSON measurements and an SVG chart derived from those measurements. | `python3 -m scripts.demo` and `python3 -m scripts.render_results`; inspect the saved artifacts. |

## 4. HTTP contract and limits

### Request

```http
POST /v1/infer HTTP/1.1
Content-Type: application/json
X-Tenant-ID: example-team

{"model":"demo-text","prompt":"Explain a reliable model service","max_tokens":32}
```

`model` must be a string of 1–128 characters and must match at least one configured backend. `prompt` must be a nonempty string no longer than `max_prompt_chars`; the local config sets this to 8,192. `max_tokens` defaults to the smaller of 256 and the configured maximum, and must be an integer from 1 through that maximum; the local maximum is 1,024. The body limit is 65,536 bytes in the local config. `X-Tenant-ID` is 1–64 ASCII letters, digits, `_`, or `-`; omitted means `anonymous`. This header is only an accounting key in the local demo, not an authentication credential.

### Success

```json
{
  "request_id": "opaque-unique-id",
  "model": "demo-text",
  "output": "[simulated completion] Explain a reliable model service",
  "backend": "worker-fast",
  "attempts": ["worker-fast"],
  "elapsed_ms": 15.7,
  "usage": {"input_tokens": 5, "output_tokens": 5}
}
```

`attempts` is ordered and contains names, not a count. `elapsed_ms` starts when upstream routing begins, after validation and rate admission. `usage` comes from the worker or Ollama adapter and is not independently verified by the gateway.

### Error

```json
{"request_id":"opaque-unique-id","error":{"code":"no_capacity","message":"No healthy backend has free capacity"}}
```

The status/code pairs of interest are: `400` for invalid JSON or fields, `404 unknown_model`, `408 request_timeout` when body reading stalls, `411 length_required`, `413 body_too_large`, `415 unsupported_media_type`, `429 rate_limited`, `502 all_backends_failed`, `503 no_capacity`, and `504 deadline_exceeded`. An unknown POST path returns `404 not_found` with the same error shape. An unknown GET path returns a simpler `404` body without a request ID. Rate-limited responses include an integer `Retry-After` header.

`GET /healthz` indicates that the HTTP process answers requests. `GET /readyz` checks whether any configured backend circuit is closed, half-open, or past its cooldown; it does not make an upstream call or test free capacity. After cooldown, readiness can return `200` while a backend snapshot still shows `open`, because the next routing attempt changes it to half-open for a probe. `GET /v1/backends` returns current model, adapter, capacity, in-flight count, EWMA latency, circuit state, and consecutive failures for each backend. `GET /metrics` emits Prometheus text containing request outcomes, attempt outcomes, end-to-end HTTP request duration, and backend gauges.

## 5. Routing and failure rules

1. The gateway validates the request and rate-admits the tenant before routing.
2. The router confirms at least one configured backend serves the model and sets a monotonic deadline.
3. The registry filters by model, free slot, and circuit state. A closed-circuit worker's score is `EWMA milliseconds × (1 + inflight / capacity) / weight`; the lowest score wins, with backend name as a tie break. A worker whose cooldown ended becomes half-open and gets one prioritized probe.
4. The adapter timeout is `min(remaining request deadline, backend timeout)`. A successful response updates the EWMA with 30% of that attempt's elapsed time, resets the failure count, closes the circuit, and releases the capacity slot.
5. A transport error, timeout, non-200 response, invalid JSON contract, or oversized reply counts as a failed attempt. The gateway increments the failure count, opens the circuit at the configured threshold, releases the slot, and tries an unattempted eligible worker while time remains.
6. If time expires, return `504`. If attempts were made and no eligible backend remains, return `502`. If none could be reserved before an attempt, return `503`. Retries can result in duplicate backend work; the API does not promise exactly-once execution.

The registry, limiter, and metrics use locks to protect shared in-memory state across HTTP threads. The implementation does not coordinate state across gateway processes.

## 6. Operational and security requirements

| Area | Requirement and current behavior |
| --- | --- |
| Network boundary | Gateway configuration accepts loopback bind addresses only. A production deployment needs authentication, TLS termination, and trusted tenant identity. |
| Input and output bounds | Request body, prompt, requested token count, backend timeout, and backend response bytes have explicit limits. |
| Sensitive content | Gateway operational events and metrics omit prompt and output text. Worker request bodies still contain prompts as required to perform inference. |
| Backpressure | Per-tenant rate buckets limit arrival rate; per-backend capacity limits concurrent upstream work. There is no durable queue. |
| Recovery | A worker opens its breaker after repeated failures and receives one probe after a cooldown. There is no active health poll. |
| Observability | JSON events go to stderr; metrics and backend snapshots are exposed over local HTTP and reset on process restart. |
| Reproducibility | Python 3.9+ standard library is sufficient for gateway, mock workers, demo, benchmark, chart, and tests. The optional real model is an external local Ollama dependency. |

## 7. Verification plan and evidence

Run from the project directory:

```bash
python3 -m unittest discover -s tests -v
python3 -m scripts.demo
python3 -m scripts.render_results
```

The unit/integration suite should verify the failure paths as well as the successful path. The minimum acceptance matrix is:

| Area | Check |
| --- | --- |
| Configuration | Valid local config loads; invalid host/backend/limits are rejected. |
| HTTP boundary | Valid inference succeeds; wrong content type, malformed JSON, invalid fields, oversized body, and unknown model return the specified errors. |
| Tenant admission | A burst is limited with `429` and `Retry-After`; another tenant has an independent bucket. |
| Registry | Capacity is reserved and released; open circuits are skipped; only one half-open probe is allowed; recovery closes the circuit. |
| Router and adapter | A failed fast worker is retried on another backend within the deadline; all-failed and no-capacity paths are distinguishable. |
| Observability | Request outcome count differs appropriately from attempt count after a retry; Prometheus text and backend state are readable. |
| Demo | All three phases complete successfully; the failure phase contains retries; recovery routes to the restored worker. |

The saved [demo results](demo-results.json) record one actual local run: 40/40 successful client requests in each phase, five retried requests in the failure phase, and traffic returning to the faster worker after recovery. The [chart](images/demo-latency.svg) visualizes p50 and p95 for those same phases. The measurement file identifies the Python version, operating system, scenario, and date. The results are specific to a loopback simulated-worker run and are not a model-serving performance claim. The [testing report](testing.md) records the checks actually run and their outcomes.

## 8. Known limitations and follow-on work

- State is in one process. Multiple gateway replicas need coordinated or deliberately partitioned rate and worker state.
- Readiness relies on circuit state; it does not actively verify worker availability or account for momentary full capacity.
- The endpoint is non-streaming. It does not support token streaming, cancellation propagated to workers, or a durable request queue.
- `X-Tenant-ID` is caller supplied. A shared deployment needs authenticated identity and protected diagnostic endpoints.
- Retries may repeat work after a timeout. A durable idempotency design is needed if a future request can have side effects.
- Mock workers demonstrate control behavior only. Real throughput and tail latency studies need a stated model, hardware, input mix, and load method.
