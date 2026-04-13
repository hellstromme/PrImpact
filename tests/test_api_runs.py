"""Tests for the v1.0 REST API: /api/runs and /api/runs/{id}/report endpoints.

These tests require fastapi and httpx (via pytest-httpx or starlette TestClient).
They run against an in-process FastAPI app backed by a tmp-path SQLite DB.
"""

from typing import NamedTuple

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed; skipping API tests")

from fastapi.testclient import TestClient  # noqa: E402

from pr_impact.history import save_run  # noqa: E402
from pr_impact.web.server import create_app  # noqa: E402
from tests.helpers import make_report  # noqa: E402


class _ClientFixture(NamedTuple):
    tc: TestClient
    db_path: str
    ids: list[str]


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / ".primpact" / "history.db")


@pytest.fixture()
def seeded_db(db_path):
    """Seed DB with two runs and return (db_path, [run_id_0, run_id_1])."""
    ids = []
    for i in range(2):
        rid = save_run(db_path, make_report(pr_title=f"PR #{i}"), repo_path="/repo")
        ids.append(rid)
    return db_path, ids


@pytest.fixture()
def client(seeded_db) -> _ClientFixture:
    db, ids = seeded_db
    app = create_app(db_path=db)
    return _ClientFixture(tc=TestClient(app), db_path=db, ids=ids)


# --- GET /api/runs ---


def test_list_runs_returns_200(client):
    resp = client.tc.get("/api/runs", params={"repo": "/repo"})
    assert resp.status_code == 200


def test_list_runs_returns_both_items(client):
    resp = client.tc.get("/api/runs", params={"repo": "/repo"})
    assert len(resp.json()) == 2


def test_list_runs_shape(client):
    resp = client.tc.get("/api/runs", params={"repo": "/repo"})
    item = resp.json()[0]
    for field in ("id", "repo_path", "base_sha", "head_sha", "created_at",
                  "blast_radius_count", "anomaly_count", "signal_count"):
        assert field in item, f"Missing field: {field}"


def test_list_runs_ordered_newest_first(client):
    resp = client.tc.get("/api/runs", params={"repo": "/repo"})
    returned_ids = [item["id"] for item in resp.json()]
    # PR #1 was inserted after PR #0, so it should appear first
    assert returned_ids[0] == client.ids[1]
    assert returned_ids[1] == client.ids[0]


def test_list_runs_filters_by_repo(client):
    resp = client.tc.get("/api/runs", params={"repo": "/other-repo"})
    assert resp.json() == []


def test_list_runs_pagination(client):
    page1 = client.tc.get("/api/runs", params={"repo": "/repo", "limit": 1, "offset": 0}).json()
    page2 = client.tc.get("/api/runs", params={"repo": "/repo", "limit": 1, "offset": 1}).json()
    assert len(page1) == 1
    assert len(page2) == 1
    assert page1[0]["id"] != page2[0]["id"]


# --- GET /api/runs/{run_id} ---


def test_get_run_returns_200(client):
    resp = client.tc.get(f"/api/runs/{client.ids[0]}")
    assert resp.status_code == 200


def test_get_run_correct_id(client):
    resp = client.tc.get(f"/api/runs/{client.ids[0]}")
    assert resp.json()["id"] == client.ids[0]


def test_get_run_404_for_unknown(client):
    resp = client.tc.get("/api/runs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert "error" in resp.json()["detail"]


# --- GET /api/runs/{run_id}/report ---


def test_get_report_returns_200(client):
    resp = client.tc.get(f"/api/runs/{client.ids[0]}/report")
    assert resp.status_code == 200


def test_get_report_contains_expected_fields(client):
    data = client.tc.get(f"/api/runs/{client.ids[0]}/report").json()
    for field in ("pr_title", "base_sha", "head_sha", "changed_files",
                  "blast_radius", "ai_analysis"):
        assert field in data, f"Missing field: {field}"


def test_get_report_pr_title(client):
    data = client.tc.get(f"/api/runs/{client.ids[0]}/report").json()
    # ids[0] corresponds to PR #0
    assert data["pr_title"] == "PR #0"


def test_get_report_404_for_unknown(client):
    resp = client.tc.get("/api/runs/00000000-0000-0000-0000-000000000000/report")
    assert resp.status_code == 404
    assert "error" in resp.json()["detail"]
