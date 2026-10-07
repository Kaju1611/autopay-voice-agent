import logging
import time
import uuid
from collections import defaultdict, deque

from shared.utils import logging as slog

logger = logging.getLogger("latency")

# p95 targets in ms for the hot path of a live call (callers notice silence after ~1s)
LATENCY_BUDGETS_MS = {
    "POST /webhooks/voice/function-call": 1000,
    "POST /internal/functions/execute": 900,
    "POST /payments/{payment_id}/retry": 700,
    "GET /payments/{payment_id}": 100,
    "POST /recovery/{customer_id}": 100,
}
SKIP_PATHS = {"/health", "/metrics", "/favicon.ico"}


class LatencyRecorder:
    def __init__(self, window: int = 500):
        self._data: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._counts: dict[str, int] = defaultdict(int)
        self.counters: dict[str, int] = defaultdict(int)

    def observe(self, name: str, ms: float) -> None:
        self._data[name].append(ms)
        self._counts[name] += 1

    def incr(self, name: str, n: int = 1) -> None:
        self.counters[name] += n

    @staticmethod
    def _pct(sorted_vals: list[float], p: float) -> float:
        if not sorted_vals:
            return 0.0
        k = min(len(sorted_vals) - 1, int(round((p / 100) * (len(sorted_vals) - 1))))
        return round(sorted_vals[k], 1)

    def snapshot(self) -> dict:
        routes = {}
        for name, vals in self._data.items():
            s = sorted(vals)
            p95 = self._pct(s, 95)
            budget = LATENCY_BUDGETS_MS.get(name)
            routes[name] = {"count": self._counts[name], "avg": round(sum(s) / len(s), 1),
                            "p50": self._pct(s, 50), "p95": p95, "p99": self._pct(s, 99), "max": round(s[-1], 1),
                            "budget_p95": budget, "within_budget": (p95 <= budget) if budget else None}
        return {"routes": routes, "counters": dict(self.counters)}


recorder = LatencyRecorder()


class Timer:
    """with Timer() as t: ...   then read t.ms"""
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self.start) * 1000


class ObservabilityMiddleware:
    """Pure ASGI middleware (lowest overhead): request/correlation IDs, Server-Timing header, per-route latency, slow-request log."""

    def __init__(self, app, service: str, slow_ms: int = 500):
        self.app, self.service, self.slow_ms = app, service, slow_ms

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        rid = headers.get("x-request-id") or uuid.uuid4().hex[:16]
        cid = headers.get("x-correlation-id") or rid
        slog.reset_ctx()
        slog.bind(request_id=rid, correlation_id=cid)
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                dur = (time.perf_counter() - start) * 1000
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", rid.encode()), (b"x-correlation-id", cid.encode()),
                    (b"server-timing", f"app;dur={dur:.1f}".encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            dur = (time.perf_counter() - start) * 1000
            path = scope["path"]
            route = scope.get("route")
            key = f'{scope["method"]} {getattr(route, "path", path)}'
            if path not in SKIP_PATHS:
                recorder.observe(key, dur)
                budget = LATENCY_BUDGETS_MS.get(key)
                if budget and dur > budget:
                    recorder.incr("latency_budget_violations")
                level = logging.WARNING if dur > self.slow_ms else logging.INFO
                slog.log(logger, "request", level, method=scope["method"], path=path,
                         status=status, duration_ms=round(dur, 1), slow=dur > self.slow_ms)