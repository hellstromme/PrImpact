"""REST endpoints for browsing persisted analysis runs.

GET /api/runs             — paginated list of RunSummary for a repo
GET /api/runs/{run_id}    — single RunSummary
GET /api/runs/{run_id}/report — full ImpactReport JSON
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, HTTPException, Query, Request

from ...history import clear_history, load_run, load_run_summary, load_runs

router = APIRouter()


def _db_path(request: Request) -> str:
    return request.app.state.db_path


@router.get("/runs")
def list_runs(
    request: Request,
    repo: str = Query(..., description="Absolute path to the repository"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """Return a paginated list of run summaries for the given repo."""
    summaries = load_runs(_db_path(request), repo, limit=limit, offset=offset)
    return [dataclasses.asdict(s) for s in summaries]


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    """Return a single RunSummary by UUID."""
    summary = load_run_summary(_db_path(request), run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail={"error": "Run not found"})
    return dataclasses.asdict(summary)


@router.delete("/history")
def delete_history(
    request: Request,
    repo: str = Query(..., description="Absolute path to the repository"),
) -> dict:
    """Delete all recorded runs for the given repo."""
    try:
        clear_history(_db_path(request), repo)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc
    return {"deleted": True}


@router.get("/runs/{run_id}/report")
def get_report(run_id: str, request: Request) -> dict:
    """Return the full ImpactReport JSON for a run."""
    report = load_run(_db_path(request), run_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"error": "Run not found"})
    return dataclasses.asdict(report)
