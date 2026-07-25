"""Rate limiting for the auth endpoints.

Two abuses this closes:

- **Password guessing** on /login. Argon2 verification is deliberately slow, so
  an unthrottled login form is both a credential-stuffing target and a cheap way
  to pin the CPU.
- **Mail flooding** via /register and /forgot. Each one sends through the
  project's single Postmark server, so an attacker could burn the send quota and
  the sender reputation — which would break verification mail for *every* user —
  or use us to mail-bomb a third party.

Counters live in this process's memory: a deque of hit timestamps per key,
pruned on read. That is sufficient for the single-worker uvicorn deployment and
keeps the dependency list short. **Running more than one worker (or more than
one container) would give each its own counters and multiply every limit —
switch to Redis before scaling out.**

Limits are applied per client IP *and* per account, because either alone is
evadable: one IP hammering many accounts, or a botnet hammering one account.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request


@dataclass(frozen=True)
class Limit:
    """At most `max_hits` within a rolling window of `window_seconds`."""

    max_hits: int
    window_seconds: int


# Login attempts. Per-email is tighter than per-IP so a shared address (office,
# mobile carrier NAT) doesn't lock people out on someone else's typos.
LOGIN_PER_IP = Limit(max_hits=10, window_seconds=15 * 60)
LOGIN_PER_EMAIL = Limit(max_hits=5, window_seconds=15 * 60)

# Anything that causes an outbound email (register, forgot).
EMAIL_PER_IP = Limit(max_hits=3, window_seconds=60 * 60)
EMAIL_PER_EMAIL = Limit(max_hits=3, window_seconds=60 * 60)

# Reset-token submissions — signed tokens aren't guessable, this just keeps the
# endpoint from being a free Argon2-hashing service.
RESET_PER_IP = Limit(max_hits=10, window_seconds=15 * 60)


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: Limit, *, now: float | None = None) -> bool:
        """Record an attempt against `key` and return whether it is permitted.

        Refused attempts are not recorded, so a blocked caller doesn't extend
        their own penalty by retrying — the window drains on schedule.
        """
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        cutoff = now - limit.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit.max_hits:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        """Drop all counters. For tests, and for an operator unlocking someone."""
        self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    """The caller's IP, honouring exactly one trusted reverse proxy (Caddy).

    Takes the **last** X-Forwarded-For entry, not the first: a client can send
    a forged XFF header, and the proxy appends the real peer address after it.
    The first entry is therefore attacker-controlled and the last is not.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _check(request: Request, email: str, ip_limit: Limit,
           email_limit: Limit | None, bucket: str) -> bool:
    ip_ok = limiter.allow(f"{bucket}:ip:{client_ip(request)}", ip_limit)
    if email_limit is None:
        return ip_ok
    # Evaluate both so one attempt consumes one hit from each counter,
    # regardless of which one refuses.
    email_ok = limiter.allow(f"{bucket}:email:{email.strip().lower()}", email_limit)
    return ip_ok and email_ok


def allow_login(request: Request, email: str) -> bool:
    return _check(request, email, LOGIN_PER_IP, LOGIN_PER_EMAIL, "login")


def allow_email_send(request: Request, email: str) -> bool:
    return _check(request, email, EMAIL_PER_IP, EMAIL_PER_EMAIL, "mail")


def allow_reset_attempt(request: Request) -> bool:
    return limiter.allow(f"reset:ip:{client_ip(request)}", RESET_PER_IP)
