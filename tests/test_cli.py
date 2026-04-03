"""Unit and integration tests for pr_impact/cli.py."""

import json
from unittest.mock import MagicMock, patch

import git
import pytest
from click.testing import CliRunner

from pr_impact.cli import (
    _FALLBACK_BASE,
    _FALLBACK_HEAD,
    _format_pr_title,
    _invert_graph,
    _resolve_refs,
    _run_pipeline,
    _warn_no_github_token,
    _write_outputs,
    main,
)
from pr_impact.models import AIAnalysis, BlastRadiusEntry, RefsResult
from tests.helpers import make_file, make_report

_ENV = {"ANTHROPIC_API_KEY": "test-key"}

# ---------------------------------------------------------------------------
# _invert_graph — pure
# ---------------------------------------------------------------------------


def test_invert_graph_empty():
    assert _invert_graph({}) == {}


def test_invert_graph_single_edge():
    assert _invert_graph({"a": ["b"]}) == {"b": ["a"]}


def test_invert_graph_multiple_targets_from_one_source():
    result = _invert_graph({"a": ["b", "c"]})
    assert result == {"b": ["a"], "c": ["a"]}


def test_invert_graph_multiple_sources_to_same_target():
    result = _invert_graph({"a": ["c"], "b": ["c"]})
    assert set(result["c"]) == {"a", "b"}


def test_invert_graph_node_with_empty_list_contributes_nothing():
    assert _invert_graph({"a": []}) == {}


def test_invert_graph_does_not_mutate_input():
    original = {"a": ["b"]}
    _invert_graph(original)
    assert original == {"a": ["b"]}


def test_invert_graph_returns_plain_dict():
    result = _invert_graph({"a": ["b"]})
    assert type(result) is dict


# ---------------------------------------------------------------------------
# Fixtures / helpers for CLI tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


def _base_patches():
    """Return context manager patching all pipeline I/O boundaries."""
    return [
        patch("pr_impact.cli.git.Repo", return_value=MagicMock()),
        patch("pr_impact.cli.get_changed_files", return_value=[make_file("foo.py")]),
        patch("pr_impact.cli.build_import_graph", return_value={}),
        patch("pr_impact.cli.get_blast_radius", return_value=[]),
        patch("pr_impact.cli.get_git_churn", return_value=0.0),
        patch("pr_impact.cli.get_pr_metadata", return_value={}),
        patch(
            "pr_impact.cli.run_ai_analysis",
            return_value=AIAnalysis(summary="test summary"),
        ),
    ]


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


def test_analyse_warns_when_api_key_missing(runner):
    # Without an API key the tool should still run; AI analysis is skipped with a warning
    patches = _base_patches()
    # Replace the run_ai_analysis patch with a real ValueError (what ai_layer raises)
    patches[-1] = patch(
        "pr_impact.cli.run_ai_analysis",
        side_effect=ValueError("ANTHROPIC_API_KEY is not set"),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env={"ANTHROPIC_API_KEY": ""},
        )
    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY" in result.output


def test_analyse_exits_1_when_repo_invalid(runner):
    with patch("pr_impact.cli.git.Repo", side_effect=git.InvalidGitRepositoryError("bad")):
        result = runner.invoke(
            main,
            ["analyse", "--repo", "/bad/path", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert result.exit_code == 1


def test_analyse_exits_1_when_get_changed_files_raises(runner):
    with (
        patch("pr_impact.cli.git.Repo", return_value=MagicMock()),
        patch("pr_impact.cli.get_changed_files", side_effect=RuntimeError("boom")),
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert result.exit_code == 1


def test_analyse_exits_0_when_no_changed_files(runner):
    with (
        patch("pr_impact.cli.git.Repo", return_value=MagicMock()),
        patch("pr_impact.cli.get_changed_files", return_value=[]),
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert result.exit_code == 0
    assert "No supported source files" in result.output


# ---------------------------------------------------------------------------
# Success-path tests
# ---------------------------------------------------------------------------


def test_analyse_success_exit_code_zero(runner):
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert result.exit_code == 0


def test_analyse_success_report_header_in_stdout(runner):
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert "PR IMPACT REPORT" in result.output


def test_analyse_success_ai_summary_in_output(runner):
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert "test summary" in result.output


def test_analyse_success_run_ai_analysis_called(runner):
    patches = _base_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6] as mock_ai,
    ):
        runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert mock_ai.called


def test_analyse_output_flag_writes_file(runner):
    patches = _base_patches()
    with runner.isolated_filesystem():
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = runner.invoke(
                main,
                ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--output", "out.md"],
                env=_ENV,
            )
        assert result.exit_code == 0
        with open("out.md") as fh:
            content = fh.read()
        assert content.startswith("# PR Impact Report")


def test_analyse_json_flag_writes_valid_json(runner):
    patches = _base_patches()
    with runner.isolated_filesystem():
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = runner.invoke(
                main,
                ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--json", "out.json"],
                env=_ENV,
            )
        assert result.exit_code == 0
        with open("out.json") as fh:
            data = json.loads(fh.read())
        assert "pr_title" in data


def test_analyse_max_depth_passed_to_blast_radius(runner):
    patches = _base_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3] as mock_blast,
        patches[4],
        patches[5],
        patches[6],
    ):
        runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--max-depth", "5"],
            env=_ENV,
        )
    call_kwargs = mock_blast.call_args
    assert call_kwargs is not None
    # max_depth is the 3rd positional or a kwarg
    args, kwargs = call_kwargs
    max_depth_value = kwargs.get("max_depth", args[2] if len(args) > 2 else None)
    assert max_depth_value == 5


