"""FastAPI routes for the Primpact-as-a-Service webhook receiver (Milestone 6).

POST /webhook/github  — receives GitHub pull_request events
POST /webhook/gitlab  — receives GitLab merge_request events

Both endpoints:
1. Validate the incoming signature / token.
2. Parse the payload into a WebhookJob.
3. Enqueue the job onto app.state.webhook_queue for the background worker.

The background worker (started in the server-mode lifespan) drains the queue,
clones/fetches repos, runs the analysis subprocess, and posts comments.

Required environment variables:
  WEBHOOK_SECRET        — GitHub HMAC-SHA256 secret (required for GitHub webhooks)
  GITLAB_WEBHOOK_TOKEN  — GitLab webhook token (required for GitLab webhooks)
  GITHUB_TOKEN          — GitHub PAT for posting PR comments
  GITLAB_TOKEN          — GitLab PAT for posting MR notes
  GITLAB_URL            — GitLab base URL (default: https://gitlab.com)
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ...webhook import (
    WebhookJob,
    parse_github_event,
    parse_gitlab_event,
    validate_github_signature,
    validate_gitlab_token,
)

router = APIRouter()


def _enqueue(request: Request, job: WebhookJob) -> None:
    """Put job on the webhook queue stored in app state."""
    queue = getattr(request.app.state, "webhook_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail={"error": "Webhook queue not initialised"})
    queue.put_nowait(job)


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> JSONResponse:
    """Receive a GitHub pull_request webhook event."""
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail={"error": "WEBHOOK_SECRET not configured"})

    payload_bytes = await request.body()

    if not validate_github_signature(payload_bytes, x_hub_signature_256, secret):
        raise HTTPException(status_code=401, detail={"error": "Invalid signature"})

    try:
        payload = json.loads(payload_bytes)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail={"error": "Invalid JSON payload"})

    job = parse_github_event(x_github_event, payload)
    if job is None:
        return JSONResponse({"status": "ignored"}, status_code=200)

    # Fill in server-supplied fields
    github_token = os.environ.get("GITHUB_TOKEN")
    repos_dir: str = getattr(request.app.state, "repos_dir", "./repos")
    db_path: str = getattr(request.app.state, "db_path", ".primpact/history.db")

    if github_token:
        if not job["clone_url"].startswith("https://"):
            raise HTTPException(
                status_code=400,
                detail={"error": "Expected HTTPS clone URL; cannot embed token"},
            )
        clone_url = job["clone_url"].replace(
            "https://", f"https://x-access-token:{github_token}@", 1
        )
    else:
        clone_url = job["clone_url"]

    job = WebhookJob(
        **{**job, "repos_dir": repos_dir, "db_path": db_path,
           "github_token": github_token, "clone_url": clone_url},
    )

    _enqueue(request, job)
    return JSONResponse(
        {"status": "queued", "owner": job["owner"], "repo": job["repo_name"],
         "pr": job["pr_number"]},
        status_code=202,
    )


@router.post("/webhook/gitlab")
async def gitlab_webhook(
    request: Request,
    x_gitlab_token: str = Header(default=""),
) -> JSONResponse:
    """Receive a GitLab merge_request webhook event."""
    secret = os.environ.get("GITLAB_WEBHOOK_TOKEN", "")
    if not secret:
        raise HTTPException(status_code=503, detail={"error": "GITLAB_WEBHOOK_TOKEN not configured"})

    if not validate_gitlab_token(x_gitlab_token, secret):
        raise HTTPException(status_code=401, detail={"error": "Invalid token"})

    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail={"error": "Invalid JSON payload"})

    job = parse_gitlab_event(payload)
    if job is None:
        return JSONResponse({"status": "ignored"}, status_code=200)

    gitlab_token = os.environ.get("GITLAB_TOKEN")
    gitlab_url = os.environ.get("GITLAB_URL", "https://gitlab.com")
    repos_dir: str = getattr(request.app.state, "repos_dir", "./repos")
    db_path: str = getattr(request.app.state, "db_path", ".primpact/history.db")

    # Embed PAT into clone URL so ensure_repo can authenticate against private projects.
    # GitLab HTTPS clone with a PAT uses oauth2 as the username.
    clone_url = job["clone_url"]
    if gitlab_token and clone_url.startswith("https://"):
        clone_url = clone_url.replace("https://", f"https://oauth2:{gitlab_token}@", 1)

    job = WebhookJob(
        **{**job, "repos_dir": repos_dir, "db_path": db_path,
           "gitlab_token": gitlab_token, "gitlab_url": gitlab_url,
           "clone_url": clone_url},
    )

    _enqueue(request, job)
    return JSONResponse(
        {"status": "queued", "owner": job["owner"], "repo": job["repo_name"],
         "pr": job["pr_number"]},
        status_code=202,
    )
