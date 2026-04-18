"""Authentication middleware, session helpers, and access control for the PrImpact web server.

Access control modes are inferred from environment variables:
  Neither set          → open (any authenticated GitHub user allowed)
  PRIMPACT_ALLOWED_ORG → org mode (must be a member of that GitHub org)
  PRIMPACT_ALLOWED_USERS → user allowlist (comma-separated GitHub logins)
  Both set             → union (org members OR listed users)

Only /api/* routes and /auth/me require authentication. Static assets, the
React SPA shell, and the auth flow endpoints are always served without a
session check.
"""

from __future__ import annotations

import os
import secrets

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.middleware.base import BaseHTTPMiddleware

from ..history import get_session, get_user

COOKIE_NAME = "primpact_session"
OAUTH_STATE_COOKIE = "primpact_oauth_state"
SESSION_TTL_DAYS = 30

_PROTECTED_PREFIXES = ("/api/",)
_PROTECTED_EXACT = {"/auth/me"}


# --- Session cookie helpers ---

def make_session_token(secret: str) -> str:
    """Return a signed opaque token suitable for use as a session cookie value."""
    raw = secrets.token_hex(32)
    return TimestampSigner(secret).sign(raw).decode()


def verify_session_token(secret: str, token: str) -> bool:
    """Return True if the token has a valid signature and is not older than SESSION_TTL_DAYS."""
    try:
        TimestampSigner(secret).unsign(token, max_age=SESSION_TTL_DAYS * 86400)
        return True
    except (BadSignature, SignatureExpired):
        return False


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 86400,
        secure=os.environ.get("PRIMPACT_SECURE_COOKIES", "").lower() == "true",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(key=COOKIE_NAME, httponly=True, samesite="lax")


# --- Access control ---

async def check_access(github_login: str, github_token: str) -> bool:
    """Return True if this GitHub user is permitted to access this PrImpact instance.

    Evaluates PRIMPACT_ALLOWED_ORG and PRIMPACT_ALLOWED_USERS env vars.
    Returns True immediately when neither is configured (open mode).
    """
    allowed_org = os.environ.get("PRIMPACT_ALLOWED_ORG", "").strip()
    raw_users = os.environ.get("PRIMPACT_ALLOWED_USERS", "").strip()
    allowed_users = {u.strip() for u in raw_users.split(",") if u.strip()}

    if not allowed_org and not allowed_users:
        return True

    if allowed_users and github_login in allowed_users:
        return True

    if allowed_org:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://api.github.com/orgs/{allowed_org}/members/{github_login}",
                    headers={
                        "Authorization": f"Bearer {github_token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if resp.status_code == 204:
                    return True
        except Exception:
            pass

    return False


# --- Request session resolution ---

def _resolve_user(token: str | None, secret: str, db_path: str) -> dict | None:
    """Validate a session cookie value and return the user dict, or None."""
    if not token:
        return None
    if not verify_session_token(secret, token):
        return None
    user_id = get_session(db_path, token)
    if user_id is None:
        return None
    return get_user(db_path, user_id)


# --- Middleware ---

class AuthMiddleware(BaseHTTPMiddleware):
    """Protect /api/* and /auth/me; attach request.state.user on success."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        needs_auth = path in _PROTECTED_EXACT or any(path.startswith(p) for p in _PROTECTED_PREFIXES)
        if not needs_auth:
            return await call_next(request)

        db_path: str = request.app.state.db_path
        secret: str = request.app.state.session_secret
        token = request.cookies.get(COOKIE_NAME)
        user = _resolve_user(token, secret, db_path)
        if user is None:
            return JSONResponse({"detail": "Unauthenticated"}, status_code=401)
        request.state.user = user
        return await call_next(request)


# --- Startup validation ---

def check_auth_env() -> None:
    """Raise SystemExit with a clear message if required auth env vars are missing."""
    missing = [v for v in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "SESSION_SECRET") if not os.environ.get(v)]
    if missing:
        import sys
        print(
            f"[primpact] Auth is enabled but the following environment variables are not set: "
            f"{', '.join(missing)}. Set them before starting the server.",
            file=sys.stderr,
        )
        sys.exit(1)
