"""Configuration loading and validation."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class BackendConfig:
    name: str
    url: str
    adapter: str
    models: Tuple[str, ...]
    capacity: int
    weight: float
    timeout_ms: int


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    port: int
    request_timeout_ms: int
    max_body_bytes: int
    max_prompt_chars: int
    max_tokens: int
    rate_per_minute: int
    rate_burst: int
    failure_threshold: int
    circuit_cooldown_ms: int
    backends: Tuple[BackendConfig, ...]


def _positive_int(value, field, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer" % field)
    if value < (0 if allow_zero else 1):
        raise ValueError("%s is out of range" % field)
    return value


def load_config(path):
    """Read a JSON config and reject ambiguous or unsafe local demo settings."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config must be an object")
    host = raw.get("host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("gateway binds to loopback only; add authentication before exposing it")
    backends = []
    names = set()
    for item in raw.get("backends", []):
        if not isinstance(item, dict):
            raise ValueError("each backend must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in names:
            raise ValueError("backend names must be unique 1-64 character identifiers")
        names.add(name)
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("backend %s needs an HTTP URL" % name)
        adapter = item.get("adapter", "contract")
        if adapter not in ("contract", "ollama"):
            raise ValueError("backend %s has an unsupported adapter" % name)
        models = item.get("models", [])
        if not isinstance(models, list) or not models or not all(isinstance(m, str) and m for m in models):
            raise ValueError("backend %s needs at least one model" % name)
        weight = item.get("weight", 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < weight <= 100:
            raise ValueError("backend %s weight must be in (0, 100]" % name)
        backends.append(BackendConfig(
            name=name,
            url=url.rstrip("/"),
            adapter=adapter,
            models=tuple(models),
            capacity=_positive_int(item.get("capacity", 4), "capacity"),
            weight=float(weight),
            timeout_ms=_positive_int(item.get("timeout_ms", 1200), "timeout_ms"),
        ))
    if not backends:
        raise ValueError("at least one backend is required")
    port = _positive_int(raw.get("port", 8080), "port", allow_zero=True)
    if port > 65535:
        raise ValueError("port must be at most 65535")
    return GatewayConfig(
        host=host,
        port=port,
        request_timeout_ms=_positive_int(raw.get("request_timeout_ms", 2500), "request_timeout_ms"),
        max_body_bytes=_positive_int(raw.get("max_body_bytes", 65536), "max_body_bytes"),
        max_prompt_chars=_positive_int(raw.get("max_prompt_chars", 8192), "max_prompt_chars"),
        max_tokens=_positive_int(raw.get("max_tokens", 1024), "max_tokens"),
        rate_per_minute=_positive_int(raw.get("rate_per_minute", 120), "rate_per_minute"),
        rate_burst=_positive_int(raw.get("rate_burst", 20), "rate_burst"),
        failure_threshold=_positive_int(raw.get("failure_threshold", 2), "failure_threshold"),
        circuit_cooldown_ms=_positive_int(raw.get("circuit_cooldown_ms", 1000), "circuit_cooldown_ms"),
        backends=tuple(backends),
    )
