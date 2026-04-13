"""REST endpoints for triggering and polling analysis runs.

POST /api/analyse                     — spawn a new primpact analyse subprocess
GET  /api/analyse/{run_id}/status     — poll for completion
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

# In-memory job status registry (sufficient for v1.0 single-user local server)
_job_status: dict[str, Literal["pending", "complete", "failed"]] = {}


class AnalyseRequest(BaseModel):
    repo: str
    pr_number: int | None = None
    base_sha: str | None = None
    head_sha: str | None = None


@router.post("/analyse")
async def trigger_analyse(body: AnalyseRequest, request: Request) -> dict:
    """Spawn a primpact analyse subprocess and return immediately with a run ID."""
    run_id = str(uuid.uuid4())
    db_path: str = request.app.state.db_path
    _job_status[run_id] = "pending"

    # Build the command
    cmd: list[str] = [sys.executable, "-m", "pr_impact.cli", "analyse", "--repo", body.repo, "--run-id", run_id]
    if body.pr_number is not None:
        cmd += ["--pr", str(body.pr_number)]
    elif body.base_sha and body.head_sha:
        cmd += ["--base", body.base_sha, "--head", body.head_sha]
    else:
        raise HTTPException(status_code=422, detail={"error": "Provide pr_number or both base_sha and head_sha"})
    if db_path:
        cmd += ["--history-db", db_path]

    asyncio.get_event_loop().create_task(_run_subprocess(run_id, cmd))
    return {"run_id": run_id, "status": "pending"}


@router.get("/analyse/{run_id}/status")
def get_status(run_id: str) -> dict:
    """Poll the status of a triggered analysis run."""
    status = _job_status.get(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail={"error": "Unknown run ID"})
    return {"run_id": run_id, "status": status}


async def _run_subprocess(run_id: str, cmd: list[str]) -> None:
    """Execute the analysis subprocess and update job status on completion."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        _job_status[run_id] = "complete" if proc.returncode == 0 else "failed"
    except Exception:
        _job_status[run_id] = "failed"
