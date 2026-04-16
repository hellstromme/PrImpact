"""Tests for the v1.0 REST API: /api/runs and /api/runs/{id}/report endpoints.

These tests require fastapi and httpx (via pytest-httpx or starlette TestClient).
They run against an in-process FastAPI app backed by a tmp-path SQLite DB.
"""

from typing import NamedTuple
from unittest.mock import MagicMock, patch

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


# --- GET /api/runs — parameter validation ---


def test_list_runs_missing_repo_param(client):
    """GET /api/runs without the required 'repo' query param must return 422."""
    resp = client.tc.get("/api/runs")
    assert resp.status_code == 422


# --- POST /api/analyse — parameter validation ---


def test_trigger_analyse_missing_all_refs(client):
    """POST /api/analyse without pr_number and without base/head SHAs returns 422."""
    resp = client.tc.post("/api/analyse", json={"repo": "/repo"})
    assert resp.status_code == 422
    assert "error" in resp.json()["detail"]


def test_trigger_analyse_only_base_sha(client):
    """POST /api/analyse with base_sha but no head_sha returns 422."""
    resp = client.tc.post("/api/analyse", json={"repo": "/repo", "base_sha": "abc1234"})
    assert resp.status_code == 422
    assert "error" in resp.json()["detail"]


def test_trigger_analyse_only_head_sha(client):
    """POST /api/analyse with head_sha but no base_sha returns 422."""
    resp = client.tc.post("/api/analyse", json={"repo": "/repo", "head_sha": "def5678"})
    assert resp.status_code == 422
    assert "error" in resp.json()["detail"]


def test_trigger_analyse_with_pr_number_returns_pending(client):
    """POST /api/analyse with a valid pr_number returns run_id and pending status immediately."""
    resp = client.tc.post("/api/analyse", json={"repo": "/repo", "pr_number": 1})
    # The subprocess will fail (no real repo), but the API must return 200 with pending immediately
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert "run_id" in data


# --- GET /api/analyse/{run_id}/status ---


def test_get_status_unknown_run_id_returns_404(client):
    resp = client.tc.get("/api/analyse/00000000-0000-0000-0000-000000000000/status")
    assert resp.status_code == 404
    assert "error" in resp.json()["detail"]


def test_get_status_returns_error_field(client):
    """Status response always includes an 'error' field (None when not failed)."""
    resp = client.tc.post("/api/analyse", json={"repo": "/repo", "pr_number": 1})
    run_id = resp.json()["run_id"]
    status_resp = client.tc.get(f"/api/analyse/{run_id}/status")
    assert status_resp.status_code == 200
    assert "error" in status_resp.json()


# ---------------------------------------------------------------------------
# merged field — integration tests for _check_merged via the list endpoint
# ---------------------------------------------------------------------------
#
# These tests exercise the full path: API request → _enrich() → _check_merged()
# → GitHub API (mocked) or git ancestry (mocked) → `merged` field in response.
# pr_number is extracted from pr_title in save_run when the title starts with "#".


def _git_returncode(code: int) -> MagicMock:
    m = MagicMock()
    m.returncode = code
    return m


def _make_client(db_path, pr_title):
    rid = save_run(db_path, make_report(pr_title=pr_title), repo_path="/repo")
    app = create_app(db_path=db_path)
    return TestClient(app), rid


def test_merged_true_when_github_api_says_merged(db_path):
    """PR-backed run: GitHub API returns merged → list endpoint returns merged=true."""
    tc, _ = _make_client(db_path, "#42: fix auth")
    with (
        patch("pr_impact.web.api.runs._github_owner_repo", return_value=("acme", "myrepo")),
        patch("pr_impact.web.api.runs.is_pr_merged", return_value=True),
    ):
        data = tc.get("/api/runs", params={"repo": "/repo"}).json()
    assert data[0]["merged"] is True


def test_merged_false_when_github_api_says_open(db_path):
    """PR-backed run: GitHub API returns not merged → list endpoint returns merged=false."""
    tc, _ = _make_client(db_path, "#43: add feature")
    with (
        patch("pr_impact.web.api.runs._github_owner_repo", return_value=("acme", "myrepo")),
        patch("pr_impact.web.api.runs.is_pr_merged", return_value=False),
    ):
        data = tc.get("/api/runs", params={"repo": "/repo"}).json()
    assert data[0]["merged"] is False


def test_merged_uses_github_token_from_env(db_path, monkeypatch):
    """GITHUB_TOKEN env var is forwarded to is_pr_merged."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-xyz")
    tc, _ = _make_client(db_path, "#44: refactor")
    captured = {}

    def fake_is_pr_merged(owner, repo_name, pr_number, token):
        captured["token"] = token
        return False

    with (
        patch("pr_impact.web.api.runs._github_owner_repo", return_value=("acme", "myrepo")),
        patch("pr_impact.web.api.runs.is_pr_merged", side_effect=fake_is_pr_merged),
    ):
        tc.get("/api/runs", params={"repo": "/repo"})
    assert captured["token"] == "test-token-xyz"


def test_merged_falls_back_to_git_when_api_returns_none(db_path):
    """When is_pr_merged returns None (API error), falls back to git ancestry (not merged)."""
    tc, _ = _make_client(db_path, "#45: hotfix")
    with (
        patch("pr_impact.web.api.runs._github_owner_repo", return_value=("acme", "myrepo")),
        patch("pr_impact.web.api.runs.is_pr_merged", return_value=None),
        patch("pr_impact.web.api.runs.subprocess.run", return_value=_git_returncode(1)),
    ):
        data = tc.get("/api/runs", params={"repo": "/repo"}).json()
    assert data[0]["merged"] is False


def test_merged_falls_back_to_git_when_no_github_remote(db_path):
    """When _github_owner_repo returns None (no GitHub remote), git ancestry is used."""
    tc, _ = _make_client(db_path, "#46: cleanup")
    with (
        patch("pr_impact.web.api.runs._github_owner_repo", return_value=None),
        patch("pr_impact.web.api.runs.subprocess.run", return_value=_git_returncode(0)),
    ):
        data = tc.get("/api/runs", params={"repo": "/repo"}).json()
    assert data[0]["merged"] is True


def test_sha_only_run_skips_github_api(db_path):
    """SHA-only run (no pr_number) goes directly to git ancestry; GitHub API is never called."""
    # pr_title doesn't start with "#" so pr_number is stored as None
    tc, _ = _make_client(db_path, "feat: direct sha analysis")
    with (
        patch("pr_impact.web.api.runs.is_pr_merged") as mock_api,
        patch("pr_impact.web.api.runs.subprocess.run", return_value=_git_returncode(0)),
    ):
        data = tc.get("/api/runs", params={"repo": "/repo"}).json()
    mock_api.assert_not_called()
    assert data[0]["merged"] is True


def test_get_run_single_endpoint_also_has_merged_field(db_path):
    """GET /api/runs/{id} (single-run endpoint) also populates merged."""
    tc, rid = _make_client(db_path, "#47: single-run check")
    with (
        patch("pr_impact.web.api.runs._github_owner_repo", return_value=("acme", "myrepo")),
        patch("pr_impact.web.api.runs.is_pr_merged", return_value=True),
    ):
        data = tc.get(f"/api/runs/{rid}").json()
    assert data["merged"] is True
