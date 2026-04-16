"""Tests for Milestone 6 — Primpact-as-a-Service webhook handling.

Covers:
- HMAC-SHA256 signature validation (webhook.py)
- GitLab token validation (webhook.py)
- GitHub event parsing — actionable and ignored events (webhook.py)
- GitLab event parsing — actionable and ignored events (webhook.py)
- POST /webhook/github endpoint — signature rejection, valid payload queued
- POST /webhook/gitlab endpoint — token rejection, valid payload queued
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import unittest.mock as mock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed; skipping webhook tests")

from fastapi.testclient import TestClient  # noqa: E402

from pr_impact.webhook import (  # noqa: E402
    PRIMPACT_COMMENT_MARKER,
    parse_github_event,
    parse_gitlab_event,
    validate_github_signature,
    validate_gitlab_token,
)
from pr_impact.web.server import create_server_app  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "test-webhook-secret"
_GITLAB_SECRET = "gitlab-token-secret"


def _sign(payload: bytes, secret: str = _SECRET) -> str:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _github_pr_payload(
    action: str = "opened",
    state: str = "open",
    pr_number: int = 42,
    base_sha: str = "abc1234",
    head_sha: str = "def5678",
    owner: str = "myorg",
    repo: str = "myrepo",
) -> dict:
    return {
        "action": action,
        "number": pr_number,
        "pull_request": {
            "state": state,
            "base": {"sha": base_sha},
            "head": {"sha": head_sha},
        },
        "repository": {
            "owner": {"login": owner},
            "name": repo,
            "clone_url": f"https://github.com/{owner}/{repo}.git",
        },
    }


def _gitlab_mr_payload(
    action: str = "open",
    state: str = "opened",
    mr_iid: int = 7,
    base_sha: str = "aaa1111",
    head_sha: str = "bbb2222",
    namespace: str = "mygroup",
    repo: str = "myproject",
    project_id: int = 123,
) -> dict:
    return {
        "object_kind": "merge_request",
        "event_type": "merge_request",
        "project": {
            "id": project_id,
            "name": repo,
            "namespace": namespace,
            "git_http_url": f"https://gitlab.com/{namespace}/{repo}.git",
        },
        "object_attributes": {
            "iid": mr_iid,
            "action": action,
            "state": state,
            "diff_refs": {
                "base_sha": base_sha,
                "head_sha": head_sha,
            },
        },
    }


# ---------------------------------------------------------------------------
# validate_github_signature
# ---------------------------------------------------------------------------


class TestValidateGithubSignature:
    def test_valid_signature_returns_true(self):
        payload = b'{"action":"opened"}'
        sig = _sign(payload)
        assert validate_github_signature(payload, sig, _SECRET) is True

    def test_tampered_payload_returns_false(self):
        payload = b'{"action":"opened"}'
        sig = _sign(payload)
        assert validate_github_signature(b'{"action":"closed"}', sig, _SECRET) is False

    def test_wrong_secret_returns_false(self):
        payload = b'{"action":"opened"}'
        sig = _sign(payload, "other-secret")
        assert validate_github_signature(payload, sig, _SECRET) is False

    def test_missing_sha256_prefix_returns_false(self):
        payload = b"data"
        bare = hmac.new(_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        assert validate_github_signature(payload, bare, _SECRET) is False

    def test_empty_signature_returns_false(self):
        assert validate_github_signature(b"data", "", _SECRET) is False


# ---------------------------------------------------------------------------
# validate_gitlab_token
# ---------------------------------------------------------------------------


class TestValidateGitlabToken:
    def test_matching_token_returns_true(self):
        assert validate_gitlab_token(_GITLAB_SECRET, _GITLAB_SECRET) is True

    def test_wrong_token_returns_false(self):
        assert validate_gitlab_token("wrong", _GITLAB_SECRET) is False

    def test_empty_token_returns_false(self):
        assert validate_gitlab_token("", _GITLAB_SECRET) is False


# ---------------------------------------------------------------------------
# parse_github_event
# ---------------------------------------------------------------------------


class TestParseGithubEvent:
    def test_opened_event_returns_job(self):
        payload = _github_pr_payload("opened")
        job = parse_github_event("pull_request", payload)
        assert job is not None
        assert job["platform"] == "github"
        assert job["pr_number"] == 42
        assert job["base_sha"] == "abc1234"
        assert job["head_sha"] == "def5678"
        assert job["owner"] == "myorg"
        assert job["repo_name"] == "myrepo"

    def test_synchronize_event_returns_job(self):
        job = parse_github_event("pull_request", _github_pr_payload("synchronize"))
        assert job is not None

    def test_reopened_event_returns_job(self):
        job = parse_github_event("pull_request", _github_pr_payload("reopened"))
        assert job is not None

    def test_closed_action_returns_none(self):
        assert parse_github_event("pull_request", _github_pr_payload("closed")) is None

    def test_labeled_action_returns_none(self):
        assert parse_github_event("pull_request", _github_pr_payload("labeled")) is None

    def test_push_event_type_returns_none(self):
        assert parse_github_event("push", _github_pr_payload("opened")) is None

    def test_closed_state_returns_none(self):
        payload = _github_pr_payload("opened", state="closed")
        assert parse_github_event("pull_request", payload) is None

    def test_missing_base_sha_returns_none(self):
        payload = _github_pr_payload("opened")
        payload["pull_request"]["base"]["sha"] = ""
        assert parse_github_event("pull_request", payload) is None

    def test_clone_url_preserved(self):
        job = parse_github_event("pull_request", _github_pr_payload("opened"))
        assert job is not None
        assert "github.com" in job["clone_url"]


# ---------------------------------------------------------------------------
# parse_gitlab_event
# ---------------------------------------------------------------------------


class TestParseGitlabEvent:
    def test_open_action_returns_job(self):
        payload = _gitlab_mr_payload("open")
        job = parse_gitlab_event(payload)
        assert job is not None
        assert job["platform"] == "gitlab"
        assert job["pr_number"] == 7
        assert job["base_sha"] == "aaa1111"
        assert job["head_sha"] == "bbb2222"
        assert job["owner"] == "mygroup"
        assert job["repo_name"] == "myproject"
        assert job["project_id"] == "123"

    def test_update_action_returns_job(self):
        assert parse_gitlab_event(_gitlab_mr_payload("update")) is not None

    def test_reopen_action_returns_job(self):
        assert parse_gitlab_event(_gitlab_mr_payload("reopen")) is not None

    def test_close_action_returns_none(self):
        assert parse_gitlab_event(_gitlab_mr_payload("close")) is None

    def test_merge_action_returns_none(self):
        assert parse_gitlab_event(_gitlab_mr_payload("merge")) is None

    def test_closed_state_returns_none(self):
        payload = _gitlab_mr_payload("open", state="closed")
        assert parse_gitlab_event(payload) is None

    def test_merged_state_returns_none(self):
        payload = _gitlab_mr_payload("open", state="merged")
        assert parse_gitlab_event(payload) is None

    def test_wrong_object_kind_returns_none(self):
        payload = _gitlab_mr_payload("open")
        payload["object_kind"] = "push"
        assert parse_gitlab_event(payload) is None

    def test_missing_head_sha_returns_none(self):
        payload = _gitlab_mr_payload("open")
        payload["object_attributes"]["diff_refs"]["head_sha"] = ""
        assert parse_gitlab_event(payload) is None


# ---------------------------------------------------------------------------
# POST /webhook/github endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_with_queue(tmp_path):
    """Server app with webhook routes and a pre-seeded queue (no live worker)."""
    app = create_server_app(
        db_path=str(tmp_path / "h.db"),
        repos_dir=str(tmp_path / "repos"),
    )
    # Pre-set the queue so the endpoints can enqueue without the lifespan starting
    app.state.webhook_queue = asyncio.Queue()
    return app


class TestGithubWebhookEndpoint:
    def test_missing_secret_env_returns_503(self, app_with_queue, monkeypatch):
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        client = TestClient(app_with_queue)
        payload = json.dumps(_github_pr_payload()).encode()
        resp = client.post(
            "/webhook/github",
            content=payload,
            headers={
                "X-Hub-Signature-256": _sign(payload),
                "X-GitHub-Event": "pull_request",
            },
        )
        assert resp.status_code == 503

    def test_invalid_signature_returns_401(self, app_with_queue, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", _SECRET)
        client = TestClient(app_with_queue)
        payload = json.dumps(_github_pr_payload()).encode()
        resp = client.post(
            "/webhook/github",
            content=payload,
            headers={
                "X-Hub-Signature-256": "sha256=badhash",
                "X-GitHub-Event": "pull_request",
            },
        )
        assert resp.status_code == 401

    def test_valid_pr_event_returns_202_and_queued(self, app_with_queue, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", _SECRET)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        client = TestClient(app_with_queue)
        payload = json.dumps(_github_pr_payload("opened")).encode()
        resp = client.post(
            "/webhook/github",
            content=payload,
            headers={
                "X-Hub-Signature-256": _sign(payload),
                "X-GitHub-Event": "pull_request",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["pr"] == 42
        assert not app_with_queue.state.webhook_queue.empty()

    def test_ignored_event_returns_200_ignored(self, app_with_queue, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", _SECRET)
        client = TestClient(app_with_queue)
        payload = json.dumps({"action": "labeled"}).encode()
        resp = client.post(
            "/webhook/github",
            content=payload,
            headers={
                "X-Hub-Signature-256": _sign(payload),
                "X-GitHub-Event": "push",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_github_token_embedded_in_clone_url(self, app_with_queue, monkeypatch):
        monkeypatch.setenv("WEBHOOK_SECRET", _SECRET)
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_testtoken")
        client = TestClient(app_with_queue)
        payload = json.dumps(_github_pr_payload("opened")).encode()
        client.post(
            "/webhook/github",
            content=payload,
            headers={
                "X-Hub-Signature-256": _sign(payload),
                "X-GitHub-Event": "pull_request",
            },
        )
        job = app_with_queue.state.webhook_queue.get_nowait()
        assert "ghp_testtoken" in job["clone_url"]


# ---------------------------------------------------------------------------
# POST /webhook/gitlab endpoint
# ---------------------------------------------------------------------------


class TestGitlabWebhookEndpoint:
    def test_missing_secret_env_returns_503(self, app_with_queue, monkeypatch):
        monkeypatch.delenv("GITLAB_WEBHOOK_TOKEN", raising=False)
        client = TestClient(app_with_queue)
        payload = json.dumps(_gitlab_mr_payload()).encode()
        resp = client.post(
            "/webhook/gitlab",
            content=payload,
            headers={"X-Gitlab-Token": _GITLAB_SECRET},
        )
        assert resp.status_code == 503

    def test_invalid_token_returns_401(self, app_with_queue, monkeypatch):
        monkeypatch.setenv("GITLAB_WEBHOOK_TOKEN", _GITLAB_SECRET)
        client = TestClient(app_with_queue)
        payload = json.dumps(_gitlab_mr_payload()).encode()
        resp = client.post(
            "/webhook/gitlab",
            content=payload,
            headers={"X-Gitlab-Token": "wrong-token"},
        )
        assert resp.status_code == 401

    def test_valid_mr_event_returns_202_and_queued(self, app_with_queue, monkeypatch):
        monkeypatch.setenv("GITLAB_WEBHOOK_TOKEN", _GITLAB_SECRET)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        client = TestClient(app_with_queue)
        payload = json.dumps(_gitlab_mr_payload("open")).encode()
        resp = client.post(
            "/webhook/gitlab",
            content=payload,
            headers={"X-Gitlab-Token": _GITLAB_SECRET},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["pr"] == 7
        assert not app_with_queue.state.webhook_queue.empty()

    def test_ignored_mr_action_returns_200_ignored(self, app_with_queue, monkeypatch):
        monkeypatch.setenv("GITLAB_WEBHOOK_TOKEN", _GITLAB_SECRET)
        client = TestClient(app_with_queue)
        payload = json.dumps(_gitlab_mr_payload("close")).encode()
        resp = client.post(
            "/webhook/gitlab",
            content=payload,
            headers={"X-Gitlab-Token": _GITLAB_SECRET},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_gitlab_url_defaults_to_gitlab_com(self, app_with_queue, monkeypatch):
        monkeypatch.setenv("GITLAB_WEBHOOK_TOKEN", _GITLAB_SECRET)
        monkeypatch.delenv("GITLAB_URL", raising=False)
        client = TestClient(app_with_queue)
        payload = json.dumps(_gitlab_mr_payload("open")).encode()
        client.post(
            "/webhook/gitlab",
            content=payload,
            headers={"X-Gitlab-Token": _GITLAB_SECRET},
        )
        job = app_with_queue.state.webhook_queue.get_nowait()
        assert job["gitlab_url"] == "https://gitlab.com"


# ---------------------------------------------------------------------------
# PRIMPACT_COMMENT_MARKER
# ---------------------------------------------------------------------------


def test_comment_marker_is_html_comment():
    assert PRIMPACT_COMMENT_MARKER.startswith("<!--")
    assert PRIMPACT_COMMENT_MARKER.endswith("-->")


# ---------------------------------------------------------------------------
# _process_webhook_job (server.py) — job processing logic
# ---------------------------------------------------------------------------


def _make_job(tmp_path, platform="github", **overrides):
    """Build a minimal WebhookJob dict for testing."""
    from pr_impact.webhook import WebhookJob
    base = WebhookJob(
        platform=platform,
        repos_dir=str(tmp_path / "repos"),
        owner="myorg",
        repo_name="myrepo",
        clone_url="https://github.com/myorg/myrepo.git",
        pr_number=42,
        base_sha="abc123",
        head_sha="def456",
        db_path=str(tmp_path / "h.db"),
        github_token="ghp_test" if platform == "github" else None,
        gitlab_token="glpat_test" if platform == "gitlab" else None,
        gitlab_url="https://gitlab.com",
        project_id="123" if platform == "gitlab" else None,
    )
    return {**base, **overrides}


class TestProcessWebhookJob:
    """Tests for _process_webhook_job — clone → analyse → post comment."""

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_github_happy_path_posts_comment(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch
        from pr_impact.web.server import _process_webhook_job

        job = _make_job(tmp_path, platform="github")
        fake_report = MagicMock()
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def run():
            with (
                patch("pr_impact.webhook.ensure_repo", return_value="/local/repo"),
                patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)),
                patch("pr_impact.history.load_run", return_value=fake_report),
                patch("pr_impact.reporter.render_markdown", return_value="## Report"),
                patch("pr_impact.webhook.post_github_comment") as mock_post,
            ):
                await _process_webhook_job(job, MagicMock())
            call_args = mock_post.call_args[0]
            assert call_args[0] == "myorg"
            assert call_args[1] == "myrepo"
            assert call_args[2] == 42
            assert call_args[3] == "## Report"
            assert call_args[4] == "ghp_test"

        self._run(run())

    def test_gitlab_happy_path_posts_note(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch
        from pr_impact.web.server import _process_webhook_job

        job = _make_job(tmp_path, platform="gitlab")
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def run():
            with (
                patch("pr_impact.webhook.ensure_repo", return_value="/local/repo"),
                patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)),
                patch("pr_impact.history.load_run", return_value=MagicMock()),
                patch("pr_impact.reporter.render_markdown", return_value="## Report"),
                patch("pr_impact.webhook.post_gitlab_comment") as mock_post,
            ):
                await _process_webhook_job(job, MagicMock())
            call_args = mock_post.call_args[0]
            assert call_args[0] == "123"   # project_id
            assert call_args[1] == 42      # mr_iid
            assert call_args[3] == "glpat_test"

        self._run(run())

    def test_subprocess_failure_raises(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch
        from pr_impact.web.server import _process_webhook_job

        job = _make_job(tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = 5
        fake_proc.communicate = AsyncMock(return_value=(b"", b"something went wrong"))

        async def run():
            with (
                patch("pr_impact.webhook.ensure_repo", return_value="/local/repo"),
                patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)),
            ):
                await _process_webhook_job(job, MagicMock())

        with pytest.raises(RuntimeError, match="analyse subprocess failed"):
            self._run(run())

    def test_subprocess_timeout_kills_process_and_raises(self, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from pr_impact.web.server import _process_webhook_job

        job = _make_job(tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock()

        async def run():
            with (
                patch("pr_impact.webhook.ensure_repo", return_value="/local/repo"),
                patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)),
                patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
            ):
                await _process_webhook_job(job, MagicMock())

        with pytest.raises(RuntimeError, match="timed out"):
            self._run(run())
        fake_proc.kill.assert_called_once()

    def test_missing_report_in_db_raises(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch
        from pr_impact.web.server import _process_webhook_job

        job = _make_job(tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def run():
            with (
                patch("pr_impact.webhook.ensure_repo", return_value="/local/repo"),
                patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)),
                patch("pr_impact.history.load_run", return_value=None),
            ):
                await _process_webhook_job(job, MagicMock())

        with pytest.raises(RuntimeError, match="not found in history DB"):
            self._run(run())
