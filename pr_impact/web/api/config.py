"""GET /api/config — returns the active .primpact.yml config for the current repo."""

import dataclasses
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/config")
async def get_config(repo: str = Query(..., description="Absolute path to the repo")) -> JSONResponse:
    """Return the parsed .primpact.yml config for *repo*, or a 404 if absent.

    Assumes a trusted local client — *repo* is an absolute path to a local
    repository directory. The endpoint only reads files named primpact.toml or
    .primpact.toml, so path traversal can at most produce a 404, not arbitrary
    file reads.
    """
    try:
        from pr_impact.config_file import load_config_file
        config = load_config_file(repo)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    if config is None:
        return JSONResponse({"error": "No .primpact.yml found"}, status_code=404)

    return JSONResponse({
        "path": str(Path(repo) / ".primpact.yml"),
        **dataclasses.asdict(config),
    })
