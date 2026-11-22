"""Single-user login: signed session cookie, constant-time password check, and a failed-login limit."""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import Request

from stockresearch.config import Settings

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


class NotAuthenticated(Exception):
    pass


class LoginLimiter:
    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        q = self._failures[key]
        cutoff = time.monotonic() - WINDOW_SECONDS
        while q and q[0] < cutoff:
            q.popleft()
        return len(q) >= MAX_FAILURES

    def fail(self, key: str) -> None:
        self._failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


def check_credentials(settings: Settings, user: str, password: str) -> bool:
    if not settings.login_configured:
        return False
    ok_user = hmac.compare_digest(user.encode(), settings.app_user.encode())
    ok_pass = hmac.compare_digest(password.encode(), settings.app_password.get_secret_value().encode())
    return ok_user and ok_pass


def require_user(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise NotAuthenticated()
    return str(user)


def same_origin(request: Request) -> bool:
    """CSRF defence for form posts, on top of the SameSite=strict cookie."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # same-site navigation from some browsers omits both; the strict cookie still protects us
    host = request.headers.get("host", "")
    return origin.split("://", 1)[-1].split("/", 1)[0] == host
