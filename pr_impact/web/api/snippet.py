"""Snippet endpoint — returns lines around a specific line in a file at a run's head SHA.

GET /api/runs/{run_id}/snippet?file={path}&line={n}&context=5

Used by the Security tab's code evidence block.
"""

from __future__ import annotations

import git
from fastapi import APIRouter, HTTPException, Query, Request

from ...history import load_run_summary

router = APIRouter()


@router.get("/runs/{run_id}/snippet")
def get_snippet(
    run_id: str,
    request: Request,
    file: str = Query(..., description="File path relative to repo root"),
    line: int = Query(..., ge=1, description="Target line number (1-based)"),
    context: int = Query(5, ge=0, le=20, description="Lines of context around the target line"),
) -> dict:
    """Return lines around a specific line from the file at the run's head SHA."""
    db_path: str = request.app.state.db_path
    summary = load_run_summary(db_path, run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    try:
        repo = git.Repo(summary.repo_path, search_parent_directories=True)
        commit = repo.commit(summary.head_sha)
        blob = commit.tree / file
        content = blob.data_stream.read().decode("utf-8", errors="replace")
    except (git.GitCommandError, KeyError, AttributeError, Exception) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not read '{file}' at commit '{summary.head_sha}': {exc}",
        )

    all_lines = content.splitlines()
    total = len(all_lines)

    # Convert to 0-based index; clamp to valid range
    idx = line - 1
    start_idx = max(0, idx - context)
    end_idx = min(total, idx + context + 1)

    return {
        "lines": all_lines[start_idx:end_idx],
        "start_line": start_idx + 1,   # 1-based
        "highlight_line": line,
        "total_lines": total,
    }
