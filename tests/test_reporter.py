"""Unit tests for pr_impact/reporter.py."""

import json

from pr_impact.models import (
    AIAnalysis,
    Anomaly,
    Assumption,
    BlastRadiusEntry,
    Decision,
    InterfaceChange,
    TestGap,
)
from pr_impact.reporter import render_json, render_markdown
from tests.helpers import make_file, make_report

# ---------------------------------------------------------------------------
# render_markdown: header and structure
# ---------------------------------------------------------------------------


def test_markdown_starts_with_h1():
    md = render_markdown(make_report())
    assert md.startswith("# PR Impact Report")


def test_markdown_contains_pr_title():
    md = render_markdown(make_report(pr_title="fix: null pointer"))
    assert "fix: null pointer" in md


def test_markdown_sha_truncated_to_7():
    md = render_markdown(make_report(base_sha="aabbccdd1122", head_sha="eeff99887766"))
    assert "aabbccd" in md
    assert "eeff998" in md


def test_markdown_contains_summary_section():
    md = render_markdown(make_report())
    assert "## Summary" in md


def test_markdown_empty_summary_shows_placeholder():
    report = make_report(ai_analysis=AIAnalysis())
    md = render_markdown(report)
    assert "_No summary available._" in md


def test_markdown_contains_blast_radius_section():
    md = render_markdown(make_report())
    assert "## Blast Radius" in md


# ---------------------------------------------------------------------------
# render_markdown: blast radius table
# ---------------------------------------------------------------------------


def test_blast_radius_table_present_when_populated():
    md = render_markdown(make_report())
    assert "| File |" in md


def test_blast_radius_table_absent_when_empty():
    md = render_markdown(make_report(blast_radius=[]))
    assert "| File |" not in md


def test_blast_radius_churn_none_shows_dash():
    entry = BlastRadiusEntry(path="x.py", distance=1, imported_symbols=[], churn_score=None)
    md = render_markdown(make_report(blast_radius=[entry]))
    # The churn column for this entry should show —
    lines = [row for row in md.splitlines() if "x.py" in row]
    assert any("—" in row for row in lines)


def test_blast_radius_churn_float_truncated_to_int():
    entry = BlastRadiusEntry(path="x.py", distance=1, imported_symbols=[], churn_score=7.9)
    md = render_markdown(make_report(blast_radius=[entry]))
    lines = [row for row in md.splitlines() if "x.py" in row]
    assert any("| 7 |" in row for row in lines)


def test_blast_radius_empty_symbols_shows_dash():
    entry = BlastRadiusEntry(path="x.py", distance=1, imported_symbols=[], churn_score=0.0)
    md = render_markdown(make_report(blast_radius=[entry]))
    lines = [row for row in md.splitlines() if "x.py" in row]
    assert any("| — |" in row for row in lines)


def test_blast_radius_symbols_joined_by_comma():
    entry = BlastRadiusEntry(
        path="x.py", distance=1, imported_symbols=["foo", "bar"], churn_score=0.0
    )
    md = render_markdown(make_report(blast_radius=[entry]))
    lines = [row for row in md.splitlines() if "x.py" in row]
    assert any("foo, bar" in row for row in lines)


def test_blast_radius_downstream_count_in_summary():
    entries = [
        BlastRadiusEntry(path="b.py", distance=1, imported_symbols=[], churn_score=None),
        BlastRadiusEntry(path="c.py", distance=2, imported_symbols=[], churn_score=None),
    ]
    md = render_markdown(make_report(blast_radius=entries))
    assert "2 file(s)" in md
    assert "2 dependency hop(s)" in md


def test_blast_radius_zero_downstream_shows_message():
    md = render_markdown(make_report(blast_radius=[]))
    assert "No downstream dependents found." in md


# ---------------------------------------------------------------------------
# render_markdown: interface changes
# ---------------------------------------------------------------------------


def test_interface_changes_section_present_when_populated():
    md = render_markdown(make_report())
    assert "## Interface Changes" in md


def test_interface_changes_section_absent_when_empty():
    md = render_markdown(make_report(interface_changes=[]))
    assert "## Interface Changes" not in md


def test_interface_change_before_and_after_shown():
    ic = InterfaceChange(
        file="a.py", symbol="foo", before="def foo()", after="def foo(x)", callers=[]
    )
    md = render_markdown(make_report(interface_changes=[ic]))
    assert "**Before:**" in md
    assert "**After:**" in md


def test_interface_change_empty_before_shows_new_label():
    ic = InterfaceChange(file="a.py", symbol="foo", before="", after="def foo(x)", callers=[])
    md = render_markdown(make_report(interface_changes=[ic]))
    assert "_(new)_" in md


def test_interface_change_empty_after_shows_removed_label():
    ic = InterfaceChange(file="a.py", symbol="foo", before="def foo()", after="", callers=[])
    md = render_markdown(make_report(interface_changes=[ic]))
    assert "_(removed)_" in md


