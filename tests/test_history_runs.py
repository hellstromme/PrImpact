"""Tests for the v1.0 history.py additions: save_run round-trip, load_runs, load_run."""

import os

import pytest

from pr_impact.history import load_run, load_run_summary, load_runs, save_run
from pr_impact.models import ImpactReport, RunSummary
from tests.helpers import make_report


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / ".primpact" / "history.db")


@pytest.fixture()
def report() -> ImpactReport:
    return make_report()


# --- save_run / load_run round-trip ---


def test_save_run_returns_uuid(db_path, report):
    run_id = save_run(db_path, report, repo_path="/repo")
    assert isinstance(run_id, str)
    assert len(run_id) == 36  # standard UUID format


def test_save_run_uses_provided_uuid(db_path, report):
    fixed_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = save_run(db_path, report, repo_path="/repo", run_uuid=fixed_uuid)
    assert result == fixed_uuid


def test_load_run_round_trip(db_path, report):
    run_id = save_run(db_path, report, repo_path="/repo")
    loaded = load_run(db_path, run_id)
    assert loaded is not None
    assert loaded.pr_title == report.pr_title
    assert loaded.base_sha == report.base_sha
    assert loaded.head_sha == report.head_sha
    assert len(loaded.blast_radius) == len(report.blast_radius)
    assert len(loaded.changed_files) == len(report.changed_files)
    assert loaded.ai_analysis.summary == report.ai_analysis.summary
    assert len(loaded.ai_analysis.anomalies) == len(report.ai_analysis.anomalies)
    assert loaded.ai_analysis.anomalies[0].description == report.ai_analysis.anomalies[0].description


def test_load_run_missing_returns_none(db_path):
    run_id = save_run(db_path, make_report(), repo_path="/repo")
    result = load_run(db_path, "00000000-0000-0000-0000-000000000000")
    assert result is None


def test_load_run_nonexistent_db_returns_none(tmp_path):
    result = load_run(str(tmp_path / "nonexistent.db"), "any-uuid")
    assert result is None


# --- load_runs ---


def test_load_runs_returns_newest_first(db_path):
    repo = "/repo"
    for i in range(3):
        save_run(db_path, make_report(pr_title=f"PR #{i}"), repo_path=repo)
    summaries = load_runs(db_path, repo)
    assert len(summaries) == 3
    # Most recent insertion last (timestamp ordering DESC means PR #2 is first)
    titles = [s.pr_title for s in summaries]
    assert titles == ["PR #2", "PR #1", "PR #0"]


def test_load_runs_correct_counts(db_path):
    run_id = save_run(db_path, make_report(), repo_path="/repo")
    summaries = load_runs(db_path, "/repo")
    assert len(summaries) == 1
    s = summaries[0]
    assert s.id == run_id
    assert s.blast_radius_count == 1
    assert s.anomaly_count == 1


def test_load_runs_pagination(db_path):
    repo = "/repo"
    for i in range(5):
        save_run(db_path, make_report(pr_title=f"PR #{i}"), repo_path=repo)
    page1 = load_runs(db_path, repo, limit=2, offset=0)
    page2 = load_runs(db_path, repo, limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    # Pages should not overlap
    ids1 = {s.id for s in page1}
    ids2 = {s.id for s in page2}
    assert ids1.isdisjoint(ids2)


def test_load_runs_filters_by_repo(db_path):
    save_run(db_path, make_report(), repo_path="/repo-a")
    save_run(db_path, make_report(), repo_path="/repo-b")
    assert len(load_runs(db_path, "/repo-a")) == 1
    assert len(load_runs(db_path, "/repo-b")) == 1
    assert len(load_runs(db_path, "/repo-c")) == 0


def test_load_runs_nonexistent_db_returns_empty(tmp_path):
    result = load_runs(str(tmp_path / "nonexistent.db"), "/repo")
    assert result == []


# --- load_run_summary ---


def test_load_run_summary_returns_correct_fields(db_path, report):
    run_id = save_run(db_path, report, repo_path="/repo")
    summary = load_run_summary(db_path, run_id)
    assert isinstance(summary, RunSummary)
    assert summary.id == run_id
    assert summary.repo_path == "/repo"
    assert summary.base_sha == report.base_sha


def test_load_run_summary_missing_returns_none(db_path):
    assert load_run_summary(db_path, "no-such-uuid") is None


# --- fault tolerance ---


def test_save_run_failure_is_silent():
    """save_run must never raise, even with a bad db path."""
    result = save_run("/\x00invalid\x00path/history.db", make_report(), repo_path="/repo")
    # Should return a UUID string without raising
    assert isinstance(result, str)
