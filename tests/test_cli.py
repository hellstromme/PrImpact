"""Unit and integration tests for pr_impact/cli.py."""

import json
from unittest.mock import MagicMock, patch

import git
import pytest
from click.testing import CliRunner

from pr_impact.cli import _invert_graph, main
from pr_impact.models import AIAnalysis, BlastRadiusEntry
from tests.helpers import make_file

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


def test_analyse_exits_1_when_api_key_missing(runner):
    # Pass empty string to override any ANTHROPIC_API_KEY already in the process environment
    result = runner.invoke(
        main,
        ["analyse", "--repo", ".", "--base", "abc", "--head", "def"],
        env={"ANTHROPIC_API_KEY": ""},
    )
    assert result.exit_code == 1
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
