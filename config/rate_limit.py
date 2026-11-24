"""Small in-memory sliding-window rate limiter (per process).

Usage:  @app.post(...)  def f(request: Request, _=Depends(rate_limit("login", 10, 60))): ...
Limits are per client IP and per bucket name. Good enough for a single uvicorn
worker; swap for Redis/slowapi when scaling out.
"""
import os
import time
import threading
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_hits: dict = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    """The visitor's IP, for rate limiting.

    Behind a proxy the connection comes from the proxy, so the visitor's address is in a
    header, and any header the visitor sends can be faked. Which value is trustworthy depends
    on the host, so it's configured (check with GET /client-ip after deploying):

      CLIENT_IP_HEADER=<name>   a header the host sets itself and overwrites
                                (e.g. True-Client-IP or CF-Connecting-IP), used when present
      TRUSTED_PROXY_HOPS=<n>    the number of proxies that append to X-Forwarded-For; the
                                visitor is the n-th address from the right (1 = the last one)
      neither set               the first X-Forwarded-For address: works anywhere, but a
                                visitor can fake it
    """
    header = os.getenv("CLIENT_IP_HEADER", "").strip()
    if header:
        value = request.headers.get(header, "").strip()
        if value:
            return value
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if forwarded:
        try:
            hops = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
        except ValueError:
            hops = 0
        if hops > 0:
            return forwarded[-hops] if len(forwarded) >= hops else forwarded[0]
        return forwarded[0]
    return request.client.host if request.client else "unknown"


_client_ip = client_ip  # older name, kept for callers that import it


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
