"""FastAPI application: server-rendered pages. Pages read the database only; they never call external sources."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from stockresearch.container import Container
from stockresearch.web import views
from stockresearch.web.auth import LoginLimiter, NotAuthenticated

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'self'; img-src 'self' data:; script-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # same-origin, not no-referrer: with no-referrer browsers send "Origin: null" on form posts, which the CSRF
    # check must reject. Outbound links carry rel="noreferrer", so nothing leaks to other sites either way.
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app(container: Container | None = None) -> FastAPI:
    if container is None:
        from stockresearch.container import build

        container = build()
    settings = container.settings
    secret = settings.session_secret.get_secret_value()
    if not settings.login_configured:
        log.warning("APP_PASSWORD (12+ chars) and SESSION_SECRET (32+ chars) are not set: login is disabled")
        secret = secret or secrets.token_urlsafe(48)

    app = FastAPI(title="Stock Research", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = container
    app.state.limiter = LoginLimiter()
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="sr_session", max_age=14 * 24 * 3600,
                       same_site="strict", https_only=settings.cookie_secure)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.exception_handler(NotAuthenticated)
    async def to_login(request: Request, _exc: NotAuthenticated) -> RedirectResponse:
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.include_router(views.router)
    return app


def app_factory() -> FastAPI:
    """Entry point for uvicorn: `uvicorn stockresearch.web.app:app_factory --factory`."""
    from stockresearch import logging_setup

    logging_setup.configure()
    return create_app()
