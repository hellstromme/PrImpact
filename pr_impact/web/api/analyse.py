"""REST endpoints for triggering and polling analysis runs.

POST /api/analyse                     — spawn a new primpact analyse subprocess
GET  /api/analyse/{run_id}/status     — poll for completion
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class _JobEntry(TypedDict):
    status: Literal["pending", "complete", "failed"]
    error: str | None


# In-memory job status registry (sufficient for v1.0 single-user local server)
_job_status: dict[str, _JobEntry] = {}


class AnalyseRequest(BaseModel):
    repo: str
    pr_number: int | None = None
    base_sha: str | None = None
    head_sha: str | None = None


@router.post("/analyse")
async def trigger_analyse(body: AnalyseRequest, request: Request) -> dict:
    """Spawn a primpact analyse subprocess and return immediately with a run ID."""
    run_id = str(uuid.uuid4())
    db_path: str | None = getattr(request.app.state, "db_path", None)
    _job_status[run_id] = {"status": "pending", "error": None}

    # Build the command
    cmd: list[str] = [sys.executable, "-m", "pr_impact.cli", "analyse", "--repo", body.repo, "--run-id", run_id, "--verdict"]
    if body.pr_number is not None:
        cmd += ["--pr", str(body.pr_number)]
    elif body.base_sha and body.head_sha:
        cmd += ["--base", body.base_sha, "--head", body.head_sha]
    else:
        raise HTTPException(status_code=422, detail={"error": "Provide pr_number or both base_sha and head_sha"})
    if db_path:
        cmd += ["--history-db", db_path]

    asyncio.create_task(_run_subprocess(run_id, cmd))
    return {"run_id": run_id, "status": "pending"}


@router.get("/analyse/{run_id}/status")
def get_status(run_id: str) -> dict:
    """Poll the status of a triggered analysis run."""
    entry = _job_status.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail={"error": "Unknown run ID"})
    return {"run_id": run_id, "status": entry["status"], "error": entry["error"]}


async def _run_subprocess(run_id: str, cmd: list[str]) -> None:
    """Execute the analysis subprocess and update job status on completion."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()
        if proc.returncode == 0:
            _job_status[run_id] = {"status": "complete", "error": None}
        else:
            stderr_text = stderr_bytes.decode(errors="replace").strip() if stderr_bytes else ""
            _job_status[run_id] = {"status": "failed", "error": stderr_text or None}
    except Exception as exc:
        _job_status[run_id] = {"status": "failed", "error": str(exc)}
