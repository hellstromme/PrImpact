"""Webhook handling for Primpact-as-a-Service (Milestone 6).

This module is pure logic — no FastAPI imports. It is imported only by
web/api/webhook.py, following the helper-module constraint.

Responsibilities:
- HMAC-SHA256 signature validation for GitHub
- Constant-time token validation for GitLab
- Parsing pull_request / merge_request webhook payloads into WebhookJob dicts
- Cloning / fetching repos into a local directory
- Posting (and upserting) analysis comments on GitHub PRs and GitLab MRs
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import TypedDict

# Marker embedded in every comment body so we can find it for upsert.
PRIMPACT_COMMENT_MARKER = "<!-- primpact-report -->"

_GITHUB_API_VERSION = "2022-11-28"


# ---------------------------------------------------------------------------
# Job description (passed through the asyncio.Queue)
# ---------------------------------------------------------------------------


class WebhookJob(TypedDict):
    platform: str                  # "github" | "gitlab"
    repos_dir: str                 # root directory for local checkouts
    owner: str                     # GitHub owner / GitLab namespace
    repo_name: str
    clone_url: str                 # HTTPS clone URL (token embedded by caller if needed)
    pr_number: int                 # PR number (GitHub) or MR iid (GitLab)
    base_sha: str
    head_sha: str
    db_path: str                   # history DB path passed to analyse subprocess
    github_token: str | None       # for posting GitHub comments
    gitlab_token: str | None       # for posting GitLab notes
    gitlab_url: str                # e.g. "https://gitlab.com"
    project_id: str | None         # GitLab numeric project ID


# ---------------------------------------------------------------------------
# Signature / token validation
# ---------------------------------------------------------------------------


def validate_github_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """Return True if the HMAC-SHA256 signature matches the payload.

    signature_header is the raw value of the X-Hub-Signature-256 header,
    e.g. "sha256=abc123...".
    """
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def validate_gitlab_token(token: str, secret: str) -> bool:
    """Return True if the GitLab X-Gitlab-Token header matches the configured secret."""
    return hmac.compare_digest(token, secret)


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

_GITHUB_ACTIONS = frozenset({"opened", "synchronize", "reopened"})
_GITLAB_ACTIONS = frozenset({"open", "reopen", "update"})


def parse_github_event(event_type: str, payload: dict) -> WebhookJob | None:
    """Parse a GitHub webhook payload.

    Returns a partial WebhookJob (missing repos_dir, db_path, tokens) or None
    if the event is not an actionable PR event. The caller fills in the missing
    fields before enqueuing.
    """
    if event_type != "pull_request":
        return None

    action = payload.get("action", "")
    if action not in _GITHUB_ACTIONS:
        return None

    pr = payload.get("pull_request", {})
    if pr.get("state") == "closed":
        return None

    repo = payload.get("repository", {})
    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")
    clone_url = repo.get("clone_url", "")
    pr_number = payload.get("number", 0)
    base_sha = pr.get("base", {}).get("sha", "")
    head_sha = pr.get("head", {}).get("sha", "")

    if not all([owner, repo_name, clone_url, pr_number, base_sha, head_sha]):
        return None

    return WebhookJob(
        platform="github",
        repos_dir="",          # filled by caller
        owner=owner,
        repo_name=repo_name,
        clone_url=clone_url,
        pr_number=pr_number,
        base_sha=base_sha,
        head_sha=head_sha,
        db_path="",            # filled by caller
        github_token=None,     # filled by caller
        gitlab_token=None,
        gitlab_url="https://gitlab.com",
        project_id=None,
    )


def parse_gitlab_event(payload: dict) -> WebhookJob | None:
    """Parse a GitLab merge_request webhook payload.

    Returns a partial WebhookJob or None if not actionable.
    """
    if payload.get("object_kind") != "merge_request":
        return None

    attrs = payload.get("object_attributes", {})
    action = attrs.get("action", "")
    if action not in _GITLAB_ACTIONS:
        return None

    if attrs.get("state") == "closed" or attrs.get("state") == "merged":
        return None

    project = payload.get("project", {})
    namespace = project.get("namespace", "")
    repo_name = project.get("name", "")
    clone_url = project.get("git_http_url", "")
    raw_id = project.get("id")
    project_id = str(raw_id) if raw_id is not None else ""
    mr_iid = attrs.get("iid", 0)

    diff_refs = attrs.get("diff_refs", {})
    base_sha = diff_refs.get("base_sha", "")
    head_sha = diff_refs.get("head_sha", "") or diff_refs.get("end_sha", "")

    if not all([namespace, repo_name, clone_url, project_id, mr_iid, base_sha, head_sha]):
        return None

    return WebhookJob(
        platform="gitlab",
        repos_dir="",          # filled by caller
        owner=namespace,
        repo_name=repo_name,
        clone_url=clone_url,
        pr_number=mr_iid,
        base_sha=base_sha,
        head_sha=head_sha,
        db_path="",            # filled by caller
        github_token=None,
        gitlab_token=None,     # filled by caller
        gitlab_url="https://gitlab.com",
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Repository management
# ---------------------------------------------------------------------------


def ensure_repo(repos_dir: str, owner: str, repo_name: str, clone_url: str) -> str:
    """Clone the repo into repos_dir/{owner}/{repo_name} if absent, then fetch.

    Returns the local repo path.
    """
    local_path = os.path.join(repos_dir, owner, repo_name)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    if not os.path.isdir(os.path.join(local_path, ".git")):
        subprocess.run(
            ["git", "clone", "--no-single-branch", clone_url, local_path],
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "-C", local_path, "fetch", "--all", "--quiet"],
            check=True,
            capture_output=True,
        )

    return local_path


# ---------------------------------------------------------------------------
# Comment / note posting
# ---------------------------------------------------------------------------


def _github_request(
    method: str,
    url: str,
    token: str,
    body: dict | None = None,
) -> dict | list:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        "Authorization": f"Bearer {token}",
        "User-Agent": "primpact/1.0",
    }
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def post_github_comment(
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
    token: str,
) -> None:
    """Create or update a PrImpact comment on a GitHub PR (upsert by marker)."""
    base = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    try:
        comments = _github_request("GET", f"{base}?per_page=100", token)
        if not isinstance(comments, list):
            comments = []
    except urllib.error.URLError:
        comments = []

    existing_id: int | None = None
    for c in comments:
        if isinstance(c, dict) and PRIMPACT_COMMENT_MARKER in c.get("body", ""):
            existing_id = c["id"]
            break

    marked_body = f"{PRIMPACT_COMMENT_MARKER}\n{body}"
    if existing_id is not None:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{existing_id}"
        _github_request("PATCH", url, token, {"body": marked_body})
    else:
        _github_request("POST", base, token, {"body": marked_body})


def post_gitlab_comment(
    project_id: str | int,
    mr_iid: int,
    body: str,
    token: str,
    gitlab_url: str = "https://gitlab.com",
) -> None:
    """Create or update a PrImpact note on a GitLab MR (upsert by marker)."""
    base = f"{gitlab_url.rstrip('/')}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes"
    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
        "User-Agent": "primpact/1.0",
    }

    def _request(method: str, url: str, data: dict | None = None) -> dict | list:
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    try:
        notes = _request("GET", base)
        if not isinstance(notes, list):
            notes = []
    except urllib.error.URLError:
        notes = []

    existing_id: int | None = None
    for n in notes:
        if isinstance(n, dict) and PRIMPACT_COMMENT_MARKER in n.get("body", ""):
            existing_id = n["id"]
            break

    marked_body = f"{PRIMPACT_COMMENT_MARKER}\n{body}"
    if existing_id is not None:
        url = f"{base}/{existing_id}"
        _request("PUT", url, {"body": marked_body})
    else:
        _request("POST", base, {"body": marked_body})
