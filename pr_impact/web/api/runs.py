"""REST endpoints for browsing persisted analysis runs.

GET /api/runs             — paginated list of RunSummary for a repo
GET /api/runs/{run_id}    — single RunSummary
GET /api/runs/{run_id}/report — full ImpactReport JSON
"""

from __future__ import annotations

import dataclasses
import subprocess

from fastapi import APIRouter, HTTPException, Query, Request

from ...history import load_run, load_run_summary, load_runs
from ...models import RunSummary

router = APIRouter()


def _db_path(request: Request) -> str:
    return request.app.state.db_path


def _check_merged(repo_path: str, head_sha: str) -> bool:
    """Return True if head_sha is an ancestor of the remote main branch.

    Checks origin/main and origin/master (remote-tracking refs) so the result
    reflects what has landed on the remote without requiring a local git pull.
    Falls back to local main/master for repos with no remote.
    """
    for ref in ("origin/main", "origin/master", "main", "master"):
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "merge-base", "--is-ancestor", head_sha, ref],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _enrich(summary: RunSummary) -> RunSummary:
    summary.merged = _check_merged(summary.repo_path, summary.head_sha)
    return summary


@router.get("/runs")
def list_runs(
    request: Request,
    repo: str = Query(..., description="Absolute path to the repository"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """Return a paginated list of run summaries for the given repo."""
    summaries = load_runs(_db_path(request), repo, limit=limit, offset=offset)
    return [dataclasses.asdict(_enrich(s)) for s in summaries]


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    """Return a single RunSummary by UUID."""
    summary = load_run_summary(_db_path(request), run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail={"error": "Run not found"})
    return dataclasses.asdict(_enrich(summary))


@router.get("/runs/{run_id}/report")
def get_report(run_id: str, request: Request) -> dict:
    """Return the full ImpactReport JSON for a run."""
    report = load_run(_db_path(request), run_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"error": "Run not found"})
    return dataclasses.asdict(report)
