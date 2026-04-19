"""FastAPI application factories for the PrImpact web server.

Two factories are provided:

create_app()        — local web UI server started by `primpact serve`.
                      Binds to localhost, no webhook endpoints.

create_server_app() — team webhook server started by `primpact server`.
                      Adds POST /webhook/github and /webhook/gitlab endpoints,
                      starts a background asyncio worker that drains the job
                      queue, clones repos, runs analysis, and posts comments.

During development you can also run:

    uvicorn pr_impact.web.server:app --reload

which uses the default db_path from PRIMPACT_DB_PATH or .primpact/history.db.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.analyse import router as analyse_router
from .api.annotations import router as annotations_router
from .api.config import router as config_router
from .api.runs import router as runs_router
from .api.snippet import router as snippet_router

_DEFAULT_DB = os.path.join(".primpact", "history.db")
_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"

_STATIC_DIR = Path(__file__).parent / "static"


def _cors_origins() -> list[str]:
    """Return allowed CORS origins from the CORS_ORIGINS env var, or the dev defaults."""
    raw = os.environ.get("CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app(
    db_path: str | None = None,
    lifespan=None,
    extra_routers: list | None = None,
    auth: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path:       Path to the SQLite history database. Defaults to
                       PRIMPACT_DB_PATH env var or .primpact/history.db.
        lifespan:      Optional asynccontextmanager lifespan for the app.
                       Used by create_server_app() to attach the webhook worker.
        extra_routers: Additional APIRouter instances to include before the SPA
                       catch-all route. create_server_app() passes the webhook
                       router here so it is registered before /{full_path:path}.
        auth:          Enable GitHub OAuth authentication. When True, requires
                       GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, and SESSION_SECRET
                       environment variables.
    """
    resolved_db = db_path or os.environ.get("PRIMPACT_DB_PATH", _DEFAULT_DB)

    app = FastAPI(
        title="PrImpact API",
        description="REST API for browsing and triggering PrImpact analyses",
        version="1.0.1",
        lifespan=lifespan,
    )

    # Store db_path on app state so route handlers can read it via request.app.state
    app.state.db_path = resolved_db
    app.state.auth_enabled = auth

    # Allowed origins are read from the CORS_ORIGINS env var (comma-separated).
    # Defaults to localhost Vite dev server origins for local development.
    # NOTE: allow_origins must never be ["*"] when allow_credentials=True —
    # Starlette raises at startup if you attempt this combination. Only list
    # known frontend origins (the default localhost values are safe).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    if auth:
        from .auth import AuthMiddleware, check_auth_env
        from .api.auth_routes import router as auth_router
        from ..history import purge_expired_sessions

        check_auth_env()
        app.state.session_secret = os.environ["SESSION_SECRET"]
        app.add_middleware(AuthMiddleware)
        app.include_router(auth_router)
        purge_expired_sessions(resolved_db)
    else:
        # Register only /auth/status in no-auth mode so the frontend can detect
        # the deployment type. The full auth_router is NOT included here —
        # registering it would expose /auth/login and /auth/callback which crash
        # without GITHUB_CLIENT_ID / SESSION_SECRET in the environment.
        @app.get("/auth/status")
        async def auth_status_no_auth() -> dict:
            return {"auth_enabled": False, "user": None}

    app.include_router(runs_router, prefix="/api")
    app.include_router(analyse_router, prefix="/api")
    app.include_router(snippet_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(annotations_router, prefix="/api")

    # Extra routers (e.g. webhook) must be registered before the SPA catch-all
    # so that /webhook/* paths are not swallowed by /{full_path:path}.
    for router in (extra_routers or []):
        app.include_router(router)

    # Serve the built React bundle when present.
    # In dev mode, Vite runs separately and proxies /api to this server.
    if _STATIC_DIR.exists():
        assets_dir = _STATIC_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            return FileResponse(str(_STATIC_DIR / "index.html"))

    return app


def create_server_app(
    db_path: str | None = None,
    repos_dir: str | None = None,
) -> FastAPI:
    """Create the webhook server application for `primpact server` (Milestone 6).

    Extends create_app() with:
    - POST /webhook/github and /webhook/gitlab endpoints
    - An asyncio.Queue and background worker that processes WebhookJob entries:
      1. Clone / fetch the repo into repos_dir
      2. Run ``primpact analyse`` as a subprocess
      3. Load the completed run from the history DB
      4. Post the Markdown report as a PR / MR comment (upsert)

    Args:
        db_path:   Path to the SQLite history database.
        repos_dir: Root directory for local repo checkouts
                   (default: PRIMPACT_REPOS_DIR env var or ./repos).
    """
    from .api.webhook import router as webhook_router

    resolved_repos = repos_dir or os.environ.get("PRIMPACT_REPOS_DIR", "./repos")

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        queue: asyncio.Queue = asyncio.Queue()
        app.state.webhook_queue = queue
        worker = asyncio.create_task(_webhook_worker(queue, app))
        yield
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    # Pass webhook_router via extra_routers so it is registered before the
    # SPA catch-all route (/{full_path:path}) in create_app().
    app = create_app(db_path=db_path, lifespan=_lifespan, extra_routers=[webhook_router], auth=True)
    app.state.repos_dir = resolved_repos
    return app


async def _webhook_worker(queue: asyncio.Queue, app: FastAPI) -> None:
    """Drain the webhook job queue sequentially."""
    while True:
        job = await queue.get()
        try:
            await _process_webhook_job(job, app)
        except Exception as exc:
            # Never let a job failure crash the worker
            print(f"[primpact server] webhook job failed: {exc}", file=sys.stderr)
        finally:
            queue.task_done()


async def _process_webhook_job(job, app) -> None:
    """Clone repo → analyse → post comment for one WebhookJob."""
    import uuid
    from ..history import load_run
    from ..reporter import render_markdown
    from ..webhook import ensure_repo, post_github_comment, post_gitlab_comment

    # 1. Clone / fetch
    local_path = await asyncio.to_thread(
        ensure_repo,
        job["repos_dir"],
        job["owner"],
        job["repo_name"],
        job["clone_url"],
    )

    # 2. Run analysis subprocess
    run_id = str(uuid.uuid4())
    cmd = [
        sys.executable, "-m", "pr_impact.cli", "analyse",
        "--repo", local_path,
        "--base", job["base_sha"],
        "--head", job["head_sha"],
        "--run-id", run_id,
        "--history-db", job["db_path"],
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("analyse subprocess timed out after 300s")
    if proc.returncode not in (0, 1, 2):
        err = (stderr_bytes or b"").decode(errors="replace").strip()
        raise RuntimeError(f"analyse subprocess failed (rc={proc.returncode}): {err}")

    # 3. Load report from DB
    report = await asyncio.to_thread(load_run, job["db_path"], run_id)
    if report is None:
        raise RuntimeError(f"Report for run {run_id} not found in history DB")

    # 4. Render and post comment
    markdown = render_markdown(report)

    if job["platform"] == "github" and job["github_token"]:
        await asyncio.to_thread(
            post_github_comment,
            job["owner"],
            job["repo_name"],
            job["pr_number"],
            markdown,
            job["github_token"],
        )
    elif job["platform"] == "gitlab" and job["gitlab_token"]:
        await asyncio.to_thread(
            post_gitlab_comment,
            job["project_id"],
            job["pr_number"],
            markdown,
            job["gitlab_token"],
            job.get("gitlab_url", "https://gitlab.com"),
        )


# Module-level app instance for `uvicorn pr_impact.web.server:app`
app = create_app()
