import sys
from datetime import datetime, timedelta

import git

from .models import ChangedFile

LANGUAGE_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}


def _resolve_language(path: str) -> str:
    for ext, lang in LANGUAGE_MAP.items():
        if path.endswith(ext):
            return lang
    return "unknown"


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
        language = _resolve_language(path)
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
