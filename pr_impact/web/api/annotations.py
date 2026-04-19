"""REST endpoints for per-signal mute and reviewer-assignment annotations.

GET  /api/runs/{run_id}/annotations
    Return all annotations for signals in the given run, keyed by signal_key.

POST /api/runs/{run_id}/annotations/{signal_key}
    Upsert a mute or reviewer assignment for a signal.
    Body: { muted?: bool, mute_reason?: str, assigned_to?: str }
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ...history import (
    compute_signal_key,
    load_run,
    load_run_summary,
    load_signal_annotations,
    save_signal_annotation,
)

router = APIRouter()


def _db_path(request: Request) -> str:
    return request.app.state.db_path


def _signal_keys_for_run(run_uuid: str, db_path: str) -> tuple[str, list[tuple[str, str]]]:
    """Load the run and return (repo_path, [(signal_key, kind), ...]).

    Raises HTTPException 404 if the run is not found.
    """
    summary = load_run_summary(db_path, run_uuid)
    if summary is None:
        raise HTTPException(status_code=404, detail={"error": "Run not found"})

    report = load_run(db_path, run_uuid)
    if report is None:
        raise HTTPException(status_code=404, detail={"error": "Run not found"})

    pairs: list[tuple[str, str]] = []
    for sig in report.ai_analysis.security_signals:
        key = compute_signal_key(
            "signal", sig.location.file, sig.signal_type, sig.description
        )
        pairs.append((key, "signal"))
    for dep in report.dependency_issues:
        key = compute_signal_key("dep", dep.package_name, dep.issue_type, dep.description)
        pairs.append((key, "dep"))

    return summary.repo_path, pairs


@router.get("/runs/{run_id}/annotations")
def get_annotations(run_id: str, request: Request) -> dict:
    """Return all annotations for signals in this run, keyed by signal_key."""
    db_path = _db_path(request)
    repo_path, pairs = _signal_keys_for_run(run_id, db_path)
    signal_keys = [k for k, _ in pairs]
    annotations = load_signal_annotations(db_path, repo_path, signal_keys)
    return annotations


class AnnotationBody(BaseModel):
    muted: bool | None = None
    mute_reason: str | None = None
    assigned_to: str | None = None


@router.post("/runs/{run_id}/annotations/{signal_key}")
def save_annotation(run_id: str, signal_key: str, body: AnnotationBody, request: Request) -> dict:
    """Upsert a mute or reviewer assignment for a signal."""
    db_path = _db_path(request)
    repo_path, pairs = _signal_keys_for_run(run_id, db_path)
    known_keys = {k for k, _ in pairs}
    if signal_key not in known_keys:
        raise HTTPException(status_code=404, detail={"error": "Signal not found in run"})

    user = getattr(request.state, "user", None)
    user_login: str | None = user["login"] if user else None

    return save_signal_annotation(
        db_path,
        repo_path,
        signal_key,
        muted=body.muted,
        mute_reason=body.mute_reason,
        assigned_to=body.assigned_to,
        muted_by=user_login if body.muted else ("" if body.muted is not None else None),
        assigned_by=user_login if body.assigned_to else ("" if body.assigned_to is not None else None),
    )
