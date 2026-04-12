"""Unit and integration tests for pr_impact/cli.py."""

import json
import os
from unittest.mock import MagicMock, patch

import git
import pytest
from click.testing import CliRunner

from pr_impact.analyzer import AnalyzerExit, ImpactAnalyzer, _invert_graph
from pr_impact.cli import (
    _FALLBACK_BASE,
    _FALLBACK_HEAD,
    _build_pr_title,
    _build_report,
    _check_severity_threshold,
    _format_pr_title,
    _get_github_token,
    _load_historical_context,
    _normalize_direct_refs,
    _print_banner,
    _resolve_explicit_pr,
    _resolve_interactive_pr,
    _resolve_refs,
    _run_verdict_if_requested,
    _validate_ref_options,
    _warn_no_github_token,
    _write_outputs,
    main,
)
from pr_impact.config import (
    load_config as _load_config,
    read_toml_config as _read_toml_config,
)
from pr_impact.models import AIAnalysis, Anomaly, BlastRadiusEntry, ImpactReport, RefsResult, Verdict
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
        patch("pr_impact.analyzer.get_changed_files", return_value=[make_file("foo.py")]),
        patch("pr_impact.analyzer.build_import_graph", return_value={}),
        patch("pr_impact.analyzer.get_blast_radius", return_value=[]),
        patch("pr_impact.analyzer.get_git_churn", return_value=0.0),
        patch("pr_impact.analyzer.get_pr_metadata", return_value={}),
        patch(
            "pr_impact.analyzer.run_ai_analysis",
            return_value=AIAnalysis(summary="test summary"),
        ),
        patch("pr_impact.analyzer.detect_pattern_signals", return_value=[]),
        patch("pr_impact.analyzer.check_dependency_integrity", return_value=[]),
    ]


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


def test_analyse_warns_when_api_key_missing(runner):
    # Without an API key the tool should still run; AI analysis is skipped with a warning
    patches = _base_patches()
    # Replace the run_ai_analysis patch with a real ValueError (what ai_layer raises)
    patches[6] = patch(
        "pr_impact.analyzer.run_ai_analysis",
        side_effect=ValueError("ANTHROPIC_API_KEY is not set"),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
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
        patch("pr_impact.analyzer.get_changed_files", side_effect=RuntimeError("boom")),
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
        patch("pr_impact.analyzer.get_changed_files", return_value=[]),
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
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert result.exit_code == 0


def test_analyse_success_report_header_in_stdout(runner):
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert "PR IMPACT REPORT" in result.output


def test_analyse_success_ai_summary_in_output(runner):
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
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
        patches[7],
        patches[8],
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
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
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
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            result = runner.invoke(
                main,
                ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--json", "out.json"],
                env=_ENV,
            )
        assert result.exit_code == 0
        with open("out.json") as fh:
            data = json.loads(fh.read())
        assert "pr_title" in data


def test_analyse_sarif_flag_writes_valid_sarif(runner):
    patches = _base_patches()
    with runner.isolated_filesystem():
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            result = runner.invoke(
                main,
                ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--sarif", "out.sarif"],
                env=_ENV,
            )
        assert result.exit_code == 0
        with open("out.sarif") as fh:
            data = json.loads(fh.read())
        assert data["version"] == "2.1.0"


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
        patches[7],
        patches[8],
    ):
        runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--max-depth", "5"],
            env=_ENV,
        )
    call_kwargs = mock_blast.call_args
    assert call_kwargs is not None
    # max_depth is clamped to 3 regardless of the CLI value
    args, kwargs = call_kwargs
    max_depth_value = kwargs.get("max_depth", args[2] if len(args) > 2 else None)
    assert max_depth_value == 3


