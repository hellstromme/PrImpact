"""FastAPI application factory for the PrImpact web server.

Usage:
    from pr_impact.web.server import create_app
    app = create_app(db_path="/path/to/history.db")

The `primpact serve` CLI command (Milestone 2) will use create_app() to start
the server via uvicorn. During development, you can also run:

    uvicorn pr_impact.web.server:app --reload

which uses the default db_path from the environment variable PRIMPACT_DB_PATH,
or falls back to .primpact/history.db in the current directory.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.analyse import router as analyse_router
from .api.runs import router as runs_router

_DEFAULT_DB = os.path.join(".primpact", "history.db")
_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"


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

    return app


# Module-level app instance for `uvicorn pr_impact.web.server:app`
app = create_app()
