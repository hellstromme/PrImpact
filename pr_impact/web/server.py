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


def create_app(db_path: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Path to the SQLite history database. Defaults to
                 PRIMPACT_DB_PATH env var or .primpact/history.db.
    """
    resolved_db = db_path or os.environ.get("PRIMPACT_DB_PATH", _DEFAULT_DB)

    app = FastAPI(
        title="PrImpact API",
        description="REST API for browsing and triggering PrImpact analyses",
        version="1.0.0",
    )

    # Store db_path on app state so route handlers can read it via request.app.state
    app.state.db_path = resolved_db

    # Allowed origins are read from the CORS_ORIGINS env var (comma-separated).
    # Defaults to localhost Vite dev server origins for local development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(runs_router, prefix="/api")
    app.include_router(analyse_router, prefix="/api")
    app.include_router(snippet_router, prefix="/api")
    app.include_router(config_router, prefix="/api")

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

    app = create_app(db_path=db_path)
    # Replace the plain lifespan with the one that starts the worker
    app.router.lifespan_context = _lifespan
    app.state.repos_dir = resolved_repos

    app.include_router(webhook_router)
    return app


async def _webhook_worker(queue: asyncio.Queue, app: FastAPI) -> None:
    """Drain the webhook job queue sequentially."""
    from ..history import load_run
    from ..reporter import render_markdown
    from ..webhook import (
        WebhookJob,
        ensure_repo,
        post_github_comment,
        post_gitlab_comment,
    )

    while True:
        job: WebhookJob = await queue.get()
        try:
            await _process_webhook_job(job, app, load_run, render_markdown,
                                       ensure_repo, post_github_comment,
                                       post_gitlab_comment)
        except Exception as exc:
            # Never let a job failure crash the worker
            print(f"[primpact server] webhook job failed: {exc}", file=sys.stderr)
        finally:
            queue.task_done()


async def _process_webhook_job(job, app, load_run, render_markdown,
                                ensure_repo, post_github_comment,
                                post_gitlab_comment) -> None:
    """Clone repo → analyse → post comment for one WebhookJob."""
    import uuid

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
    _, stderr_bytes = await proc.communicate()
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
