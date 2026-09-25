"""HTTP adapters for the local contract and an optional local model server."""

import json
from urllib import error, request


class BackendFailure(Exception):
    pass


def generate(backend, model, prompt, max_tokens, timeout_seconds):
    if backend.adapter == "ollama":
        url = backend.url + "/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
    else:
        url = backend.url + "/infer"
        payload = {"model": model, "prompt": prompt, "max_tokens": max_tokens}
    encoded = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=encoded, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise BackendFailure("backend returned HTTP %d" % response.status)
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise BackendFailure("backend response exceeds 1 MiB")
    except error.HTTPError as exc:
        raise BackendFailure("backend returned HTTP %d" % exc.code) from exc
    except (error.URLError, OSError, TimeoutError) as exc:
        raise BackendFailure("backend transport failed: %s" % type(exc).__name__) from exc
    try:
        result = json.loads(body)
        if backend.adapter == "ollama":
            output = result["response"]
            usage = {
                "input_tokens": result.get("prompt_eval_count", 0),
                "output_tokens": result.get("eval_count", 0),
            }
        else:
            output = result["output"]
            usage = result.get("usage", {})
        if not isinstance(output, str) or not isinstance(usage, dict):
            raise ValueError("invalid output or usage")
        return output, usage
    except (KeyError, ValueError, TypeError) as exc:
        raise BackendFailure("backend returned an invalid JSON contract") from exc