def test_analyse_churn_called_for_blast_radius_entries(runner):
    blast_entry = BlastRadiusEntry(path="dep.py", distance=1, imported_symbols=[], churn_score=None)
    patches = [
        patch("pr_impact.cli.git.Repo", return_value=MagicMock()),
        patch("pr_impact.cli.get_changed_files", return_value=[make_file("foo.py")]),
        patch("pr_impact.cli.build_import_graph", return_value={}),
        patch("pr_impact.cli.get_blast_radius", return_value=[blast_entry]),
        patch("pr_impact.cli.get_git_churn", return_value=5.0),
        patch("pr_impact.cli.get_pr_metadata", return_value={}),
        patch("pr_impact.cli.run_ai_analysis", return_value=AIAnalysis()),
    ]
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4] as mock_churn,
        patches[5],
        patches[6],
    ):
        runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert mock_churn.called
    # The churn call should reference the blast radius entry's path
    call_args = mock_churn.call_args_list[0]
    assert "dep.py" in call_args.args or "dep.py" in str(call_args)


# ---------------------------------------------------------------------------
# --pr option tests
# ---------------------------------------------------------------------------

_PR_DATA = {
    "number": 42,
    "title": "feat: add widgets",
    "draft": False,
    "base": {"sha": "base111"},
    "head": {"sha": "head222"},
    "user": {"login": "alice"},
}

_OPEN_PRS = [
    {
        "number": 10,
        "title": "fix: typo",
        "draft": False,
        "base": {"sha": "baseAAA"},
        "head": {"sha": "headBBB"},
        "user": {"login": "bob"},
    },
    {
        "number": 20,
        "title": "feat: new thing",
        "draft": True,
        "base": {"sha": "baseCCC"},
        "head": {"sha": "headDDD"},
        "user": {"login": "carol"},
    },
]


def _pr_patches(pr_data=_PR_DATA, open_prs=_OPEN_PRS):
    """Return additional patches needed for GitHub PR tests."""
    return [
        patch("pr_impact.cli.detect_github_remote", return_value=("myorg", "myrepo", "origin")),
        patch("pr_impact.cli.fetch_pr", return_value=pr_data),
        patch("pr_impact.cli.fetch_open_prs", return_value=open_prs),
    ]


def test_analyse_pr_and_base_together_exits_1(runner):
    result = runner.invoke(
        main,
        ["analyse", "--repo", ".", "--pr", "42", "--base", "abc"],
        env=_ENV,
    )
    assert result.exit_code == 1
    assert "--pr cannot be combined" in result.output


def test_analyse_pr_and_head_together_exits_1(runner):
    result = runner.invoke(
        main,
        ["analyse", "--repo", ".", "--pr", "42", "--head", "abc"],
        env=_ENV,
    )
    assert result.exit_code == 1
    assert "--pr cannot be combined" in result.output


def test_analyse_pr_number_uses_github_shas(runner):
    base_p = _base_patches()
    github_p = _pr_patches()
    with (
        base_p[0],
        base_p[1] as mock_changed,
        base_p[2],
        base_p[3],
        base_p[4],
        base_p[5],
        base_p[6],
        github_p[0],
        github_p[1],
        github_p[2],
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--pr", "42"],
            env=_ENV,
        )
    assert result.exit_code == 0
    # SHAs from the PR fixture should be passed to get_changed_files
    call_args = mock_changed.call_args
    assert "base111" in call_args.args or "base111" in str(call_args)
    assert "head222" in call_args.args or "head222" in str(call_args)


