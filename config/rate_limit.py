"""Small in-memory sliding-window rate limiter (per process).

Usage:  @app.post(...)  def f(request: Request, _=Depends(rate_limit("login", 10, 60))): ...
Limits are per client IP and per bucket name. Good enough for a single uvicorn
worker; swap for Redis/slowapi when scaling out.
"""
import time
import threading
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_hits: dict = defaultdict(deque)
_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, max_calls: int, window_seconds: int):
    def dependency(request: Request):
        key = (bucket, _client_ip(request))
        now = time.monotonic()
        with _lock:
            q = _hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= max_calls:
                retry = max(1, int(window_seconds - (now - q[0])))
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many requests. Try again in {retry} seconds.",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)
    return dependency


def reset_rate_limits():
    """Test helper."""
    with _lock:
        _hits.clear()