def test_interface_change_callers_shown():
    ic = InterfaceChange(
        file="a.py", symbol="foo", before="def foo()", after="def foo(x)", callers=["b.py", "c.py"]
    )
    md = render_markdown(make_report(interface_changes=[ic]))
    assert "**Callers:**" in md
    assert "b.py" in md
    assert "c.py" in md


def test_interface_change_no_callers_line_absent():
    ic = InterfaceChange(
        file="a.py", symbol="foo", before="def foo()", after="def foo(x)", callers=[]
    )
    md = render_markdown(make_report(interface_changes=[ic]))
    assert "**Callers:**" not in md


# ---------------------------------------------------------------------------
# render_markdown: anomaly severity icons
# ---------------------------------------------------------------------------


def test_anomaly_high_severity_shows_red_icon():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="bad thing", location="a.py:1", severity="high")]
        )
    )
    assert "🔴" in render_markdown(report)


def test_anomaly_medium_severity_shows_yellow_icon():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="meh thing", location="a.py:2", severity="medium")]
        )
    )
    assert "🟡" in render_markdown(report)


def test_anomaly_low_severity_shows_blue_icon():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="minor thing", location="a.py:3", severity="low")]
        )
    )
    assert "🔵" in render_markdown(report)


def test_anomaly_unknown_severity_defaults_to_blue():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="??", location="a.py:4", severity="critical")]
        )
    )
    assert "🔵" in render_markdown(report)


def test_anomalies_section_absent_when_empty():
    report = make_report(ai_analysis=AIAnalysis())
    assert "## Anomalies" not in render_markdown(report)


# ---------------------------------------------------------------------------
# render_markdown: decisions, assumptions, test gaps
# ---------------------------------------------------------------------------


def test_decisions_section_present_with_decisions():
    report = make_report(
        ai_analysis=AIAnalysis(
            decisions=[Decision(description="chose X", rationale="faster", risk="memory")]
        )
    )
    assert "## Decisions and Assumptions" in render_markdown(report)


def test_assumptions_section_present_with_assumptions():
    report = make_report(
        ai_analysis=AIAnalysis(
            assumptions=[Assumption(description="not None", location="a.py:foo", risk="crash")]
        )
    )
    assert "## Decisions and Assumptions" in render_markdown(report)


def test_decisions_assumptions_section_absent_when_both_empty():
    report = make_report(ai_analysis=AIAnalysis())
    assert "## Decisions and Assumptions" not in render_markdown(report)


def test_test_gaps_section_present_when_populated():
    report = make_report(
        ai_analysis=AIAnalysis(test_gaps=[TestGap(behaviour="error path", location="a.py:foo")])
    )
    assert "## Test Gaps" in render_markdown(report)


def test_test_gaps_section_absent_when_empty():
    report = make_report(ai_analysis=AIAnalysis())
    assert "## Test Gaps" not in render_markdown(report)


def test_decision_content_shown():
    report = make_report(
        ai_analysis=AIAnalysis(
            decisions=[Decision(description="use caching", rationale="speed", risk="stale data")]
        )
    )
    md = render_markdown(report)
    assert "use caching" in md
    assert "speed" in md
    assert "stale data" in md


def test_test_gap_content_shown():
    report = make_report(
        ai_analysis=AIAnalysis(
            test_gaps=[TestGap(behaviour="login with empty password", location="auth.py:login")]
        )
    )
    md = render_markdown(report)
    assert "login with empty password" in md
    assert "auth.py:login" in md


# ---------------------------------------------------------------------------
# render_json
# ---------------------------------------------------------------------------


def test_render_json_is_valid_json():
    json.loads(render_json(make_report()))  # must not raise


def test_render_json_top_level_keys():
    result = json.loads(render_json(make_report()))
    assert set(result.keys()) >= {
        "pr_title",
        "base_sha",
        "head_sha",
        "changed_files",
        "blast_radius",
        "interface_changes",
        "ai_analysis",
    }


def test_render_json_pr_title_value():
    result = json.loads(render_json(make_report(pr_title="my PR")))
    assert result["pr_title"] == "my PR"


def test_render_json_churn_none_serialised_as_null():
    entry = BlastRadiusEntry(path="x.py", distance=1, imported_symbols=[], churn_score=None)
    result = json.loads(render_json(make_report(blast_radius=[entry])))
    assert result["blast_radius"][0]["churn_score"] is None


def test_render_json_empty_blast_radius():
    result = json.loads(render_json(make_report(blast_radius=[])))
    assert result["blast_radius"] == []


def test_render_json_nested_ai_analysis():
    report = make_report(ai_analysis=AIAnalysis(summary="hello world"))
    result = json.loads(render_json(report))
    assert result["ai_analysis"]["summary"] == "hello world"


def test_render_json_changed_files_nested():
    f = make_file(path="src/main.py")
    result = json.loads(render_json(make_report(changed_files=[f])))
    assert result["changed_files"][0]["path"] == "src/main.py"


def test_render_json_decisions_serialised():
    report = make_report(
        ai_analysis=AIAnalysis(decisions=[Decision(description="d", rationale="r", risk="rk")])
    )
    result = json.loads(render_json(report))
    assert result["ai_analysis"]["decisions"][0]["description"] == "d"
