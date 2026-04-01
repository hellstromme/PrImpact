import sys
from datetime import datetime, timedelta

import git

from .models import ChangedFile, resolve_language


def _blob_content(blob: git.Blob) -> str:
    try:
        data: bytes = blob.data_stream.read()
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def get_changed_files(
    repo_path: str, base_sha: str, head_sha: str, repo: git.Repo | None = None
) -> list[ChangedFile]:
    repo = repo or git.Repo(repo_path)
    base_commit = repo.commit(base_sha)
    head_commit = repo.commit(head_sha)

    diffs = base_commit.diff(head_commit, create_patch=True)
    results: list[ChangedFile] = []

    for diff_item in diffs:
        path: str = diff_item.b_path or diff_item.a_path or ""
        if not path:
            continue
        language = resolve_language(path)
        if language == "unknown":
            continue

        try:
            raw_diff = diff_item.diff
            if isinstance(raw_diff, bytes):
                raw_diff = raw_diff.decode("utf-8", errors="replace")
            if raw_diff is None:
                raw_diff = ""

            a_blob = diff_item.a_blob
            b_blob = diff_item.b_blob
            content_before = _blob_content(a_blob) if isinstance(a_blob, git.Blob) else ""
            content_after = _blob_content(b_blob) if isinstance(b_blob, git.Blob) else ""

            results.append(
                ChangedFile(
                    path=path,
                    language=language,
                    diff=raw_diff,
                    content_before=content_before,
                    content_after=content_after,
                )
            )
        except Exception as e:
            print(f"Warning: skipping {path}: {e}", file=sys.stderr)

    return results


def get_git_churn(repo_path: str, path: str, days: int = 90, repo: git.Repo | None = None) -> float:
    try:
        r = repo or git.Repo(repo_path)
        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        log_output = r.git.log(
            "--oneline",
            "--follow",
            f"--after={since_date}",
            "--",
            path,
        )
        lines = [ln for ln in log_output.splitlines() if ln.strip()]
        return float(len(lines))
    except Exception:
        return 0.0


def ensure_commits_present(
    repo_path: str,
    base_sha: str,
    head_sha: str,
    remote_name: str,
    pr_number: int | None = None,
    base_ref: str | None = None,
    repo: git.Repo | None = None,
) -> None:
    """Fetch PR commits from the remote if they are absent locally.

    Stale clones and fork PRs often lack the exact SHAs returned by the
    GitHub API. Fetches refs/pull/{pr_number}/head for the head and the
    base branch ref for the base when either is missing. Failures are
    silenced — get_changed_files will produce a clear error if commits
    remain absent after the fetch attempt.
    """
    r = repo or git.Repo(repo_path)

    head_missing = False
    base_missing = False
    try:
        r.commit(head_sha)
    except Exception:
        head_missing = True
    try:
        r.commit(base_sha)
    except Exception:
        base_missing = True

    if not head_missing and not base_missing:
        return

    try:
        remote = r.remote(remote_name)
        if head_missing and pr_number is not None:
            remote.fetch(f"refs/pull/{pr_number}/head")
        if base_missing and base_ref:
            remote.fetch(base_ref)
    except Exception as e:
        raise RuntimeError(f"Could not fetch missing PR commits from '{remote_name}': {e}") from e

    if head_missing and pr_number is not None:
        try:
            r.commit(head_sha)
        except Exception:
            raise RuntimeError(
                f"Head SHA {head_sha!r} still not present after fetch from '{remote_name}'."
            )
    if base_missing and base_ref:
        try:
            r.commit(base_sha)
        except Exception:
            raise RuntimeError(
                f"Base SHA {base_sha!r} still not present after fetch from '{remote_name}'."
            )


def get_pr_metadata(repo_path: str, base_sha: str, head_sha: str) -> dict[str, list[str]]:
    try:
        repo = git.Repo(repo_path)
        commits = list(repo.iter_commits(f"{base_sha}..{head_sha}"))
        return {
            "commits": [str(c.message).strip() for c in commits],
            "authors": list({str(c.author.name) for c in commits}),
        }
    except Exception:
        return {}