def test_analyse_churn_called_for_blast_radius_entries(runner):
    blast_entry = BlastRadiusEntry(path="dep.py", distance=1, imported_symbols=[], churn_score=None)
    patches = [
        patch("pr_impact.cli.git.Repo", return_value=MagicMock()),
        patch("pr_impact.analyzer.get_changed_files", return_value=[make_file("foo.py")]),
        patch("pr_impact.analyzer.build_import_graph", return_value={}),
        patch("pr_impact.analyzer.get_blast_radius", return_value=[blast_entry]),
        patch("pr_impact.analyzer.get_git_churn", return_value=5.0),
        patch("pr_impact.analyzer.get_pr_metadata", return_value={}),
        patch("pr_impact.analyzer.run_ai_analysis", return_value=AIAnalysis()),
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
        base_p[7],
        base_p[8],
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
        base_p[7],
        base_p[8],
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
        base_p[7],
        base_p[8],
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
        base_p[7],
        base_p[8],
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
        base_p[7],
        base_p[8],
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
        base_p[7],
        base_p[8],
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
        base_p[7],
        base_p[8],
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
        base_p[7],
        base_p[8],
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
    """Patches for all external I/O in ImpactAnalyzer.run()."""
    return [
        patch("pr_impact.analyzer.get_changed_files", return_value=[make_file("foo.py")]),
        patch("pr_impact.analyzer.build_import_graph", return_value={}),
        patch("pr_impact.analyzer.get_blast_radius", return_value=[]),
        patch("pr_impact.analyzer.get_git_churn", return_value=0.0),
        patch("pr_impact.analyzer.get_pr_metadata", return_value={}),
        patch("pr_impact.analyzer.run_ai_analysis", return_value=AIAnalysis(summary="ok")),
        patch("pr_impact.analyzer.detect_pattern_signals", return_value=[]),
        patch("pr_impact.analyzer.check_dependency_integrity", return_value=[]),
    ]


def test_run_pipeline_exits_1_when_get_changed_files_raises():
    refs = RefsResult(base="abc", head="def")
    with (
        patch("pr_impact.analyzer.get_changed_files", side_effect=RuntimeError("git boom")),
        pytest.raises(AnalyzerExit) as exc_info,
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert exc_info.value.code == 1


def test_run_pipeline_exits_0_when_no_changed_files():
    refs = RefsResult(base="abc", head="def")
    with (
        patch("pr_impact.analyzer.get_changed_files", return_value=[]),
        pytest.raises(AnalyzerExit) as exc_info,
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert exc_info.value.code == 0


def test_run_pipeline_returns_six_tuple():
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        result = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    changed, _blast, _, ai, _, dep = result
    assert changed[0].path == "foo.py"
    assert _blast == []
    assert ai.summary == "ok"
    assert dep == []


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
        patches[6],
        patches[7],
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=7).run(MagicMock())
    args, kwargs = mock_blast.call_args
    max_depth_val = kwargs.get("max_depth", args[2] if len(args) > 2 else None)
    assert max_depth_val == 3  # clamped from 7 to the BFS depth cap


def test_run_pipeline_import_graph_failure_continues():
    """Import graph failure is non-fatal — pipeline continues with empty graph."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[1] = patch("pr_impact.analyzer.build_import_graph", side_effect=RuntimeError("oops"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        changed, _, _, _, _, _ = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert changed  # pipeline still completed


def test_run_pipeline_blast_radius_failure_continues():
    """Blast radius failure is non-fatal — pipeline continues with empty list."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[2] = patch("pr_impact.analyzer.get_blast_radius", side_effect=RuntimeError("oops"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        _, blast, _, _, _, _ = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert blast == []


def test_run_pipeline_ai_failure_returns_empty_analysis():
    """AI analysis failure is non-fatal — returns empty AIAnalysis."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[5] = patch("pr_impact.analyzer.run_ai_analysis", side_effect=ValueError("no key"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        _, _, _, ai, _, _ = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
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
        patch("pr_impact.analyzer.classify_changed_file", side_effect=RuntimeError("parse error")),
    ):
        changed, _, _, _, _, _ = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
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
        patch("pr_impact.analyzer.get_interface_changes", side_effect=RuntimeError("oops")),
    ):
        _, _, interface, _, _, _ = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
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
        patch("pr_impact.analyzer.ensure_commits_present", side_effect=RuntimeError("fetch failed")),
    ):
        changed, _, _, _, _, _ = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert changed  # pipeline completed despite the warning


def test_run_pipeline_churn_failure_sets_none_and_continues():
    """get_git_churn raising is non-fatal — churn_score falls back to None."""
    refs = RefsResult(base="abc", head="def")
    blast_entry = MagicMock()
    patches = _pipeline_patches()
    patches[2] = patch("pr_impact.analyzer.get_blast_radius", return_value=[blast_entry])
    patches[3] = patch("pr_impact.analyzer.get_git_churn", side_effect=RuntimeError("git error"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        _, blast, _, _, _, _ = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert blast_entry.churn_score is None


def test_run_pipeline_metadata_failure_returns_empty_dict():
    """get_pr_metadata raising is non-fatal — metadata falls back to {}."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[4] = patch("pr_impact.analyzer.get_pr_metadata", side_effect=RuntimeError("no history"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        _, _, _, _, meta, _ = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert meta == {}


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
    with patches[0], patches[1] as mock_changed, patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main, ["analyse", "--repo", ".", "--base", "abc123"], env=_ENV
        )
    assert result.exit_code == 0
    call_args = mock_changed.call_args
    assert "HEAD" in call_args.args or "HEAD" in str(call_args)


def test_analyse_head_only_defaults_base_to_parent(runner):
    """--head without --base should default base to head~1 (lines 417-418)."""
    patches = _base_patches()
    with patches[0], patches[1] as mock_changed, patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
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
        patch("pr_impact.analyzer.ensure_commits_present") as mock_ensure,
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
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
    _write_outputs(make_report(), out, None, None)
    with open(out) as fh:
        assert fh.read().startswith("# PR Impact Report")


def test_write_outputs_writes_json_file(tmp_path):
    out = str(tmp_path / "report.json")
    _write_outputs(make_report(), None, out, None)
    with open(out) as fh:
        data = json.loads(fh.read())
    assert "pr_title" in data


def test_write_outputs_writes_sarif_file(tmp_path):
    out = str(tmp_path / "report.sarif")
    _write_outputs(make_report(), None, None, out)
    with open(out) as fh:
        data = json.loads(fh.read())
    assert data["version"] == "2.1.0"


def test_write_outputs_does_nothing_when_all_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_outputs(make_report(), None, None, None)
    assert list(tmp_path.iterdir()) == []


def test_write_outputs_markdown_error_does_not_raise(tmp_path):
    bad_path = str(tmp_path / "missing_dir" / "report.md")
    with patch("pr_impact.cli.stderr") as mock_stderr:
        _write_outputs(make_report(), bad_path, None, None)
    assert mock_stderr.print.called
    assert "Warning" in mock_stderr.print.call_args[0][0]
    assert bad_path in mock_stderr.print.call_args[0][0]


def test_write_outputs_json_error_does_not_raise(tmp_path):
    bad_path = str(tmp_path / "missing_dir" / "report.json")
    with patch("pr_impact.cli.stderr") as mock_stderr:
        _write_outputs(make_report(), None, bad_path, None)
    assert mock_stderr.print.called
    assert bad_path in mock_stderr.print.call_args[0][0]


def test_write_outputs_sarif_error_does_not_raise(tmp_path):
    bad_path = str(tmp_path / "missing_dir" / "report.sarif")
    with patch("pr_impact.cli.stderr") as mock_stderr:
        _write_outputs(make_report(), None, None, bad_path)
    assert mock_stderr.print.called
    assert bad_path in mock_stderr.print.call_args[0][0]


# ---------------------------------------------------------------------------
# _read_toml_config — error paths (lines 68, 79-80)
# ---------------------------------------------------------------------------


def test_read_toml_config_returns_none_when_file_missing(monkeypatch, tmp_path):
    """Line 68: CONFIG_PATH does not exist → return None immediately."""
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", tmp_path / "nonexistent.toml")
    assert _read_toml_config() is None


def test_read_toml_config_parses_valid_toml(monkeypatch, tmp_path):
    """Happy path: valid TOML returns the parsed dict."""
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'anthropic_api_key = "sk-test"\n')
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    result = _read_toml_config()
    assert result == {"anthropic_api_key": "sk-test"}