def test_analyse_pr_title_uses_github_title(runner):
    base_p = _base_patches()
    github_p = _pr_patches()
    with (
        base_p[0],
        base_p[1],
        base_p[2],
        base_p[3],
        base_p[4],
        base_p[5],
        base_p[6],
        github_p[0],
        github_p[1],
        github_p[2],
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--pr", "42"],
            env=_ENV,
        )
    assert result.exit_code == 0
    assert "feat: add widgets" in result.output


def test_analyse_pr_fetch_error_exits_1(runner):
    base_p = _base_patches()
    with (
        base_p[0],
        base_p[1],
        base_p[2],
        base_p[3],
        base_p[4],
        base_p[5],
        base_p[6],
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_pr", side_effect=RuntimeError("GitHub API error 404")),
        patch("pr_impact.cli.fetch_open_prs", return_value=[]),
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--pr", "99"],
            env=_ENV,
        )
    assert result.exit_code == 1
    assert "GitHub API error 404" in result.output


def test_analyse_no_github_remote_falls_back_to_head(runner):
    """No-arg path with no GitHub remote should fall back to HEAD~1..HEAD, not exit."""
    base_p = _base_patches()
    with (
        base_p[0],
        base_p[1] as mock_changed,
        base_p[2],
        base_p[3],
        base_p[4],
        base_p[5],
        base_p[6],
        patch("pr_impact.cli.detect_github_remote", return_value=None),
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", "."],
            env=_ENV,
        )
    assert result.exit_code == 0
    call_args = mock_changed.call_args
    assert "HEAD~1" in call_args.args or "HEAD~1" in str(call_args)


def test_analyse_no_github_remote_with_pr_flag_exits_1(runner):
    """--pr flag with no GitHub remote should still exit with an error."""
    with (
        patch("pr_impact.cli.git.Repo", return_value=MagicMock()),
        patch("pr_impact.cli.detect_github_remote", return_value=None),
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--pr", "5"],
            env=_ENV,
        )
    assert result.exit_code == 1
    assert "Could not detect a GitHub remote" in result.output


def test_analyse_interactive_selects_pr(runner):
    base_p = _base_patches()
    github_p = _pr_patches()
    with (
        base_p[0],
        base_p[1] as mock_changed,
        base_p[2],
        base_p[3],
        base_p[4],
        base_p[5],
        base_p[6],
        github_p[0],
        github_p[1],
        github_p[2],
        patch("pr_impact.cli._stdin_is_interactive", return_value=True),
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", "."],
            env=_ENV,
            input="10\n",
        )
    assert result.exit_code == 0
    call_args = mock_changed.call_args
    assert "baseAAA" in call_args.args or "baseAAA" in str(call_args)
    assert "headBBB" in call_args.args or "headBBB" in str(call_args)


def test_analyse_interactive_invalid_pr_number_exits_1(runner):
    base_p = _base_patches()
    github_p = _pr_patches()
    with (
        base_p[0],
        base_p[1],
        base_p[2],
        base_p[3],
        base_p[4],
        base_p[5],
        base_p[6],
        github_p[0],
        github_p[1],
        github_p[2],
        patch("pr_impact.cli._stdin_is_interactive", return_value=True),
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", "."],
            env=_ENV,
            input="999\n",
        )
    assert result.exit_code == 1
    assert "not a valid PR number" in result.output


def test_analyse_interactive_no_open_prs_falls_back_to_head(runner):
    """When no open PRs are found in an interactive session, fall back automatically."""
    base_p = _base_patches()
    with (
        base_p[0],
        base_p[1] as mock_changed,
        base_p[2],
        base_p[3],
        base_p[4],
        base_p[5],
        base_p[6],
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_open_prs", return_value=[]),
        patch("pr_impact.cli._stdin_is_interactive", return_value=True),
    ):
        result = runner.invoke(main, ["analyse", "--repo", "."], env=_ENV)
    assert result.exit_code == 0
    assert "No open PRs found" in result.output
    call_args = mock_changed.call_args
    assert "HEAD~1" in call_args.args or "HEAD~1" in str(call_args)


