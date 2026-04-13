"""Tests for the v1.0 history.py additions: save_run round-trip, load_runs, load_run."""

import json
import os
import sqlite3

import pytest

from pr_impact.history import _connect, load_run, load_run_summary, load_runs, save_run
from pr_impact.models import AIAnalysis, ImpactReport, RunSummary, SecuritySignal, SourceLocation
from tests.helpers import make_report, make_security_signal


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


# --- load_run_summary with NULL / corrupt report_json ---


def _insert_bare_run(db_path: str, run_uuid: str, report_json: str | None) -> None:
    """Insert a row directly into the DB to simulate legacy or corrupt data."""
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO runs (repo_path, base_sha, head_sha, timestamp, uuid, report_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("/repo", "aaa", "bbb", "2026-01-01T00:00:00+00:00", run_uuid, report_json),
    )
    conn.commit()
    conn.close()


def test_load_run_summary_null_report_json_returns_none(db_path):
    _insert_bare_run(db_path, "null-uuid-0001", None)
    assert load_run_summary(db_path, "null-uuid-0001") is None


def test_load_run_summary_malformed_json_returns_none(db_path):
    _insert_bare_run(db_path, "bad-uuid-0002", "not valid json {{")
    assert load_run_summary(db_path, "bad-uuid-0002") is None


def test_load_run_null_report_json_returns_none(db_path):
    _insert_bare_run(db_path, "null-uuid-0003", None)
    assert load_run(db_path, "null-uuid-0003") is None


def test_load_run_malformed_json_returns_none(db_path):
    _insert_bare_run(db_path, "bad-uuid-0004", "{invalid")
    assert load_run(db_path, "bad-uuid-0004") is None


# --- security signal round-trip (verifies sig.location.file fix) ---


def test_save_run_security_signal_uses_location_file(db_path):
    """save_run must read sig.location.file, not the removed sig.file_path attribute."""
    report = make_report(
        ai_analysis=AIAnalysis(
            security_signals=[
                make_security_signal(file_path="src/auth.py", line_number=42)
            ]
        )
    )
    # Must not raise AttributeError; if it did the old sig.file_path bug would surface here
    run_id = save_run(db_path, report, repo_path="/repo")
    assert run_id is not None

    # Verify the signal round-trips correctly through load_run
    loaded = load_run(db_path, run_id)
    assert loaded is not None
    assert len(loaded.ai_analysis.security_signals) == 1
    sig = loaded.ai_analysis.security_signals[0]
    assert sig.location.file == "src/auth.py"
    assert sig.location.line == 42


# --- _report_from_dict: malformed security_signal location ---


def test_load_run_non_dict_security_signal_location(db_path):
    """Non-dict location in stored security_signal JSON falls back to SourceLocation(file=str(loc))."""
    report = make_report()
    run_id = save_run(db_path, report, repo_path="/repo")

    # Corrupt the stored JSON by replacing the security_signal location with a plain string
    conn = _connect(db_path)
    row = conn.execute("SELECT report_json FROM runs WHERE uuid = ?", (run_id,)).fetchone()
    data = json.loads(row[0])
    data["ai_analysis"]["security_signals"] = [
        {
            "description": "test signal",
            "location": "src/evil.py:10",  # string, not dict
            "signal_type": "shell_invoke",
            "severity": "high",
            "why_unusual": "unusual",
            "suggested_action": "review",
        }
    ]
    conn.execute("UPDATE runs SET report_json = ? WHERE uuid = ?", (json.dumps(data), run_id))
    conn.commit()
    conn.close()

    loaded = load_run(db_path, run_id)
    assert loaded is not None
    assert len(loaded.ai_analysis.security_signals) == 1
    # Falls back to SourceLocation(file=str(loc)) — file is the raw string
    assert loaded.ai_analysis.security_signals[0].location.file == "src/evil.py:10"


# --- fault tolerance ---


def test_save_run_failure_is_silent():
    """save_run must never raise, even with a bad db path."""
    result = save_run("/\x00invalid\x00path/history.db", make_report(), repo_path="/repo")
    # Should return a UUID string without raising
    assert isinstance(result, str)