def test_read_toml_config_returns_none_on_invalid_toml(monkeypatch, tmp_path):
    """Lines 79-80: file exists but is malformed TOML → except block returns None."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is not = = valid toml !!\n", encoding="utf-8")
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    assert _read_toml_config() is None


# ---------------------------------------------------------------------------
# _load_config — error paths (lines 87-89, 93, 99-103)
# ---------------------------------------------------------------------------


def test_load_config_does_nothing_when_file_absent(monkeypatch, tmp_path):
    """File absent and CONFIG_PATH.exists() is False → silent return, no warning."""
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", tmp_path / "nonexistent.toml")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("pr_impact.config._stderr") as mock_stderr:
        _load_config()
    mock_stderr.print.assert_not_called()


def test_load_config_warns_when_config_file_unparseable(monkeypatch, tmp_path):
    """Lines 87-89: file exists but can't be parsed → prints error and returns."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("bad toml == ===\n", encoding="utf-8")
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    with patch("pr_impact.config._stderr") as mock_stderr:
        _load_config()
    mock_stderr.print.assert_called_once()
    assert "Could not parse" in mock_stderr.print.call_args[0][0]


def test_load_config_returns_early_when_no_api_key_in_config(monkeypatch, tmp_path):
    """Line 93: config exists but has no anthropic_api_key → return without touching env."""
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'github_token = "tok"\n')
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _load_config()
    assert os.environ.get("ANTHROPIC_API_KEY") is None


def test_load_config_warns_on_unresolved_env_var_in_api_key(monkeypatch, tmp_path):
    """Lines 99-103: api_key is an env var placeholder that isn't expanded → prints error."""
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'anthropic_api_key = "$UNSET_PRIMPACT_KEY_XYZ"\n')
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("UNSET_PRIMPACT_KEY_XYZ", raising=False)
    with patch("pr_impact.config._stderr") as mock_stderr:
        _load_config()
    mock_stderr.print.assert_called_once()
    assert "environment variable" in mock_stderr.print.call_args[0][0]


def test_load_config_does_not_overwrite_existing_api_key(monkeypatch, tmp_path):
    """Line 94-95: ANTHROPIC_API_KEY already in env → config value is ignored."""
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'anthropic_api_key = "from-config"\n')
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "already-set")
    _load_config()
    assert os.environ["ANTHROPIC_API_KEY"] == "already-set"