def test_analyse_non_interactive_no_open_prs_uses_last_two_commits(runner):
    """Non-interactive (CI) path: no PR discovery, falls back to HEAD~1..HEAD silently."""
    base_p = _base_patches()
    with (
        base_p[0],
        base_p[1] as mock_changed,
        base_p[2],
        base_p[3],
        base_p[4],
        base_p[5],
        base_p[6],
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_open_prs", return_value=[]),
    ):
        result = runner.invoke(main, ["analyse", "--repo", "."], env=_ENV)
    assert result.exit_code == 0
    call_args = mock_changed.call_args
    assert "HEAD~1" in call_args.args or "HEAD~1" in str(call_args)
    assert "HEAD" in call_args.args or "HEAD" in str(call_args)


# ---------------------------------------------------------------------------
# _format_pr_title — pure helper, no mocking needed
# ---------------------------------------------------------------------------


def test_format_pr_title_with_real_title():
    assert _format_pr_title(42, "feat: add widgets") == "#42: feat: add widgets"


def test_format_pr_title_with_none_title():
    assert _format_pr_title(42, None) == "#42: PR 42"


def test_format_pr_title_with_empty_string():
    # Empty string is falsy — falls back to generic label
    assert _format_pr_title(7, "") == "#7: PR 7"


# ---------------------------------------------------------------------------
# _resolve_refs — direct unit tests, 3-4 patches each
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_repo():
    return MagicMock(spec=git.Repo)


def test_resolve_refs_explicit_pr_returns_refs(mock_repo):
    """Path A: --pr given, remote found, fetch succeeds."""
    with (
        patch("pr_impact.cli._get_github_token", return_value="tok"),
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_pr", return_value=_PR_DATA),
    ):
        result = _resolve_refs(mock_repo, pr_number=42, base=None, head=None)
    assert result.base == "base111"
    assert result.head == "head222"
    assert result.fetch_pr_number == 42
    assert result.pr_title == "#42: feat: add widgets"
    assert result.fetch_remote == "origin"


