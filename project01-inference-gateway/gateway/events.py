"""Structured, prompt-free operational events."""

import json
import sys
from datetime import datetime, timezone


def emit(event, **fields):
    record = {"time": datetime.now(timezone.utc).isoformat(), "event": event}
    record.update(fields)
    print(json.dumps(record, separators=(",", ":"), sort_keys=True), file=sys.stderr, flush=True)
