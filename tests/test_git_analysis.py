"""Unit and integration tests for pr_impact/git_analysis.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import git
import pytest

from pr_impact.git_analysis import (
    ensure_commits_present,
    get_changed_files,
    get_git_churn,
    get_pr_metadata,
)
from pr_impact.models import resolve_language as _resolve_language

REPO_ROOT = str(Path(__file__).resolve().parents[1])
BASE_SHA = "dd92358"
HEAD_SHA = "53ca05c"


# ---------------------------------------------------------------------------
# _resolve_language — pure
# ---------------------------------------------------------------------------


def test_resolve_py():
    assert _resolve_language("module.py") == "python"


def test_resolve_ts():
    assert _resolve_language("index.ts") == "typescript"


def test_resolve_tsx():
    assert _resolve_language("App.tsx") == "typescript"


def test_resolve_js():
    assert _resolve_language("util.js") == "javascript"


def test_resolve_jsx():
    assert _resolve_language("App.jsx") == "javascript"


def test_resolve_mjs():
    assert _resolve_language("worker.mjs") == "javascript"


def test_resolve_cjs():
    assert _resolve_language("config.cjs") == "javascript"


def test_resolve_unknown_extension():
    assert _resolve_language("README.md") == "unknown"


def test_resolve_no_extension():
    assert _resolve_language("Makefile") == "unknown"


def test_resolve_path_with_directory_prefix():
    assert _resolve_language("src/utils/helpers.py") == "python"


def test_resolve_dotfile_with_py_extension():
    assert _resolve_language(".github/setup.py") == "python"


# ---------------------------------------------------------------------------
# Helpers for stub-based tests
# ---------------------------------------------------------------------------


def _make_stub_repo(*diff_items):
    """Return a MagicMock git.Repo whose base_commit.diff() yields diff_items."""
    stub = MagicMock()
    base = MagicMock()
    stub.commit.return_value = base
    base.diff.return_value = list(diff_items)
    return stub


def _make_diff_item(
    b_path="module.py", a_path="module.py", diff=b"+line\n", a_blob=None, b_blob=None
):
    item = MagicMock()
    item.b_path = b_path
    item.a_path = a_path
    item.diff = diff
    item.a_blob = a_blob
    item.b_blob = b_blob
    return item


def _make_blob(content: bytes):
    blob = MagicMock(spec=git.Blob)
    blob.data_stream.read.return_value = content
    return blob


# ---------------------------------------------------------------------------
# get_changed_files — stub-based unit tests
# ---------------------------------------------------------------------------


def test_get_changed_files_skips_unknown_language():
    item = _make_diff_item(b_path="README.md", a_path="README.md")
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result == []


def test_get_changed_files_returns_python_file():
    item = _make_diff_item(b_path="module.py")
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert len(result) == 1
    assert result[0].language == "python"
    assert result[0].path == "module.py"


def test_get_changed_files_returns_typescript_file():
    item = _make_diff_item(b_path="index.ts")
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].language == "typescript"


def test_get_changed_files_uses_b_path_over_a_path():
    item = _make_diff_item(b_path="new.py", a_path="old.py")
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].path == "new.py"


def test_get_changed_files_falls_back_to_a_path_when_b_path_empty():
    item = _make_diff_item(b_path="", a_path="deleted.py")
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].path == "deleted.py"


def test_get_changed_files_skips_item_with_both_paths_empty():
    item = _make_diff_item(b_path="", a_path="")
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result == []


def test_get_changed_files_decodes_bytes_diff():
    item = _make_diff_item(diff=b"+def foo():\n")
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].diff == "+def foo():\n"


def test_get_changed_files_str_diff_passes_through():
    item = _make_diff_item(diff="+line\n")
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].diff == "+line\n"


def test_get_changed_files_none_diff_becomes_empty_string():
    item = _make_diff_item(diff=None)
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].diff == ""


def test_get_changed_files_none_a_blob_gives_empty_content_before():
    item = _make_diff_item(a_blob=None)
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].content_before == ""


def test_get_changed_files_none_b_blob_gives_empty_content_after():
    item = _make_diff_item(b_blob=None)
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].content_after == ""


def test_get_changed_files_blob_content_decoded():
    blob = _make_blob(b"def foo(): pass\n")
    item = _make_diff_item(b_blob=blob)
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert result[0].content_after == "def foo(): pass\n"


def test_get_changed_files_bad_blob_read_gives_empty_content():
    # _blob_content has its own try/except — a bad read degrades to "" rather than skipping the file
    blob = MagicMock(spec=git.Blob)
    blob.data_stream.read.side_effect = OSError("read failed")
    item = _make_diff_item(a_blob=blob)
    stub = _make_stub_repo(item)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert len(result) == 1
    assert result[0].content_before == ""


def test_get_changed_files_uses_injected_repo_not_path():
    item = _make_diff_item()
    stub = _make_stub_repo(item)
    # repo_path is deliberately invalid — injected stub must be used instead
    result = get_changed_files("/does/not/exist", "abc", "def", repo=stub)
    assert len(result) == 1


def test_get_changed_files_mixed_languages_filtered():
    items = [
        _make_diff_item(b_path="a.py"),
        _make_diff_item(b_path="b.ts"),
        _make_diff_item(b_path="c.md"),
    ]
    stub = _make_stub_repo(*items)
    result = get_changed_files(".", "abc", "def", repo=stub)
    assert len(result) == 2
    assert {r.language for r in result} == {"python", "typescript"}


# ---------------------------------------------------------------------------
# ensure_commits_present — mock-based unit tests
# ---------------------------------------------------------------------------


def _make_repo_for_fetch(
    head_present: bool = True,
    base_present: bool = True,
    fetch_resolves: bool = True,
):
    """Return a stub git.Repo with controllable commit lookup and remote fetch.

    When fetch_resolves=True (default), calling remote.fetch() makes the
    previously-missing SHA resolvable, simulating a successful fetch.
    When fetch_resolves=False, the SHA remains absent after fetch.
    """
    resolved: set[str] = set()
    if head_present:
        resolved.add("head123")
    if base_present:
        resolved.add("base456")

    def _commit(sha):
        if sha not in resolved:
            raise git.BadName(sha)

    def _fetch(ref):
        if fetch_resolves:
            if "pull/" in ref:
                resolved.add("head123")
            else:
                resolved.add("base456")

    repo = MagicMock()
    repo.commit.side_effect = _commit
    remote = MagicMock()
    remote.fetch.side_effect = _fetch
    repo.remote.return_value = remote
    return repo, remote


def test_ensure_commits_present_both_present_no_fetch():
    repo, remote = _make_repo_for_fetch(head_present=True, base_present=True)
    ensure_commits_present(".", "base456", "head123", "origin", pr_number=7, repo=repo)
    remote.fetch.assert_not_called()


def test_ensure_commits_present_missing_head_fetches_pr_ref():
    repo, remote = _make_repo_for_fetch(head_present=False, base_present=True)
    ensure_commits_present(".", "base456", "head123", "origin", pr_number=42, repo=repo)
    remote.fetch.assert_called_once_with("refs/pull/42/head")


def test_ensure_commits_present_missing_base_fetches_base_ref():
    repo, remote = _make_repo_for_fetch(head_present=True, base_present=False)
    ensure_commits_present(".", "base456", "head123", "origin", pr_number=7, base_ref="main", repo=repo)
    remote.fetch.assert_called_once_with("main")


def test_ensure_commits_present_missing_base_no_base_ref_skips_fetch():
    repo, remote = _make_repo_for_fetch(head_present=True, base_present=False)
    ensure_commits_present(".", "base456", "head123", "origin", pr_number=7, base_ref=None, repo=repo)
    remote.fetch.assert_not_called()


def test_ensure_commits_present_fetch_failure_raises():
    repo, remote = _make_repo_for_fetch(head_present=False, base_present=True)
    remote.fetch.side_effect = git.GitCommandError("fetch", 128)
    with pytest.raises(RuntimeError, match="Could not fetch missing PR commits"):
        ensure_commits_present(".", "base456", "head123", "origin", pr_number=7, repo=repo)


def test_ensure_commits_present_sha_still_absent_after_fetch_raises():
    repo, _remote = _make_repo_for_fetch(head_present=False, base_present=True, fetch_resolves=False)
    with pytest.raises(RuntimeError, match="still not present after fetch"):
        ensure_commits_present(".", "base456", "head123", "origin", pr_number=7, repo=repo)


def test_ensure_commits_present_base_still_absent_after_fetch_raises():
    repo, _remote = _make_repo_for_fetch(head_present=True, base_present=False, fetch_resolves=False)
    with pytest.raises(RuntimeError, match="still not present after fetch"):
        ensure_commits_present(".", "base456", "head123", "origin", pr_number=7, base_ref="main", repo=repo)


# ---------------------------------------------------------------------------
# get_git_churn — mock-based unit tests
# ---------------------------------------------------------------------------


def _make_churn_repo(log_output: str):
    stub = MagicMock()
    stub.git.log.return_value = log_output
    return stub


def test_get_git_churn_returns_float():
    stub = _make_churn_repo("abc123 commit one\ndef456 commit two\n")
    result = get_git_churn(".", "module.py", repo=stub)
    assert isinstance(result, float)
    assert result == 2.0


def test_get_git_churn_empty_log_returns_zero():
    stub = _make_churn_repo("")
    assert get_git_churn(".", "module.py", repo=stub) == 0.0


def test_get_git_churn_blank_lines_not_counted():
    stub = _make_churn_repo("\n  \n\n")
    assert get_git_churn(".", "module.py", repo=stub) == 0.0


def test_get_git_churn_exception_returns_zero():
    stub = MagicMock()
    stub.git.log.side_effect = git.GitCommandError("log", 128)
    assert get_git_churn(".", "module.py", repo=stub) == 0.0


def test_get_git_churn_uses_injected_repo():
    stub = _make_churn_repo("abc fix\n")
    result = get_git_churn("/does/not/exist", "module.py", repo=stub)
    assert result == 1.0


# ---------------------------------------------------------------------------
# get_pr_metadata — mock-based unit tests
# ---------------------------------------------------------------------------


def _make_commit(message: str, author: str):
    c = MagicMock()
    c.message = message
    c.author.name = author
    return c


def test_get_pr_metadata_returns_expected_keys():
    with patch("pr_impact.git_analysis.git.Repo") as mock_repo_cls:
        mock_repo_cls.return_value.iter_commits.return_value = [_make_commit("fix: bug\n", "Alice")]
        result = get_pr_metadata(".", "abc", "def")
    assert "commits" in result
    assert "authors" in result


def test_get_pr_metadata_strips_commit_message():
    with patch("pr_impact.git_analysis.git.Repo") as mock_repo_cls:
        mock_repo_cls.return_value.iter_commits.return_value = [
            _make_commit("  fix: bug\n  ", "Alice")
        ]
        result = get_pr_metadata(".", "abc", "def")
    assert result["commits"] == ["fix: bug"]


def test_get_pr_metadata_deduplicates_authors():
    with patch("pr_impact.git_analysis.git.Repo") as mock_repo_cls:
        mock_repo_cls.return_value.iter_commits.return_value = [
            _make_commit("msg1", "Alice"),
            _make_commit("msg2", "Alice"),
        ]
        result = get_pr_metadata(".", "abc", "def")
    assert len(result["authors"]) == 1
    assert result["authors"] == ["Alice"]


def test_get_pr_metadata_multiple_authors():
    with patch("pr_impact.git_analysis.git.Repo") as mock_repo_cls:
        mock_repo_cls.return_value.iter_commits.return_value = [
            _make_commit("msg1", "Alice"),
            _make_commit("msg2", "Bob"),
        ]
        result = get_pr_metadata(".", "abc", "def")
    assert set(result["authors"]) == {"Alice", "Bob"}


def test_get_pr_metadata_exception_returns_empty_dict():
    with patch("pr_impact.git_analysis.git.Repo") as mock_repo_cls:
        mock_repo_cls.side_effect = git.InvalidGitRepositoryError("bad")
        result = get_pr_metadata("/bad/path", "abc", "def")
    assert result == {}


# ---------------------------------------------------------------------------
# Integration tests — real repo, known SHAs
# ---------------------------------------------------------------------------


def test_integration_get_changed_files_count():
    files = get_changed_files(REPO_ROOT, BASE_SHA, HEAD_SHA)
    # All changed files are .py — 8 total (confirmed by prior smoke test)
    assert len(files) == 8


def test_integration_get_changed_files_includes_expected_paths():
    files = get_changed_files(REPO_ROOT, BASE_SHA, HEAD_SHA)
    paths = {f.path for f in files}
    assert "pr_impact/classifier.py" in paths
    assert "tests/test_classifier.py" in paths


def test_integration_get_changed_files_no_non_source_files():
    files = get_changed_files(REPO_ROOT, BASE_SHA, HEAD_SHA)
    assert all(f.path.endswith(".py") for f in files)


def test_integration_get_changed_files_all_python_language():
    files = get_changed_files(REPO_ROOT, BASE_SHA, HEAD_SHA)
    assert all(f.language == "python" for f in files)


def test_integration_get_changed_files_diffs_are_strings():
    files = get_changed_files(REPO_ROOT, BASE_SHA, HEAD_SHA)
    assert all(isinstance(f.diff, str) for f in files)


def test_integration_get_git_churn_returns_nonneg_float():
    churn = get_git_churn(REPO_ROOT, "pr_impact/classifier.py")
    assert isinstance(churn, float)
    assert churn >= 0.0


def test_integration_get_git_churn_known_file_has_commits():
    churn = get_git_churn(REPO_ROOT, "pr_impact/classifier.py")
    assert churn >= 1.0


def test_integration_get_git_churn_nonexistent_file_returns_zero():
    churn = get_git_churn(REPO_ROOT, "does/not/exist.py")
    assert churn == 0.0


def test_integration_get_pr_metadata_structure():
    meta = get_pr_metadata(REPO_ROOT, BASE_SHA, HEAD_SHA)
    assert "commits" in meta
    assert "authors" in meta
    assert isinstance(meta["commits"], list)
    assert isinstance(meta["authors"], list)


def test_integration_get_pr_metadata_commit_message():
    meta = get_pr_metadata(REPO_ROOT, BASE_SHA, HEAD_SHA)
    assert len(meta["commits"]) == 1
    assert "Add unit test suite" in meta["commits"][0]


def test_integration_get_pr_metadata_has_author():
    meta = get_pr_metadata(REPO_ROOT, BASE_SHA, HEAD_SHA)
    assert len(meta["authors"]) >= 1