def test_resolve_refs_explicit_pr_fetch_error_exits_1(mock_repo):
    """Path A-err: --pr given, remote found, fetch_pr raises."""
    with (
        patch("pr_impact.cli._get_github_token", return_value="tok"),
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_pr", side_effect=RuntimeError("404")),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_refs(mock_repo, pr_number=99, base=None, head=None)
    assert exc_info.value.code == 1


def test_resolve_refs_pr_flag_no_remote_exits_1(mock_repo):
    """Path B: --pr given but no GitHub remote detectable."""
    with (
        patch("pr_impact.cli._get_github_token", return_value=None),
        patch("pr_impact.cli.detect_github_remote", return_value=None),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_refs(mock_repo, pr_number=5, base=None, head=None)
    assert exc_info.value.code == 1


def test_resolve_refs_no_pr_no_remote_returns_fallback(mock_repo):
    """Path C: no --pr, no remote → silent HEAD~1..HEAD fallback."""
    with (
        patch("pr_impact.cli._get_github_token", return_value=None),
        patch("pr_impact.cli.detect_github_remote", return_value=None),
    ):
        result = _resolve_refs(mock_repo, pr_number=None, base=None, head=None)
    assert result.base == _FALLBACK_BASE
    assert result.head == _FALLBACK_HEAD
    assert result.pr_title is None


def test_resolve_refs_interactive_valid_selection(mock_repo):
    """Path D: interactive terminal, user picks a valid PR number."""
    with (
        patch("pr_impact.cli._get_github_token", return_value="tok"),
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_open_prs", return_value=_OPEN_PRS),
        patch("pr_impact.cli._stdin_is_interactive", return_value=True),
        patch("click.prompt", return_value="10"),
    ):
        result = _resolve_refs(mock_repo, pr_number=None, base=None, head=None)
    assert result.base == "baseAAA"
    assert result.head == "headBBB"
    assert result.pr_title == "#10: fix: typo"
    assert result.fetch_pr_number == 10


def test_resolve_refs_interactive_invalid_selection_exits_1(mock_repo):
    """Path D: interactive terminal, user enters a number not in the list."""
    with (
        patch("pr_impact.cli._get_github_token", return_value="tok"),
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_open_prs", return_value=_OPEN_PRS),
        patch("pr_impact.cli._stdin_is_interactive", return_value=True),
        patch("click.prompt", return_value="999"),
        pytest.raises(SystemExit) as exc_info,
    ):
        _resolve_refs(mock_repo, pr_number=None, base=None, head=None)
    assert exc_info.value.code == 1


def test_resolve_refs_interactive_no_prs_returns_fallback(mock_repo):
    """Path D-empty: interactive terminal, no open PRs → fallback."""
    with (
        patch("pr_impact.cli._get_github_token", return_value="tok"),
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_open_prs", return_value=[]),
        patch("pr_impact.cli._stdin_is_interactive", return_value=True),
    ):
        result = _resolve_refs(mock_repo, pr_number=None, base=None, head=None)
    assert result.base == _FALLBACK_BASE
    assert result.head == _FALLBACK_HEAD


def test_resolve_refs_interactive_fetch_raises_returns_fallback(mock_repo):
    """Path D-err: fetch_open_prs raises → warn and fall back."""
    with (
        patch("pr_impact.cli._get_github_token", return_value="tok"),
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_open_prs", side_effect=RuntimeError("rate limited")),
        patch("pr_impact.cli._stdin_is_interactive", return_value=True),
    ):
        result = _resolve_refs(mock_repo, pr_number=None, base=None, head=None)
    assert result.base == _FALLBACK_BASE


def test_resolve_refs_non_interactive_returns_fallback(mock_repo):
    """Path E: non-interactive (CI), remote present but no --pr → silent fallback."""
    with (
        patch("pr_impact.cli._get_github_token", return_value="tok"),
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli._stdin_is_interactive", return_value=False),
    ):
        result = _resolve_refs(mock_repo, pr_number=None, base=None, head=None)
    assert result.base == _FALLBACK_BASE
    assert result.head == _FALLBACK_HEAD


# ---------------------------------------------------------------------------
# _run_pipeline — direct unit tests, injected MagicMock progress
# ---------------------------------------------------------------------------


def _pipeline_patches():
    """Patches for all external I/O in _run_pipeline."""
    return [
        patch("pr_impact.cli.get_changed_files", return_value=[make_file("foo.py")]),
        patch("pr_impact.cli.build_import_graph", return_value={}),
        patch("pr_impact.cli.get_blast_radius", return_value=[]),
        patch("pr_impact.cli.get_git_churn", return_value=0.0),
        patch("pr_impact.cli.get_pr_metadata", return_value={}),
        patch("pr_impact.cli.run_ai_analysis", return_value=AIAnalysis(summary="ok")),
    ]


def test_run_pipeline_exits_1_when_get_changed_files_raises():
    refs = RefsResult(base="abc", head="def")
    with (
        patch("pr_impact.cli.get_changed_files", side_effect=RuntimeError("git boom")),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    assert exc_info.value.code == 1


def test_run_pipeline_exits_0_when_no_changed_files():
    refs = RefsResult(base="abc", head="def")
    with (
        patch("pr_impact.cli.get_changed_files", return_value=[]),
        pytest.raises(SystemExit) as exc_info,
    ):
        _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    assert exc_info.value.code == 0


def test_run_pipeline_returns_five_tuple():
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    changed, blast, interface, ai, meta = result
    assert changed[0].path == "foo.py"
    assert blast == []
    assert ai.summary == "ok"


def test_run_pipeline_passes_max_depth_to_blast_radius():
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2] as mock_blast,
        patches[3],
        patches[4],
        patches[5],
    ):
        _run_pipeline(".", MagicMock(), refs, 7, MagicMock())
    args, kwargs = mock_blast.call_args
    max_depth_val = kwargs.get("max_depth", args[2] if len(args) > 2 else None)
    assert max_depth_val == 7


def test_run_pipeline_import_graph_failure_continues():
    """Import graph failure is non-fatal — pipeline continues with empty graph."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[1] = patch("pr_impact.cli.build_import_graph", side_effect=RuntimeError("oops"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        changed, blast, interface, ai, meta = _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    assert changed  # pipeline still completed


def test_run_pipeline_blast_radius_failure_continues():
    """Blast radius failure is non-fatal — pipeline continues with empty list."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[2] = patch("pr_impact.cli.get_blast_radius", side_effect=RuntimeError("oops"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        changed, blast, interface, ai, meta = _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    assert blast == []


def test_run_pipeline_ai_failure_returns_empty_analysis():
    """AI analysis failure is non-fatal — returns empty AIAnalysis."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[5] = patch("pr_impact.cli.run_ai_analysis", side_effect=ValueError("no key"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        changed, blast, interface, ai, meta = _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    assert ai.summary == ""


def test_run_pipeline_classifier_failure_continues():
    """classify_changed_file raising is non-fatal — pipeline completes."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch("pr_impact.cli.classify_changed_file", side_effect=RuntimeError("parse error")),
    ):
        changed, blast, interface, ai, meta = _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    assert changed  # pipeline still completed