# ---------------------------------------------------------------------------
# _get_github_token — config file paths (lines 148-157)
# ---------------------------------------------------------------------------


def test_get_github_token_returns_none_when_no_config_file(monkeypatch, tmp_path):
    """Lines 148-150: GITHUB_TOKEN absent and no config file → None."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", tmp_path / "nonexistent.toml")
    assert _get_github_token() is None


def test_get_github_token_returns_none_when_not_in_config(monkeypatch, tmp_path):
    """Lines 151-153: config exists but has no github_token entry → None."""
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'anthropic_api_key = "sk-test"\n')
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    assert _get_github_token() is None


def test_get_github_token_returns_value_from_config(monkeypatch, tmp_path):
    """Line 156: github_token found in config and expands cleanly → return it."""
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'github_token = "ghp-config-token"\n')
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    assert _get_github_token() == "ghp-config-token"


def test_get_github_token_returns_none_on_unresolved_env_var(monkeypatch, tmp_path):
    """Lines 154-155: github_token is an unexpanded env var placeholder → None."""
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'github_token = "$UNSET_GH_TOKEN_XYZ"\n')
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("UNSET_GH_TOKEN_XYZ", raising=False)
    monkeypatch.setattr("pr_impact.config.CONFIG_PATH", cfg)
    assert _get_github_token() is None


# ---------------------------------------------------------------------------
# _print_banner — fallback version (lines 43-44)
# ---------------------------------------------------------------------------


def test_print_banner_uses_dev_when_package_version_unavailable():
    """Lines 43-44: importlib.metadata.version raises → ver falls back to 'dev'."""
    with (
        patch("pr_impact.cli.stderr") as mock_stderr,
        patch("importlib.metadata.version", side_effect=Exception("not installed")),
    ):
        _print_banner()
        panel = mock_stderr.print.call_args[0][0]
        assert "vdev" in panel.renderable.plain


# ---------------------------------------------------------------------------
# --fail-on-severity flag
# ---------------------------------------------------------------------------

_HIGH_ANOMALY = Anomaly(description="risky", location="foo.py", severity="high")
_MEDIUM_ANOMALY = Anomaly(description="concern", location="baz.py", severity="medium")
_LOW_ANOMALY = Anomaly(description="minor", location="bar.py", severity="low")


def test_fail_on_severity_none_always_exits_0(runner):
    """--fail-on-severity none (default) never fails even with high-severity anomalies."""
    patches = _base_patches()
    patches[6] = patch(
        "pr_impact.analyzer.run_ai_analysis",
        return_value=AIAnalysis(summary="s", anomalies=[_HIGH_ANOMALY]),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def",
             "--fail-on-severity", "none"],
            env=_ENV,
        )
    assert result.exit_code == 0


def test_fail_on_severity_high_exits_1_on_high_anomaly(runner):
    """--fail-on-severity high exits 1 when a high-severity anomaly is present."""
    patches = _base_patches()
    patches[6] = patch(
        "pr_impact.analyzer.run_ai_analysis",
        return_value=AIAnalysis(summary="s", anomalies=[_HIGH_ANOMALY]),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def",
             "--fail-on-severity", "high"],
            env=_ENV,
        )
    assert result.exit_code == 1


def test_fail_on_severity_high_exits_0_when_no_anomalies(runner):
    """--fail-on-severity high exits 0 when there are no anomalies at all."""
    patches = _base_patches()
    patches[6] = patch(
        "pr_impact.analyzer.run_ai_analysis",
        return_value=AIAnalysis(summary="s", anomalies=[]),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def",
             "--fail-on-severity", "high"],
            env=_ENV,
        )
    assert result.exit_code == 0


def test_fail_on_severity_medium_exits_1_on_medium_anomaly(runner):
    """--fail-on-severity medium exits 1 when a medium-severity anomaly is present."""
    patches = _base_patches()
    patches[6] = patch(
        "pr_impact.analyzer.run_ai_analysis",
        return_value=AIAnalysis(summary="s", anomalies=[_MEDIUM_ANOMALY]),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def",
             "--fail-on-severity", "medium"],
            env=_ENV,
        )
    assert result.exit_code == 1


def test_fail_on_severity_medium_threshold_skips_low_anomaly(runner):
    """--fail-on-severity medium exits 0 when only a low-severity anomaly is present."""
    patches = _base_patches()
    patches[6] = patch(
        "pr_impact.analyzer.run_ai_analysis",
        return_value=AIAnalysis(summary="s", anomalies=[_LOW_ANOMALY]),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def",
             "--fail-on-severity", "medium"],
            env=_ENV,
        )
    assert result.exit_code == 0


def test_fail_on_severity_low_exits_1_on_low_anomaly(runner):
    """--fail-on-severity low exits 1 when a low-severity anomaly is present."""
    patches = _base_patches()
    patches[6] = patch(
        "pr_impact.analyzer.run_ai_analysis",
        return_value=AIAnalysis(summary="s", anomalies=[_LOW_ANOMALY]),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def",
             "--fail-on-severity", "low"],
            env=_ENV,
        )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# _run_pipeline — security steps
# ---------------------------------------------------------------------------


def test_run_pipeline_detect_signals_failure_continues():
    """detect_pattern_signals raising is non-fatal — continues with empty list."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch("pr_impact.analyzer.detect_pattern_signals", side_effect=RuntimeError("scan error")),
        patches[7],
    ):
        _, _, _, _, _, dep = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    # pipeline completed; dep is from check_dependency_integrity mock (returns [])
    assert dep == []


