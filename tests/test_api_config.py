"""Tests for GET /api/config endpoint."""

from unittest.mock import patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed; skipping API config tests")

from fastapi.testclient import TestClient  # noqa: E402

from pr_impact.models import PrImpactConfig, SuppressedSignal  # noqa: E402
from pr_impact.web.server import create_app  # noqa: E402


@pytest.fixture()
def client():
    app = create_app(db_path=":memory:")
    return TestClient(app)


def test_get_config_returns_404_when_no_config_file(tmp_path, client):
    """GET /api/config returns 404 when no .primpact.yml is present."""
    with patch("pr_impact.config_file.load_config_file", return_value=None):
        response = client.get(f"/api/config?repo={tmp_path}")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data


def test_get_config_returns_200_with_parsed_config(tmp_path, client):
    """GET /api/config returns 200 with parsed config when file exists."""
    mock_config = PrImpactConfig(
        high_sensitivity_modules=["src/auth/", "src/payments/"],
        suppressed_signals=[
            SuppressedSignal(signal_type="shell_invoke", path_prefix="tools/", reason="build")
        ],
        blast_radius_depth={"src/utils/": 2},
        fail_on_severity="high",
        anomaly_thresholds={"credential": "medium"},
    )

    with patch("pr_impact.config_file.load_config_file", return_value=mock_config):
        response = client.get(f"/api/config?repo={tmp_path}")

    assert response.status_code == 200
    data = response.json()
    assert data["path"] == str(tmp_path / ".primpact.yml")
    assert data["high_sensitivity_modules"] == ["src/auth/", "src/payments/"]
    assert len(data["suppressed_signals"]) == 1
    assert data["suppressed_signals"][0]["signal_type"] == "shell_invoke"
    assert data["suppressed_signals"][0]["path_prefix"] == "tools/"
    assert data["blast_radius_depth"] == {"src/utils/": 2}
    assert data["fail_on_severity"] == "high"
    assert data["anomaly_thresholds"] == {"credential": "medium"}


def test_get_config_missing_repo_param(client):
    """GET /api/config without repo param returns 422 Unprocessable Entity."""
    response = client.get("/api/config")
    assert response.status_code == 422


def test_get_config_returns_500_on_unexpected_error(tmp_path, client):
    """GET /api/config returns 500 when load_config_file raises unexpectedly."""
    with patch("pr_impact.config_file.load_config_file", side_effect=RuntimeError("boom")):
        response = client.get(f"/api/config?repo={tmp_path}")
    assert response.status_code == 500
    data = response.json()
    assert "error" in data
