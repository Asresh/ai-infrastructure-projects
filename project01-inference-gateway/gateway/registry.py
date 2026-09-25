"""Thread-safe backend selection, capacity and circuit state."""

import threading
import time


class BackendState:
    def __init__(self, config):
        self.config = config
        self.inflight = 0
        self.ewma_ms = 50.0
        self.failures = 0
        self.circuit = "closed"
        self.open_until = 0.0
        self.probe_inflight = False


class BackendRegistry:
    def __init__(self, backends, failure_threshold, cooldown_ms, clock=time.monotonic):
        self._states = {backend.name: BackendState(backend) for backend in backends}
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown_ms / 1000.0
        self.clock = clock
        self._lock = threading.Lock()

    def supports(self, model):
        return any(model in state.config.models for state in self._states.values())

    def any_ready(self):
        """A cooled circuit is ready for its next probe even before traffic arrives."""
        with self._lock:
            now = self.clock()
            return any(state.circuit != "open" or now >= state.open_until
                       for state in self._states.values())

    def reserve(self, model, excluded):
        """Atomically choose and reserve a backend; return its immutable config."""
        with self._lock:
            now = self.clock()
            choices = []
            for state in self._states.values():
                cfg = state.config
                if cfg.name in excluded or model not in cfg.models or state.inflight >= cfg.capacity:
                    continue
                if state.circuit == "open":
                    if now < state.open_until:
                        continue
                    state.circuit = "half_open"
                    state.probe_inflight = False
                if state.circuit == "half_open" and state.probe_inflight:
                    continue
                # A cooled-down backend gets one probe so it can recover.
                score = -1.0 if state.circuit == "half_open" else (
                    state.ewma_ms * (1.0 + state.inflight / cfg.capacity) / cfg.weight
                )
                choices.append((score, cfg.name, state))
            if not choices:
                return None
            _, _, selected = min(choices)
            selected.inflight += 1
            if selected.circuit == "half_open":
                selected.probe_inflight = True
            return selected.config

    def finish(self, name, success, elapsed_ms):
        with self._lock:
            state = self._states[name]
            state.inflight -= 1
            if state.inflight < 0:
                raise RuntimeError("backend reservation underflow")
            if success:
                state.ewma_ms = 0.3 * elapsed_ms + 0.7 * state.ewma_ms
                state.failures = 0
                state.circuit = "closed"
                state.probe_inflight = False
            else:
                state.failures += 1
                if state.circuit == "half_open" or state.failures >= self.failure_threshold:
                    state.circuit = "open"
                    state.open_until = self.clock() + self.cooldown
                    state.probe_inflight = False

    def snapshot(self):
        with self._lock:
            return [
                {
                    "name": state.config.name,
                    "models": list(state.config.models),
                    "adapter": state.config.adapter,
                    "capacity": state.config.capacity,
                    "inflight": state.inflight,
                    "ewma_ms": round(state.ewma_ms, 2),
                    "circuit": state.circuit,
                    "failures": state.failures,
                }
                for state in self._states.values()
            ]