def test_run_pipeline_detect_signals_failure_logs_warning():
    """detect_pattern_signals raising logs a warning to stderr."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patch("pr_impact.analyzer.detect_pattern_signals", side_effect=RuntimeError("scan error")),
        patches[7],
        patch("pr_impact.analyzer.stderr") as mock_stderr,
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert mock_stderr.print.called
    assert "scan error" in mock_stderr.print.call_args[0][0]


def test_run_pipeline_check_integrity_failure_continues():
    """check_dependency_integrity raising is non-fatal — continues with empty list."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patch("pr_impact.analyzer.check_dependency_integrity", side_effect=RuntimeError("dep error")),
    ):
        _, _, _, _, _, dep = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert dep == []


def test_run_pipeline_check_integrity_failure_logs_warning():
    """check_dependency_integrity raising logs a warning to stderr."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patch("pr_impact.analyzer.check_dependency_integrity", side_effect=RuntimeError("dep error")),
        patch("pr_impact.analyzer.stderr") as mock_stderr,
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert mock_stderr.print.called
    assert "dep error" in mock_stderr.print.call_args[0][0]


def test_run_pipeline_detect_signals_warning_contains_warning_prefix():
    """detect_pattern_signals failure warning uses [yellow]Warning:[/yellow] prefix."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0], patches[1], patches[2], patches[3], patches[4], patches[5],
        patch("pr_impact.analyzer.detect_pattern_signals", side_effect=RuntimeError("boom")),
        patches[7],
        patch("pr_impact.analyzer.stderr") as mock_stderr,
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert mock_stderr.print.called
    text = mock_stderr.print.call_args[0][0]
    assert "Warning" in text


