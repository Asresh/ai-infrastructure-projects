# AI Infrastructure Projects

Practical, reproducible projects about reliable model serving and the systems around it.

## Project 01 — Inference Gateway

[Explore the project](project01-inference-gateway/README.md) · [Architecture](project01-inference-gateway/docs/design.md) · [Measured local demo](project01-inference-gateway/docs/demo-results.json) · [Role alignment](project01-inference-gateway/docs/role-alignment.md)

This gateway routes inference requests across interchangeable backends. It enforces deadlines and tenant rate limits, retries failed requests on another backend, opens circuits for unhealthy backends, and publishes operational metrics. A self-contained demo uses simulated local workers to exercise normal operation, failure, and recovery. A separate adapter can send requests to a locally installed Ollama model.

From this repository's root:

```bash
cd project01-inference-gateway
python3 -m scripts.demo
python3 -m unittest discover -s tests -v
```

The demo needs Python 3.9 or newer and no third-party packages. The local measurements describe simulated workers on one computer; they are not GPU or model benchmarks.

No license has been selected for this repository. Copyright and reuse permissions remain with the repository owner until a license is added.
