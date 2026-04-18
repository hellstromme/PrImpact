"""Tests for the annotations REST endpoints.

GET  /api/runs/{run_id}/annotations
POST /api/runs/{run_id}/annotations/{signal_key}
"""

from typing import NamedTuple
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed; skipping API tests")

from fastapi.testclient import TestClient  # noqa: E402

from pr_impact.history import compute_signal_key, save_run  # noqa: E402
from pr_impact.models import AIAnalysis, DependencyIssue  # noqa: E402
from pr_impact.web.server import create_app  # noqa: E402
from tests.helpers import make_report, make_security_signal  # noqa: E402

UNKNOWN_UUID = "00000000-0000-0000-0000-000000000000"


class _ClientFixture(NamedTuple):
    tc: TestClient
    db_path: str
    run_id: str
    signal_key: str
    dep_key: str


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / ".primpact" / "history.db")


@pytest.fixture()
def client(db_path) -> _ClientFixture:
    sig = make_security_signal(file_path="src/auth.py", line_number=10)
    dep = DependencyIssue(
        package_name="evil-pkg",
        issue_type="typosquat",
        description="Suspicious package",
        severity="high",
    )
    report = make_report(
        ai_analysis=AIAnalysis(summary="test", security_signals=[sig]),
        dependency_issues=[dep],
    )
    run_id = save_run(db_path, report, repo_path="/repo")
    app = create_app(db_path=db_path)

    signal_key = compute_signal_key(
        "signal", sig.location.file, sig.signal_type, sig.description
    )
    dep_key = compute_signal_key(
        "dep", dep.package_name, dep.issue_type, dep.description
    )

    return _ClientFixture(
        tc=TestClient(app),
        db_path=db_path,
        run_id=run_id,
        signal_key=signal_key,
        dep_key=dep_key,
    )


# --- GET /api/runs/{run_id}/annotations ---


def test_get_annotations_returns_200(client):
    resp = client.tc.get(f"/api/runs/{client.run_id}/annotations")
    assert resp.status_code == 200


def test_get_annotations_returns_empty_dict_initially(client):
    resp = client.tc.get(f"/api/runs/{client.run_id}/annotations")
    assert resp.json() == {}


def test_get_annotations_404_for_unknown_run(client):
    resp = client.tc.get(f"/api/runs/{UNKNOWN_UUID}/annotations")
    assert resp.status_code == 404
    assert "error" in resp.json()["detail"]


def test_get_annotations_reflects_saved_annotation(client):
    client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.signal_key}",
        json={"muted": True, "mute_reason": "false positive"},
    )
    resp = client.tc.get(f"/api/runs/{client.run_id}/annotations")
    data = resp.json()
    assert client.signal_key in data
    assert data[client.signal_key]["muted"] is True
    assert data[client.signal_key]["mute_reason"] == "false positive"


# --- POST /api/runs/{run_id}/annotations/{signal_key} ---


def test_post_annotation_returns_200(client):
    resp = client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.signal_key}",
        json={"muted": True},
    )
    assert resp.status_code == 200


def test_post_annotation_muted_true_saves_correctly(client):
    resp = client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.signal_key}",
        json={"muted": True, "mute_reason": "wontfix"},
    )
    data = resp.json()
    assert data["muted"] is True
    assert data["mute_reason"] == "wontfix"
    assert data["signal_key"] == client.signal_key


def test_post_annotation_404_for_unknown_run(client):
    resp = client.tc.post(
        f"/api/runs/{UNKNOWN_UUID}/annotations/{client.signal_key}",
        json={"muted": True},
    )
    assert resp.status_code == 404
    assert "error" in resp.json()["detail"]


def test_post_annotation_404_for_unknown_signal_key(client):
    resp = client.tc.post(
        f"/api/runs/{client.run_id}/annotations/0000000000000000",
        json={"muted": True},
    )
    assert resp.status_code == 404
    assert "error" in resp.json()["detail"]


def test_post_annotation_invalid_muted_type_returns_422(client):
    # A nested object cannot be coerced to bool — Pydantic v2 must reject it
    resp = client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.signal_key}",
        json={"muted": {"nested": "value"}},
    )
    assert resp.status_code == 422


def test_post_annotation_empty_body_returns_200(client):
    """Empty body (all fields None) is a valid no-op upsert."""
    resp = client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.signal_key}",
        json={},
    )
    assert resp.status_code == 200


def test_post_annotation_returns_timestamp(client):
    resp = client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.signal_key}",
        json={"muted": False},
    )
    data = resp.json()
    assert "updated_at" in data
    assert data["updated_at"] is not None


def test_post_annotation_dep_signal_key_recognised(client):
    """Dependency-issue signal_key (computed with kind='dep') is accepted."""
    resp = client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.dep_key}",
        json={"assigned_to": "alice"},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == "alice"


def test_post_annotation_clears_assignee_with_empty_string(client):
    """assigned_to='' (empty string) clears an existing assignment (stored as null)."""
    client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.signal_key}",
        json={"assigned_to": "bob"},
    )
    resp = client.tc.post(
        f"/api/runs/{client.run_id}/annotations/{client.signal_key}",
        json={"assigned_to": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] is None


def test_post_annotation_db_write_fail_returns_500(db_path):
    """If save_signal_annotation raises, the endpoint returns 500."""
    sig = make_security_signal()
    report = make_report(ai_analysis=AIAnalysis(summary="test", security_signals=[sig]))
    run_id = save_run(db_path, report, repo_path="/repo")
    app = create_app(db_path=db_path)
    signal_key = compute_signal_key(
        "signal", sig.location.file, sig.signal_type, sig.description
    )
    tc = TestClient(app, raise_server_exceptions=False)
    with patch(
        "pr_impact.web.api.annotations.save_signal_annotation",
        side_effect=Exception("db error"),
    ):
        resp = tc.post(
            f"/api/runs/{run_id}/annotations/{signal_key}",
            json={"muted": True},
        )
    assert resp.status_code == 500