def test_run_pipeline_check_integrity_warning_contains_warning_prefix():
    """check_dependency_integrity failure warning uses [yellow]Warning:[/yellow] prefix."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    with (
        patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6],
        patch("pr_impact.analyzer.check_dependency_integrity", side_effect=RuntimeError("boom")),
        patch("pr_impact.analyzer.stderr") as mock_stderr,
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert mock_stderr.print.called
    text = mock_stderr.print.call_args[0][0]
    assert "Warning" in text


def test_run_pipeline_dependency_issues_returned_as_sixth_element():
    from pr_impact.models import DependencyIssue
    dep_issue = DependencyIssue(package_name="evil-pkg", issue_type="typosquat",
                                description="suspicious", severity="high")
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[7] = patch("pr_impact.analyzer.check_dependency_integrity", return_value=[dep_issue])
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        _, _, _, _, _, dep = ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(MagicMock())
    assert dep == [dep_issue]


def test_run_pipeline_progress_shows_4_calls_when_signals_present():
    """When detect_pattern_signals returns signals, progress message says 4 API calls."""
    from pr_impact.models import SecuritySignal, SourceLocation
    sig = SecuritySignal(description="x", location=SourceLocation(file="f.py", line=1),
                         signal_type="shell_invoke", severity="high",
                         why_unusual="u", suggested_action="s")
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    patches[6] = patch("pr_impact.analyzer.detect_pattern_signals", return_value=[sig])
    mock_progress = MagicMock()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(mock_progress)
    descriptions = [str(c) for c in mock_progress.update.call_args_list]
    assert any("4" in d for d in descriptions)


def test_run_pipeline_progress_shows_3_calls_when_no_signals():
    """When detect_pattern_signals returns [], progress message says 3 API calls."""
    refs = RefsResult(base="abc", head="def")
    patches = _pipeline_patches()
    mock_progress = MagicMock()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(mock_progress)
    descriptions = [str(c) for c in mock_progress.update.call_args_list]
    assert any("3" in d for d in descriptions)


# ---------------------------------------------------------------------------
# analyse command — dependency_issues passed to ImpactReport
# ---------------------------------------------------------------------------


def test_analyse_dependency_issues_in_report(runner):
    """dependency_issues from the pipeline end up in the ImpactReport."""
    from pr_impact.models import DependencyIssue
    dep_issue = DependencyIssue(package_name="requets", issue_type="typosquat",
                                description="similar to requests", severity="high")
    patches = _base_patches()
    patches[8] = patch("pr_impact.analyzer.check_dependency_integrity", return_value=[dep_issue])
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
        patches[8],
    ):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    assert result.exit_code == 0
    # Dep issue should appear in the terminal output (Security Signals section)
    assert "requets" in result.output


# ---------------------------------------------------------------------------
# --verdict flag
# ---------------------------------------------------------------------------


def _with_verdict(verdict):
    """Patch run_verdict_analysis to return a fixed verdict."""
    return patch("pr_impact.cli.run_verdict_analysis", return_value=verdict)


def _clean_verdict():
    from pr_impact.models import Verdict
    return Verdict(status="clean", agent_should_continue=False, rationale="All good.", blockers=[])


def _blocker_verdict():
    from pr_impact.models import Verdict, VerdictBlocker
    return Verdict(
        status="has_blockers",
        agent_should_continue=True,
        rationale="Missing test coverage.",
        blockers=[VerdictBlocker(category="test_gap", description="login untested", location="auth.py")],
    )


def test_verdict_json_flag_writes_json_file(runner):
    patches = _base_patches()
    with runner.isolated_filesystem():
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
                _with_verdict(_clean_verdict()):
            runner.invoke(
                main,
                ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--verdict-json", "verdict.json"],
                env=_ENV,
            )
        import json as _json
        data = _json.loads(open("verdict.json").read())
    assert data["status"] == "clean"
    assert data["agent_should_continue"] is False


def test_verdict_flag_alone_does_not_write_file(runner):
    """--verdict alone prints to terminal but writes no file."""
    patches = _base_patches()
    with runner.isolated_filesystem():
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
                _with_verdict(_clean_verdict()):
            runner.invoke(
                main,
                ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--verdict"],
                env=_ENV,
            )
        import os
        assert not os.path.exists("verdict.json")


def test_verdict_clean_exits_0(runner):
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
            _with_verdict(_clean_verdict()):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--verdict"],
            env=_ENV,
        )
    assert result.exit_code == 0


def test_verdict_has_blockers_exits_2(runner):
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
            _with_verdict(_blocker_verdict()):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--verdict"],
            env=_ENV,
        )
    assert result.exit_code == 2


def test_verdict_output_shown_in_terminal(runner):
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
            _with_verdict(_clean_verdict()):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--verdict"],
            env=_ENV,
        )
    assert "AGENT VERDICT" in result.output


def test_verdict_json_implies_verdict(runner):
    """--verdict-json alone (without --verdict) still runs verdict analysis."""
    patches = _base_patches()
    with runner.isolated_filesystem():
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
                _with_verdict(_clean_verdict()) as mock_v:
            runner.invoke(
                main,
                ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--verdict-json", "v.json"],
                env=_ENV,
            )
    mock_v.assert_called_once()


def test_verdict_api_failure_exits_0(runner):
    """Verdict API failure → warning printed, exit 0 (loop terminates safely)."""
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
            patch("pr_impact.cli.run_verdict_analysis", side_effect=RuntimeError("timeout")):
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--verdict"],
            env=_ENV,
        )
    assert result.exit_code == 0


def test_verdict_not_called_without_flag(runner):
    """run_verdict_analysis is never called unless --verdict or --verdict-json is passed."""
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
            patch("pr_impact.cli.run_verdict_analysis") as mock_v:
        runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
            env=_ENV,
        )
    mock_v.assert_not_called()


def test_analyse_check_osv_passes_flag_to_check_dependency_integrity(runner):
    """--check-osv flag is forwarded as osv_check=True to check_dependency_integrity."""
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], \
            patches[8] as mock_dep:
        runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def", "--check-osv"],
            env=_ENV,
        )
    mock_dep.assert_called_once()
    _, kwargs = mock_dep.call_args
    assert kwargs.get("osv_check") is True


def test_verdict_json_write_failure_logs_warning(runner):
    """I/O error during verdict JSON write is caught and logged, not raised."""
    patches = _base_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], \
            _with_verdict(_clean_verdict()), \
            patch("pr_impact.cli.Path") as mock_path:
        mock_path.return_value.write_text.side_effect = PermissionError("denied")
        result = runner.invoke(
            main,
            ["analyse", "--repo", ".", "--base", "abc", "--head", "def",
             "--verdict", "--verdict-json", "v.json"],
            env=_ENV,
        )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Unit tests for pure helper functions extracted from analyse()
# ---------------------------------------------------------------------------


class TestValidateRefOptions:
    def test_passes_when_only_pr_given(self):
        _validate_ref_options(42, None, None)  # no exception

    def test_passes_when_only_base_head_given(self):
        _validate_ref_options(None, "abc", "def")  # no exception

    def test_passes_when_nothing_given(self):
        _validate_ref_options(None, None, None)  # no exception

    def test_exits_when_pr_and_base_given(self):
        with pytest.raises(SystemExit) as exc:
            _validate_ref_options(42, "abc", None)
        assert exc.value.code == 1

    def test_exits_when_pr_and_head_given(self):
        with pytest.raises(SystemExit) as exc:
            _validate_ref_options(42, None, "def")
        assert exc.value.code == 1

    def test_exits_when_pr_and_both_given(self):
        with pytest.raises(SystemExit) as exc:
            _validate_ref_options(42, "abc", "def")
        assert exc.value.code == 1


class TestNormalizeDirectRefs:
    def test_noop_when_pr_given(self):
        base, head = _normalize_direct_refs(42, None, None)
        assert base is None
        assert head is None

    def test_noop_when_neither_given(self):
        base, head = _normalize_direct_refs(None, None, None)
        assert base is None
        assert head is None

    def test_both_given_unchanged(self):
        base, head = _normalize_direct_refs(None, "abc", "def")
        assert base == "abc"
        assert head == "def"

    def test_head_only_defaults_base(self):
        base, head = _normalize_direct_refs(None, None, "abc123")
        assert head == "abc123"
        assert base == "abc123~1"

    def test_base_only_defaults_head_to_HEAD(self):
        base, head = _normalize_direct_refs(None, "abc", None)
        assert base == "abc"
        assert head == "HEAD"

    def test_neither_given_with_pr_none_leaves_both_none(self):
        base, head = _normalize_direct_refs(None, None, None)
        assert base is None and head is None


class TestBuildPrTitle:
    def test_uses_pr_title_when_present(self):
        refs = RefsResult(base="a", head="b", pr_title="#42: feat: add thing")
        assert _build_pr_title(refs, {}) == "#42: feat: add thing"

    def test_uses_first_commit_line_when_no_pr_title(self):
        refs = RefsResult(base="a", head="b")
        metadata = {"commits": ["fix: typo\n\nmore detail"]}
        assert _build_pr_title(refs, metadata) == "fix: typo"

    def test_falls_back_to_sha_range(self):
        refs = RefsResult(base="abc1234567", head="def9876543")
        title = _build_pr_title(refs, {})
        assert "abc1234" in title
        assert "def9876" in title

    def test_falls_back_to_sha_range_when_commits_empty(self):
        refs = RefsResult(base="abc1234567", head="def9876543")
        title = _build_pr_title(refs, {"commits": []})
        assert "abc1234" in title


class TestCheckSeverityThreshold:
    def _anomaly(self, severity: str) -> Anomaly:
        return Anomaly(description="x", location="f.py", severity=severity)

    def test_none_never_triggers(self):
        assert _check_severity_threshold("none", [self._anomaly("high")]) is False

    def test_breaches_at_matching_level(self):
        assert _check_severity_threshold("high", [self._anomaly("high")]) is True

    def test_breaches_above_level(self):
        assert _check_severity_threshold("low", [self._anomaly("high")]) is True

    def test_does_not_breach_below_level(self):
        assert _check_severity_threshold("high", [self._anomaly("low")]) is False

    def test_empty_anomalies_never_triggers(self):
        assert _check_severity_threshold("low", []) is False


class TestBuildReport:
    def test_maps_hotspots_to_historical_hotspots(self):
        refs = RefsResult(base="abc", head="def")
        hotspots = [{"file": "src/foo.py", "appearances": 5}]
        report = _build_report(
            "title", refs, [], [], [], AIAnalysis(), [], hotspots
        )
        assert isinstance(report, ImpactReport)
        assert len(report.historical_hotspots) == 1
        assert report.historical_hotspots[0].file == "src/foo.py"
        assert report.historical_hotspots[0].appearances == 5

    def test_handles_empty_hotspots(self):
        refs = RefsResult(base="abc", head="def")
        report = _build_report("title", refs, [], [], [], AIAnalysis(), [], None)
        assert report.historical_hotspots == []

    def test_sets_base_and_head_sha(self):
        refs = RefsResult(base="aaa", head="bbb")
        report = _build_report("title", refs, [], [], [], AIAnalysis(), [], None)
        assert report.base_sha == "aaa"
        assert report.head_sha == "bbb"

    def test_all_pipeline_fields_propagate(self):
        refs = RefsResult(base="abc", head="def")
        f = make_file("src/foo.py")
        blast = [BlastRadiusEntry(path="src/bar.py", distance=1, imported_symbols=[], churn_score=None)]
        ai = AIAnalysis(summary="ok")
        report = _build_report("title", refs, [f], blast, [], ai, [], None)
        assert len(report.changed_files) == 1
        assert len(report.blast_radius) == 1
        assert report.ai_analysis.summary == "ok"


class TestResolveExplicitPr:
    def test_fetch_pr_runtime_error_exits_1(self):
        with patch("pr_impact.cli.fetch_pr", side_effect=RuntimeError("not found")):
            with pytest.raises(SystemExit) as exc:
                _resolve_explicit_pr("owner", "repo", 42, None, "origin")
        assert exc.value.code == 1

    def test_successful_fetch_returns_refs_result(self):
        pr_data = {
            "base": {"sha": "base_sha", "ref": "main"},
            "head": {"sha": "head_sha"},
            "title": "feat: something",
        }
        with patch("pr_impact.cli.fetch_pr", return_value=pr_data):
            result = _resolve_explicit_pr("owner", "repo", 7, "tok", "origin")
        assert result.base == "base_sha"
        assert result.head == "head_sha"
        assert result.fetch_pr_number == 7
        assert result.fetch_base_ref == "main"


class TestResolveInteractivePr:
    def test_fetch_open_prs_runtime_error_returns_fallback(self):
        with patch("pr_impact.cli.fetch_open_prs", side_effect=RuntimeError("timeout")):
            result = _resolve_interactive_pr("owner", "repo", None, "origin")
        assert result.base == _FALLBACK_BASE
        assert result.head == _FALLBACK_HEAD

    def test_no_open_prs_returns_fallback(self):
        with patch("pr_impact.cli.fetch_open_prs", return_value=[]):
            result = _resolve_interactive_pr("owner", "repo", "tok", "origin")
        assert result.base == _FALLBACK_BASE
        assert result.head == _FALLBACK_HEAD


class TestLoadHistoricalContext:
    def test_no_history_flag_returns_none_none(self):
        anomaly_history, hotspots = _load_historical_context("/db", "/repo", no_history=True)
        assert anomaly_history is None
        assert hotspots is None

    def test_run_count_zero_returns_none_none(self):
        with patch("pr_impact.cli.get_run_count", return_value=0):
            anomaly_history, hotspots = _load_historical_context("/db", "/repo", no_history=False)
        assert anomaly_history is None
        assert hotspots is None

    def test_run_count_positive_loads_context(self):
        with patch("pr_impact.cli.get_run_count", return_value=3), \
             patch("pr_impact.cli.load_hotspots", return_value=[{"file": "f.py", "appearances": 2}]), \
             patch("pr_impact.cli.load_anomaly_patterns", return_value=[{"file": "f.py", "description": "x", "run_count": 2}]):
            anomaly_history, hotspots = _load_historical_context("/db", "/repo", no_history=False)
        assert hotspots is not None
        assert anomaly_history is not None

    def test_empty_history_results_return_none(self):
        with patch("pr_impact.cli.get_run_count", return_value=5), \
             patch("pr_impact.cli.load_hotspots", return_value=[]), \
             patch("pr_impact.cli.load_anomaly_patterns", return_value=[]):
            anomaly_history, hotspots = _load_historical_context("/db", "/repo", no_history=False)
        assert hotspots is None
        assert anomaly_history is None


class TestRunVerdictIfRequested:
    def _clean_verdict(self):
        return Verdict(status="clean", agent_should_continue=False, rationale="ok", blockers=[])

    def _blocker_verdict(self):
        return Verdict(status="blocked", agent_should_continue=True, rationale="stop", blockers=[])

    def test_early_return_when_not_requested(self):
        verdict, should_continue = _run_verdict_if_requested(False, None, AIAnalysis(), [])
        assert verdict is None
        assert should_continue is False

    def test_exception_from_analysis_returns_none_false(self):
        with patch("pr_impact.cli.run_verdict_analysis", side_effect=ValueError("API down")), \
             patch("pr_impact.cli.render_verdict_terminal"):
            verdict, should_continue = _run_verdict_if_requested(True, None, AIAnalysis(), [])
        assert verdict is None
        assert should_continue is False

    def test_file_write_error_does_not_raise(self, tmp_path):
        v = self._clean_verdict()
        with patch("pr_impact.cli.run_verdict_analysis", return_value=v), \
             patch("pr_impact.cli.render_verdict_terminal"), \
             patch("pr_impact.cli.Path") as mock_path:
            mock_path.return_value.write_text.side_effect = PermissionError("denied")
            verdict, should_continue = _run_verdict_if_requested(True, "v.json", AIAnalysis(), [])
        assert verdict is v
        assert should_continue is False  # agent_should_continue is False on clean verdict

    def test_returns_verdict_and_agent_flag(self):
        v = self._blocker_verdict()
        with patch("pr_impact.cli.run_verdict_analysis", return_value=v), \
             patch("pr_impact.cli.render_verdict_terminal"):
            verdict, should_continue = _run_verdict_if_requested(True, None, AIAnalysis(), [])
        assert verdict is v
        assert should_continue is True

    def test_triggered_by_verdict_output_alone(self):
        v = self._clean_verdict()
        with patch("pr_impact.cli.run_verdict_analysis", return_value=v), \
             patch("pr_impact.cli.render_verdict_terminal"), \
             patch("pr_impact.cli.Path"):
            verdict, _ = _run_verdict_if_requested(False, "v.json", AIAnalysis(), [])
        assert verdict is v
