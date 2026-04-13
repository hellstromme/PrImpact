"""Tests for GET /api/runs/{run_id}/snippet."""

from __future__ import annotations

import dataclasses
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pr_impact.history import save_run
from pr_impact.models import (
    AIAnalysis,
    BlastRadiusEntry,
    ChangedFile,
    ImpactReport,
)
from pr_impact.web.server import create_app


def _minimal_report(repo_path: str) -> ImpactReport:
    return ImpactReport(
        pr_title="Test PR",
        base_sha="abc0001",
        head_sha="abc0002",
        changed_files=[
            ChangedFile(
                path="hello.py",
                language="python",
                diff="@@ -1 +1 @@\n-old\n+new",
                content_before="old\n",
                content_after="new\n",
            )
        ],
        blast_radius=[],
        interface_changes=[],
        ai_analysis=AIAnalysis(),
    )


class TestSnippetEndpoint:
    """Tests for the snippet endpoint.

    The endpoint requires a real git repo with the head_sha present.
    When that is absent, it should return 422.
    When the run itself is absent, it should return 404.
    """

    def test_unknown_run_returns_404(self, tmp_path: Path) -> None:
        db = str(tmp_path / "h.db")
        app = create_app(db_path=db)
        client = TestClient(app)
        resp = client.get("/api/runs/nonexistent-uuid/snippet?file=foo.py&line=1")
        assert resp.status_code == 404

    def test_known_run_invalid_sha_returns_422(self, tmp_path: Path) -> None:
        """A valid run whose head_sha doesn't exist in any reachable repo → 422."""
        db = str(tmp_path / "h.db")
        report = _minimal_report(str(tmp_path))
        run_id = save_run(db, report, str(tmp_path))

        app = create_app(db_path=db)
        client = TestClient(app)
        resp = client.get(f"/api/runs/{run_id}/snippet?file=hello.py&line=1")
        # head_sha 'abc0002' doesn't exist → 422
        assert resp.status_code == 422

    def test_missing_line_param_returns_422(self, tmp_path: Path) -> None:
        db = str(tmp_path / "h.db")
        report = _minimal_report(str(tmp_path))
        run_id = save_run(db, report, str(tmp_path))

        app = create_app(db_path=db)
        client = TestClient(app)
        resp = client.get(f"/api/runs/{run_id}/snippet?file=hello.py")
        assert resp.status_code == 422  # FastAPI validation: missing required param
