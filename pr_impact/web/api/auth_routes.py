"""OAuth 2.0 routes for GitHub authentication.

GET  /auth/login      — Redirect to GitHub authorisation page
GET  /auth/callback   — Exchange OAuth code → access token → session cookie
POST /auth/logout     — Destroy session, clear cookie
GET  /auth/me         — Return current user (protected by AuthMiddleware)
GET  /auth/status     — Always available; returns auth_enabled flag + current user
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ...history import create_session, delete_session, upsert_user
from ..auth import (
    COOKIE_NAME,
    OAUTH_STATE_COOKIE,
    SESSION_TTL_DAYS,
    check_access,
    clear_session_cookie,
    make_session_token,
    set_session_cookie,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    """Redirect the browser to GitHub's OAuth authorisation page."""
    state = secrets.token_hex(16)
    params = urlencode({
        "client_id": os.environ["GITHUB_CLIENT_ID"],
        "scope": "read:user read:org",
        "state": state,
    })
    response = RedirectResponse(f"{_GITHUB_AUTHORIZE_URL}?{params}")
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/callback")
async def callback(request: Request, code: str, state: str) -> Response:
    """Exchange the GitHub OAuth code for a session cookie."""
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state — possible CSRF attack")

    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            _GITHUB_TOKEN_URL,
            data={
                "client_id": os.environ["GITHUB_CLIENT_ID"],
                "client_secret": os.environ["GITHUB_CLIENT_SECRET"],
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        if not token_resp.is_success:
            raise HTTPException(status_code=502, detail="Failed to exchange OAuth code with GitHub")

        token_data = token_resp.json()
        access_token: str | None = token_data.get("access_token")
        if not access_token:
            error_desc = token_data.get("error_description", "unknown OAuth error")
            raise HTTPException(status_code=400, detail=f"GitHub OAuth error: {error_desc}")

        user_resp = await client.get(
            _GITHUB_USER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if not user_resp.is_success:
            raise HTTPException(status_code=502, detail="Failed to fetch GitHub user profile")
        gh_user = user_resp.json()

    github_login: str = gh_user["login"]
    github_id: int = gh_user["id"]
    name: str | None = gh_user.get("name")
    avatar_url: str | None = gh_user.get("avatar_url")

    allowed = await check_access(github_login, access_token)
    if not allowed:
        return HTMLResponse(content=_forbidden_page(github_login), status_code=403)

    db_path: str = request.app.state.db_path
    secret: str = request.app.state.session_secret

    user_id = upsert_user(db_path, github_id, github_login, name, avatar_url)
    token = make_session_token(secret)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    create_session(db_path, user_id, token, expires_at)

    response = RedirectResponse("/", status_code=302)
    set_session_cookie(response, token)
    response.delete_cookie(key=OAUTH_STATE_COOKIE, httponly=True, samesite="lax")
    return response


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    """Destroy the current session and clear the session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        delete_session(request.app.state.db_path, token)
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response


@router.get("/me")
async def me(request: Request) -> dict:
    """Return the current authenticated user. Requires a valid session."""
    return request.state.user


@router.get("/status")
async def status(request: Request) -> dict:
    """Return auth configuration and current user.

    Always reachable regardless of auth mode. The frontend AuthProvider
    fetches this on startup to decide whether to show the login gate.
    """
    auth_enabled: bool = getattr(request.app.state, "auth_enabled", False)
    user = getattr(request.state, "user", None)
    return {"auth_enabled": auth_enabled, "user": user}


def _forbidden_page(login: str) -> str:
    safe_login = login.replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Access Denied \u2014 PrImpact</title>
  <style>body{{font-family:monospace;padding:2rem;max-width:480px;margin:auto}}</style>
</head>
<body>
  <h1>Access Denied</h1>
  <p>Your GitHub account <strong>{safe_login}</strong> is not authorised to access this PrImpact instance.</p>
  <p>Contact the server administrator to request access.</p>
  <p><a href="/auth/login">Try a different account</a></p>
</body>
</html>"""