def test_run_pipeline_interface_change_failure_continues():
    """get_interface_changes raising is non-fatal — returns empty list."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch("pr_impact.cli.get_interface_changes", side_effect=RuntimeError("oops")),
    ):
        changed, blast, interface, ai, meta = _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    assert interface == []


def test_run_pipeline_ensure_commits_warning_on_failure():
    """ensure_commits_present raising issues a warning but pipeline continues."""
    refs = RefsResult(base="abc", head="def", fetch_pr_number=42)
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch("pr_impact.cli.ensure_commits_present", side_effect=RuntimeError("fetch failed")),
    ):
        changed, blast, interface, ai, meta = _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    assert changed  # pipeline completed despite the warning


def test_resolve_refs_interactive_no_token_warns(mock_repo):
    """Path D with no GitHub token should call _warn_no_github_token (line 343)."""
    with (
        patch("pr_impact.cli._get_github_token", return_value=None),
        patch("pr_impact.cli.detect_github_remote", return_value=("org", "repo", "origin")),
        patch("pr_impact.cli.fetch_open_prs", return_value=[]),
        patch("pr_impact.cli._stdin_is_interactive", return_value=True),
        patch("pr_impact.cli._warn_no_github_token") as mock_warn,
    ):
        result = _resolve_refs(mock_repo, pr_number=None, base=None, head=None)
    mock_warn.assert_called_once()
    assert result.base == _FALLBACK_BASE


def test_analyse_base_only_defaults_head_to_HEAD(runner):
    """--base without --head should default head to HEAD (lines 415-416)."""
    patches = _base_patches()
    with patches[0], patches[1] as mock_changed, patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = runner.invoke(
            main, ["analyse", "--repo", ".", "--base", "abc123"], env=_ENV
        )
    assert result.exit_code == 0
    call_args = mock_changed.call_args
    assert "HEAD" in call_args.args or "HEAD" in str(call_args)


def test_analyse_head_only_defaults_base_to_parent(runner):
    """--head without --base should default base to head~1 (lines 417-418)."""
    patches = _base_patches()
    with patches[0], patches[1] as mock_changed, patches[2], patches[3], patches[4], patches[5], patches[6]:
        result = runner.invoke(
            main, ["analyse", "--repo", ".", "--head", "myhead"], env=_ENV
        )
    assert result.exit_code == 0
    call_args = mock_changed.call_args
    assert "myhead~1" in call_args.args or "myhead~1" in str(call_args)


# ---------------------------------------------------------------------------
# _run_pipeline — ensure_commits_present skipped when fetch_pr_number is None
# ---------------------------------------------------------------------------


def test_run_pipeline_skips_ensure_commits_when_no_pr_number():
    """When fetch_pr_number is None, ensure_commits_present must not be called."""
    refs = RefsResult(base="abc", head="def")  # fetch_pr_number defaults to None
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch("pr_impact.cli.ensure_commits_present") as mock_ensure,
    ):
        _run_pipeline(".", MagicMock(), refs, 3, MagicMock())
    mock_ensure.assert_not_called()


# ---------------------------------------------------------------------------
# RefsResult — default field values
# ---------------------------------------------------------------------------


def test_refs_result_default_fields():
    r = RefsResult(base="abc123", head="def456")
    assert r.pr_title is None
    assert r.fetch_pr_number is None
    assert r.fetch_base_ref is None
    assert r.fetch_remote == "origin"


# ---------------------------------------------------------------------------
# _warn_no_github_token — warning text
# ---------------------------------------------------------------------------


def test_warn_no_github_token_outputs_warning_text():
    with patch("pr_impact.cli.stderr") as mock_stderr:
        _warn_no_github_token()
    printed = mock_stderr.print.call_args[0][0]
    assert "GITHUB_TOKEN" in printed


# ---------------------------------------------------------------------------
# _write_outputs — three paths
# ---------------------------------------------------------------------------


def test_write_outputs_writes_markdown_file(tmp_path):
    out = str(tmp_path / "report.md")
    _write_outputs(make_report(), out, None)
    with open(out) as fh:
        assert fh.read().startswith("# PR Impact Report")


def test_write_outputs_writes_json_file(tmp_path):
    out = str(tmp_path / "report.json")
    _write_outputs(make_report(), None, out)
    with open(out) as fh:
        data = json.loads(fh.read())
    assert "pr_title" in data


def test_write_outputs_does_nothing_when_both_none(tmp_path):
    _write_outputs(make_report(), None, None)
    assert list(tmp_path.iterdir()) == []
