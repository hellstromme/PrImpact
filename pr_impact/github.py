"""GitHub API helpers — only imported by cli.py."""

import json
import re
import urllib.error
import urllib.request
from collections.abc import Iterable


def _parse_github_remote(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) parsed from a GitHub remote URL, or None."""
    https_match = re.match(
        r"https?://(?:[^@/]+@)?github\.com(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$",
        url,
    )
    if https_match:
        return https_match.group(1), https_match.group(2)
    ssh_match = re.match(
        r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
        url,
    )
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)
    ssh_url_match = re.match(
        r"ssh://git@github\.com(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$",
        url,
    )
    if ssh_url_match:
        return ssh_url_match.group(1), ssh_url_match.group(2)
    return None


def detect_github_remote(
    remotes: Iterable[tuple[str, Iterable[str]]],
) -> tuple[str, str, str] | None:
    """Return (owner, repo_name, remote_name) for the first GitHub remote found, or None.

    ``remotes`` is an iterable of ``(name, urls)`` pairs; callers construct it
    from their VCS objects so this module stays free of GitPython.
    ``origin`` is checked before other remotes.
    """
    ordered = sorted(remotes, key=lambda r: (r[0] != "origin", r[0]))
    for name, urls in ordered:
        for url in urls:
            result = _parse_github_remote(url)
            if result is not None:
                owner, repo_name = result
                return owner, repo_name, name
    return None


def _make_github_request(url: str, token: str | None) -> dict | list:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pr-impact/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code in (401, 403, 404) and not token:
            raise RuntimeError(
                f"GitHub API error {e.code} — the repository may be private. "
                "Set GITHUB_TOKEN (or add github_token to ~/.pr_impact/config.toml) and retry."
            ) from e
        raise RuntimeError(f"GitHub API error {e.code}: {body[:200]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error reaching GitHub API: {e.reason}") from e


def fetch_open_prs(owner: str, repo_name: str, token: str | None = None) -> list[dict]:
    """Return up to 100 open PRs for the given repository."""
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls?state=open&per_page=100"
    result = _make_github_request(url, token)
    if not isinstance(result, list):
        raise RuntimeError(f"Unexpected GitHub API response: {type(result)}")
    return result


def fetch_pr(owner: str, repo_name: str, pr_number: int, token: str | None = None) -> dict:
    """Return a single PR by number."""
    url = f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}"
    result = _make_github_request(url, token)
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected GitHub API response: {type(result)}")
    return result
