"""Unit tests for pr_impact/github.py."""

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from pr_impact.github import (
    _make_github_request,
    _parse_github_remote,
    detect_github_remote,
    fetch_open_prs,
    fetch_pr,
)

# ---------------------------------------------------------------------------
# _parse_github_remote
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("git@github.com:owner/repo", ("owner", "repo")),
        ("https://token@github.com/owner/repo.git", ("owner", "repo")),
        ("https://x-token-auth:abc@github.com/org/project", ("org", "project")),
        ("https://github.com:443/owner/repo.git", ("owner", "repo")),
        ("https://github.com:443/owner/repo", ("owner", "repo")),
        ("ssh://git@github.com:22/owner/repo.git", ("owner", "repo")),
        ("ssh://git@github.com:22/owner/repo", ("owner", "repo")),
        ("https://gitlab.com/owner/repo.git", None),
        ("https://github.com/owner", None),
        ("git@bitbucket.org:owner/repo.git", None),
        ("not-a-url", None),
    ],
)
def test_parse_github_remote(url, expected):
    assert _parse_github_remote(url) == expected


# ---------------------------------------------------------------------------
# detect_github_remote
# ---------------------------------------------------------------------------


def test_detect_github_remote_no_remotes():
    assert detect_github_remote([]) is None


def test_detect_github_remote_origin_first():
    remotes = [
        ("upstream", ["https://github.com/upstream/repo.git"]),
        ("origin", ["https://github.com/myorg/myrepo.git"]),
    ]
    assert detect_github_remote(remotes) == ("myorg", "myrepo", "origin")


def test_detect_github_remote_non_github_returns_none():
    assert detect_github_remote([("origin", ["https://gitlab.com/owner/repo.git"])]) is None


def test_detect_github_remote_ssh_url():
    assert detect_github_remote([("origin", ["git@github.com:org/project.git"])]) == ("org", "project", "origin")


def test_detect_github_remote_ssh_url_scheme():
    assert detect_github_remote([("origin", ["ssh://git@github.com/org/project.git"])]) == ("org", "project", "origin")


def test_detect_github_remote_returns_remote_name():
    assert detect_github_remote([("upstream", ["https://github.com/org/repo.git"])]) == ("org", "repo", "upstream")


# ---------------------------------------------------------------------------
# _make_github_request
# ---------------------------------------------------------------------------


def _mock_urlopen(payload: dict | list, status: int = 200):
    body = json.dumps(payload).encode()
    response = MagicMock()
    response.read.return_value = body
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)
    return response


def test_make_github_request_success():
    payload = {"number": 1, "title": "Fix bug"}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        result = _make_github_request("https://api.github.com/repos/o/r/pulls/1", token=None)
    assert result == payload


def test_make_github_request_with_token_sets_auth_header():
    captured = {}

    def fake_urlopen(req, timeout):
        captured["headers"] = req.headers
        return _mock_urlopen({})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        _make_github_request("https://api.github.com/test", token="mytoken")

    assert captured["headers"].get("Authorization") == "Bearer mytoken"


def test_make_github_request_404_without_token_suggests_private_repo():
    err = urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=404,
        msg="Not Found",
        hdrs=MagicMock(),
        fp=BytesIO(b'{"message":"Not Found"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError, match="private"):
            _make_github_request("https://api.github.com/x", token=None)


def test_make_github_request_404_with_token_shows_raw_error():
    err = urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=404,
        msg="Not Found",
        hdrs=MagicMock(),
        fp=BytesIO(b'{"message":"Not Found"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError, match="404"):
            _make_github_request("https://api.github.com/x", token="tok")


def test_make_github_request_url_error_raises_runtime_error():
    err = urllib.error.URLError(reason="connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError, match="Network error"):
            _make_github_request("https://api.github.com/x", token=None)


# ---------------------------------------------------------------------------
# fetch_open_prs / fetch_pr
# ---------------------------------------------------------------------------


def test_fetch_open_prs_returns_list():
    prs = [{"number": 1, "title": "PR one"}, {"number": 2, "title": "PR two"}]
    with patch("pr_impact.github._make_github_request", return_value=prs) as mock_req:
        result = fetch_open_prs("owner", "repo", token="tok")
    assert result == prs
    url_called = mock_req.call_args[0][0]
    assert "owner/repo/pulls" in url_called
    assert "state=open" in url_called


def test_fetch_open_prs_unexpected_shape_raises():
    with patch("pr_impact.github._make_github_request", return_value={"unexpected": True}):
        with pytest.raises(RuntimeError, match="Unexpected"):
            fetch_open_prs("owner", "repo")


def test_fetch_pr_returns_dict():
    pr = {"number": 42, "title": "My PR", "base": {"sha": "base123"}, "head": {"sha": "head456"}}
    with patch("pr_impact.github._make_github_request", return_value=pr) as mock_req:
        result = fetch_pr("owner", "repo", 42, token=None)
    assert result == pr
    url_called = mock_req.call_args[0][0]
    assert "owner/repo/pulls/42" in url_called


def test_fetch_pr_unexpected_shape_raises():
    with patch("pr_impact.github._make_github_request", return_value=[1, 2, 3]):
        with pytest.raises(RuntimeError, match="Unexpected"):
            fetch_pr("owner", "repo", 42)
