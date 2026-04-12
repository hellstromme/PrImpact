"""Unit tests for pr_impact/reporter.py."""

import importlib.metadata
import io
import json
from unittest.mock import patch

from rich.console import Console

from pr_impact.models import (
    AIAnalysis,
    Anomaly,
    Assumption,
    BlastRadiusEntry,
    Decision,
    DependencyIssue,
    InterfaceChange,
    SecuritySignal,
    SourceLocation,
    TestGap,
    Verdict,
    VerdictBlocker,
)
from pr_impact.reporter import _fmt_churn, _parse_location, _sev, _sev_color, render_json, render_markdown, render_sarif, render_terminal, render_verdict_terminal
from tests.helpers import make_file, make_report


def _capture_terminal(report, **kwargs) -> str:
    """Render to a StringIO console and return the plain-text output."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True)
    render_terminal(report, console, **kwargs)
    return buf.getvalue()

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


# ---------------------------------------------------------------------------
# render_markdown: anomaly description and location content
# ---------------------------------------------------------------------------


def test_anomaly_description_shown_in_markdown():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="Direct DB call in handler", location="api.py:42", severity="high")]
        )
    )
    md = render_markdown(report)
    assert "Direct DB call in handler" in md
    assert "api.py:42" in md


def test_anomaly_section_header_present():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="x", location="y", severity="low")]
        )
    )
    assert "## Anomalies" in render_markdown(report)


# ---------------------------------------------------------------------------
# render_markdown: test gaps location content
# ---------------------------------------------------------------------------


def test_test_gap_location_shown_in_markdown():
    report = make_report(
        ai_analysis=AIAnalysis(
            test_gaps=[TestGap(behaviour="logout with expired token", location="auth.py:logout")]
        )
    )
    md = render_markdown(report)
    assert "logout with expired token" in md
    assert "auth.py:logout" in md


# ---------------------------------------------------------------------------
# render_json: anomalies, assumptions, test_gaps round-trip
# ---------------------------------------------------------------------------


def test_render_json_anomalies_serialised():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="bad", location="x.py:1", severity="medium")]
        )
    )
    result = json.loads(render_json(report))
    anomaly = result["ai_analysis"]["anomalies"][0]
    assert anomaly["description"] == "bad"
    assert anomaly["location"] == "x.py:1"
    assert anomaly["severity"] == "medium"


def test_render_json_assumptions_serialised():
    report = make_report(
        ai_analysis=AIAnalysis(
            assumptions=[Assumption(description="not None", location="a.py:foo", risk="crash")]
        )
    )
    result = json.loads(render_json(report))
    assumption = result["ai_analysis"]["assumptions"][0]
    assert assumption["description"] == "not None"
    assert assumption["location"] == "a.py:foo"
    assert assumption["risk"] == "crash"


def test_render_json_test_gaps_serialised():
    report = make_report(
        ai_analysis=AIAnalysis(
            test_gaps=[TestGap(behaviour="error path not tested", location="b.py:bar")]
        )
    )
    result = json.loads(render_json(report))
    gap = result["ai_analysis"]["test_gaps"][0]
    assert gap["behaviour"] == "error path not tested"
    assert gap["location"] == "b.py:bar"


def test_render_json_empty_ai_analysis_sections():
    result = json.loads(render_json(make_report(ai_analysis=AIAnalysis())))
    ai = result["ai_analysis"]
    assert ai["decisions"] == []
    assert ai["assumptions"] == []
    assert ai["anomalies"] == []
    assert ai["test_gaps"] == []


# ---------------------------------------------------------------------------
# render_terminal: header and summary
# ---------------------------------------------------------------------------


def test_terminal_contains_pr_title():
    out = _capture_terminal(make_report(pr_title="fix: null pointer"))
    assert "fix: null pointer" in out


def test_terminal_contains_sha_range():
    out = _capture_terminal(make_report(base_sha="aabbccdd1122", head_sha="eeff99887766"))
    assert "aabbccd" in out
    assert "eeff998" in out


def test_terminal_summary_shown():
    report = make_report(ai_analysis=AIAnalysis(summary="Everything looks fine."))
    assert "Everything looks fine." in _capture_terminal(report)


def test_terminal_no_summary_shows_placeholder():
    report = make_report(ai_analysis=AIAnalysis())
    assert "No summary available." in _capture_terminal(report)


# ---------------------------------------------------------------------------
# render_terminal: blast radius table
# ---------------------------------------------------------------------------


def test_terminal_blast_radius_file_shown():
    entry = BlastRadiusEntry(path="consumer.py", distance=1, imported_symbols=["foo"], churn_score=5.0)
    out = _capture_terminal(make_report(blast_radius=[entry]))
    assert "consumer.py" in out


def test_terminal_blast_radius_empty_no_table():
    out = _capture_terminal(make_report(blast_radius=[]))
    assert "0 downstream" in out


def test_terminal_churn_none_shows_dash():
    entry = BlastRadiusEntry(path="x.py", distance=1, imported_symbols=[], churn_score=None)
    out = _capture_terminal(make_report(blast_radius=[entry]))
    assert "—" in out


# ---------------------------------------------------------------------------
# render_terminal: interface changes
# ---------------------------------------------------------------------------


def test_terminal_interface_changes_section_shown():
    ic = InterfaceChange(file="a.py", symbol="login", before="def login()", after="def login(user)", callers=[])
    out = _capture_terminal(make_report(interface_changes=[ic]))
    assert "login" in out
    assert "INTERFACE CHANGES" in out


def test_terminal_interface_changes_absent_when_empty():
    out = _capture_terminal(make_report(interface_changes=[]))
    assert "INTERFACE CHANGES" not in out


def test_terminal_interface_change_callers_shown():
    ic = InterfaceChange(file="a.py", symbol="foo", before="def foo()", after="def foo(x)", callers=["b.py"])
    out = _capture_terminal(make_report(interface_changes=[ic]))
    assert "b.py" in out


# ---------------------------------------------------------------------------
# render_terminal: decisions and assumptions
# ---------------------------------------------------------------------------


def test_terminal_decisions_section_shown():
    report = make_report(
        ai_analysis=AIAnalysis(decisions=[Decision(description="use cache", rationale="speed", risk="stale")])
    )
    out = _capture_terminal(report)
    assert "DECISIONS" in out
    assert "use cache" in out


def test_terminal_assumptions_section_shown():
    report = make_report(
        ai_analysis=AIAnalysis(assumptions=[Assumption(description="user is valid", location="auth.py:10", risk="crash")])
    )
    out = _capture_terminal(report)
    assert "user is valid" in out


def test_terminal_decisions_assumptions_absent_when_empty():
    out = _capture_terminal(make_report(ai_analysis=AIAnalysis()))
    assert "DECISIONS" not in out


# ---------------------------------------------------------------------------
# render_terminal: anomalies
# ---------------------------------------------------------------------------


def test_terminal_anomalies_section_shown():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="Direct DB call", location="a.py:5", severity="high")])
    )
    out = _capture_terminal(report)
    assert "ANOMALIES" in out
    assert "Direct DB call" in out


def test_terminal_anomaly_counts_shown():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[
            Anomaly(description="x", location="a.py", severity="high"),
            Anomaly(description="y", location="b.py", severity="medium"),
            Anomaly(description="z", location="c.py", severity="low"),
        ])
    )
    out = _capture_terminal(report)
    assert "1 high" in out
    assert "1 medium" in out
    assert "1 low" in out


def test_terminal_anomalies_absent_when_empty():
    out = _capture_terminal(make_report(ai_analysis=AIAnalysis()))
    assert "ANOMALIES" not in out


# ---------------------------------------------------------------------------
# render_terminal: test gaps
# ---------------------------------------------------------------------------


def test_terminal_test_gaps_section_shown():
    report = make_report(
        ai_analysis=AIAnalysis(test_gaps=[TestGap(behaviour="empty password login", location="auth.py:login")])
    )
    out = _capture_terminal(report)
    assert "TEST GAPS" in out
    assert "empty password login" in out


def test_terminal_test_gaps_absent_when_empty():
    out = _capture_terminal(make_report(ai_analysis=AIAnalysis()))
    assert "TEST GAPS" not in out


# ---------------------------------------------------------------------------
# render_terminal: footer with output paths
# ---------------------------------------------------------------------------


def test_terminal_footer_shown_with_output_path():
    out = _capture_terminal(make_report(), output="report.md")
    assert "report.md" in out


def test_terminal_footer_shown_with_json_path():
    out = _capture_terminal(make_report(), json_output="report.json")
    assert "report.json" in out


def test_terminal_no_footer_when_no_outputs():
    out = _capture_terminal(make_report())
    assert "written to" not in out


def test_terminal_blast_radius_distance_3_and_other():
    """Cover the elif distance==3 (dim) and else (no style) row branches."""
    entries = [
        BlastRadiusEntry(path="dist1.py", distance=1, imported_symbols=[], churn_score=None),
        BlastRadiusEntry(path="dist2.py", distance=2, imported_symbols=[], churn_score=None),
        BlastRadiusEntry(path="dist3.py", distance=3, imported_symbols=[], churn_score=None),
    ]
    out = _capture_terminal(make_report(blast_radius=entries))
    assert "dist1.py" in out
    assert "dist2.py" in out
    assert "dist3.py" in out


# ---------------------------------------------------------------------------
# _sev, _sev_color, _fmt_churn — direct unit tests
# ---------------------------------------------------------------------------


def test_sev_returns_default_style_for_unknown_key():
    style = _sev("critical")
    assert style.icon == "🔵"
    assert style.color == "bright_blue"


def test_sev_color_returns_dim_for_unknown_key():
    assert _sev_color("critical") == "dim"


def test_sev_color_returns_dim_for_empty_string():
    assert _sev_color("") == "dim"


def test_sev_color_returns_correct_colors_for_known_keys():
    assert _sev_color("high") == "bright_red"
    assert _sev_color("medium") == "yellow"
    assert _sev_color("low") == "bright_blue"


def test_fmt_churn_returns_dash_for_none():
    assert _fmt_churn(None) == "—"


def test_fmt_churn_returns_int_string_for_float():
    assert _fmt_churn(7.9) == "7"
    assert _fmt_churn(0.0) == "0"


def test_terminal_decision_risk_unrecognised_does_not_raise():
    """render_terminal with a decision risk not in _SEVERITY falls back to 'dim' safely."""
    report = make_report(
        ai_analysis=AIAnalysis(
            decisions=[Decision(description="do X", rationale="faster", risk="Catastrophic")]
        )
    )
    out = _capture_terminal(report)
    assert "Catastrophic" in out


# ---------------------------------------------------------------------------
# render_sarif
# ---------------------------------------------------------------------------

def test_sarif_is_valid_json():
    sarif = render_sarif(make_report())
    parsed = json.loads(sarif)
    assert "$schema" in parsed
    assert parsed["version"] == "2.1.0"


def test_sarif_has_single_run():
    parsed = json.loads(render_sarif(make_report()))
    assert len(parsed["runs"]) == 1


def test_sarif_tool_driver_name():
    parsed = json.loads(render_sarif(make_report()))
    assert parsed["runs"][0]["tool"]["driver"]["name"] == "primpact"


def test_sarif_empty_results_when_no_anomalies_or_gaps():
    report = make_report(ai_analysis=AIAnalysis())
    parsed = json.loads(render_sarif(report))
    assert parsed["runs"][0]["results"] == []


def test_sarif_high_anomaly_maps_to_error():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="bad", location="foo.py", severity="high")])
    )
    parsed = json.loads(render_sarif(report))
    result = parsed["runs"][0]["results"][0]
    assert result["level"] == "error"
    assert result["ruleId"] == "primpact/anomaly"


def test_sarif_medium_anomaly_maps_to_warning():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="suspicious", location="bar.py", severity="medium")])
    )
    parsed = json.loads(render_sarif(report))
    assert parsed["runs"][0]["results"][0]["level"] == "warning"


def test_sarif_low_anomaly_maps_to_note():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="minor", location="baz.py", severity="low")])
    )
    parsed = json.loads(render_sarif(report))
    assert parsed["runs"][0]["results"][0]["level"] == "note"


def test_sarif_test_gap_level_maps_from_severity():
    report = make_report(
        ai_analysis=AIAnalysis(test_gaps=[TestGap(behaviour="missing branch", location="src/thing.py", severity="low")])
    )
    parsed = json.loads(render_sarif(report))
    result = parsed["runs"][0]["results"][0]
    assert result["level"] == "note"
    assert result["ruleId"] == "primpact/test-gap"

    report_high = make_report(
        ai_analysis=AIAnalysis(test_gaps=[TestGap(behaviour="critical path untested", location="src/thing.py", severity="high")])
    )
    parsed_high = json.loads(render_sarif(report_high))
    assert parsed_high["runs"][0]["results"][0]["level"] == "error"


def test_sarif_anomaly_location_in_physical_location():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="x", location="src/auth.py", severity="high")])
    )
    parsed = json.loads(render_sarif(report))
    uri = parsed["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "src/auth.py"


def test_sarif_anomaly_location_colon_line():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="x", location="src/auth.py:12", severity="high")])
    )
    parsed = json.loads(render_sarif(report))
    loc = parsed["runs"][0]["results"][0]["locations"][0]
    assert loc["physicalLocation"]["artifactLocation"]["uri"] == "src/auth.py"
    assert loc["physicalLocation"]["region"]["startLine"] == 12


def test_sarif_anomaly_location_colon_symbol():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="x", location="src/auth.py:my_func", severity="high")])
    )
    parsed = json.loads(render_sarif(report))
    loc = parsed["runs"][0]["results"][0]["locations"][0]
    assert loc["physicalLocation"]["artifactLocation"]["uri"] == "src/auth.py"
    assert loc["logicalLocations"][0]["name"] == "my_func"


def test_sarif_multiple_anomalies_all_present():
    anomalies = [
        Anomaly(description="a1", location="f1.py", severity="high"),
        Anomaly(description="a2", location="f2.py", severity="low"),
    ]
    report = make_report(ai_analysis=AIAnalysis(anomalies=anomalies))
    parsed = json.loads(render_sarif(report))
    assert len(parsed["runs"][0]["results"]) == 2


def test_sarif_anomaly_and_gap_both_present():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="x", location="a.py", severity="high")],
            test_gaps=[TestGap(behaviour="y", location="b.py")],
        )
    )
    parsed = json.loads(render_sarif(report))
    results = parsed["runs"][0]["results"]
    assert len(results) == 2
    rule_ids = {r["ruleId"] for r in results}
    assert rule_ids == {"primpact/anomaly", "primpact/test-gap"}


def test_sarif_rules_array_contains_both_rules():
    report = make_report(
        ai_analysis=AIAnalysis(
            anomalies=[Anomaly(description="x", location="a.py", severity="high")],
            test_gaps=[TestGap(behaviour="y", location="b.py")],
        )
    )
    parsed = json.loads(render_sarif(report))
    rule_ids = {r["id"] for r in parsed["runs"][0]["tool"]["driver"]["rules"]}
    assert rule_ids == {"primpact/anomaly", "primpact/test-gap"}


def test_sarif_unrecognised_severity_maps_to_note():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="x", location="a.py", severity="critical")])
    )
    parsed = json.loads(render_sarif(report))
    assert parsed["runs"][0]["results"][0]["level"] == "note"


def test_sarif_no_locations_when_parse_location_returns_none():
    report = make_report(
        ai_analysis=AIAnalysis(anomalies=[Anomaly(description="x", location="no file here", severity="low")])
    )
    parsed = json.loads(render_sarif(report))
    assert "locations" not in parsed["runs"][0]["results"][0]


def test_sarif_version_fallback_when_package_not_found():
    with patch("pr_impact.reporter.importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
        parsed = json.loads(render_sarif(make_report()))
    assert parsed["runs"][0]["tool"]["driver"]["version"] == "0.0.0"


# ---------------------------------------------------------------------------
# _parse_location — unit tests
# ---------------------------------------------------------------------------


def test_parse_location_returns_none_for_plain_text():
    assert _parse_location("no file here") is None


def test_parse_location_returns_none_for_path_without_extension():
    # colon present but path part has no dot-extension
    assert _parse_location("nodotpath:12") is None


# ---------------------------------------------------------------------------
# render_terminal — SARIF footer
# ---------------------------------------------------------------------------


def test_terminal_footer_shows_sarif_path():
    out = _capture_terminal(make_report(), sarif_output="report.sarif")
    assert "report.sarif" in out


# ---------------------------------------------------------------------------
# Security Signals — render_markdown
# ---------------------------------------------------------------------------


def _make_signal(severity: str = "high") -> SecuritySignal:
    return SecuritySignal(
        description="New network call in auth module",
        location=SourceLocation(file="src/auth/session.py", line=47),
        signal_type="network_call",
        severity=severity,
        why_unusual="No prior network access in this module.",
        suggested_action="Confirm with PR author.",
    )


def _make_dep_issue(issue_type: str = "typosquat") -> DependencyIssue:
    return DependencyIssue(
        package_name="requets",
        issue_type=issue_type,
        description="`requets` is very similar to `requests`.",
        severity="high",
    )


def test_markdown_security_signals_section_present_when_signals_exist():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    md = render_markdown(report)
    assert "## Security Signals" in md


def test_markdown_security_signals_section_absent_when_empty():
    report = make_report(ai_analysis=AIAnalysis())
    md = render_markdown(report)
    assert "## Security Signals" not in md


def test_markdown_security_signal_high_icon():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal("high")]))
    md = render_markdown(report)
    assert "🔴" in md
    assert "HIGH" in md


def test_markdown_security_signal_medium_icon():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal("medium")]))
    md = render_markdown(report)
    assert "🟡" in md


def test_markdown_security_signal_low_icon():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal("low")]))
    md = render_markdown(report)
    assert "🔵" in md


def test_markdown_security_signal_includes_file_path():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    md = render_markdown(report)
    assert "src/auth/session.py" in md


def test_markdown_security_signal_includes_line_number():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    md = render_markdown(report)
    assert "line 47" in md


def test_markdown_security_signal_no_line_number_when_none():
    sig = _make_signal()
    sig.location = SourceLocation(file=sig.location.file)
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    md = render_markdown(report)
    assert "line None" not in md


def test_markdown_security_disclaimer_present():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    md = render_markdown(report)
    assert "not a security audit" in md


def test_markdown_dependency_issues_present():
    report = make_report(dependency_issues=[_make_dep_issue()])
    md = render_markdown(report)
    assert "## Security Signals" in md
    assert "Dependency Issues" in md
    assert "requets" in md


def test_markdown_dependency_issues_absent_when_empty():
    report = make_report()
    md = render_markdown(report)
    assert "Dependency Issues" not in md


# ---------------------------------------------------------------------------
# Security Signals — render_sarif
# ---------------------------------------------------------------------------


def test_sarif_security_signal_rule_present():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    sarif = json.loads(render_sarif(report))
    rule_ids = [r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]]
    assert "primpact/security-signal" in rule_ids


def test_sarif_security_signal_result_level_error_for_high():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal("high")]))
    sarif = json.loads(render_sarif(report))
    results = sarif["runs"][0]["results"]
    sec_results = [r for r in results if r["ruleId"] == "primpact/security-signal"]
    assert sec_results
    assert sec_results[0]["level"] == "error"


def test_sarif_security_signal_no_rule_when_no_signals():
    report = make_report(ai_analysis=AIAnalysis())
    sarif = json.loads(render_sarif(report))
    rule_ids = [r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]]
    assert "primpact/security-signal" not in rule_ids


def test_sarif_dependency_issue_rule_present():
    report = make_report(dependency_issues=[_make_dep_issue()])
    sarif = json.loads(render_sarif(report))
    rule_ids = [r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]]
    assert "primpact/dependency-issue" in rule_ids


# ---------------------------------------------------------------------------
# Security Signals — render_terminal
# ---------------------------------------------------------------------------


def test_terminal_security_signals_section_present():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    out = _capture_terminal(report)
    assert "SECURITY SIGNALS" in out


def test_terminal_security_signals_absent_when_empty():
    report = make_report(ai_analysis=AIAnalysis())
    out = _capture_terminal(report)
    assert "SECURITY SIGNALS" not in out


def test_terminal_security_signals_shows_file_path():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    out = _capture_terminal(report)
    assert "src/auth/session.py" in out


def test_terminal_dependency_issues_shows_package_name():
    report = make_report(dependency_issues=[_make_dep_issue()])
    out = _capture_terminal(report)
    assert "SECURITY SIGNALS" in out
    assert "requets" in out


# ---------------------------------------------------------------------------
# Security Signals — render_json
# ---------------------------------------------------------------------------


def test_json_includes_security_signals():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    data = json.loads(render_json(report))
    signals = data["ai_analysis"]["security_signals"]
    assert len(signals) == 1
    assert signals[0]["signal_type"] == "network_call"


def test_json_includes_dependency_issues():
    report = make_report(dependency_issues=[_make_dep_issue()])
    data = json.loads(render_json(report))
    issues = data["dependency_issues"]
    assert len(issues) == 1
    assert issues[0]["package_name"] == "requets"


# ---------------------------------------------------------------------------
# Security Signals — SARIF location detail
# ---------------------------------------------------------------------------


def test_sarif_security_signal_includes_location_for_file_path():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    assert results
    assert "locations" in results[0]
    assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/auth/session.py"


def test_sarif_security_signal_includes_line_number_when_present():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    region = results[0]["locations"][0]["physicalLocation"].get("region", {})
    assert region.get("startLine") == 47


def test_sarif_security_signal_no_location_when_empty_file_path():
    sig = _make_signal()
    sig.location = SourceLocation(file="")
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    assert results
    assert "locations" not in results[0]


def test_sarif_security_signal_medium_maps_to_warning():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal("medium")]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    assert results[0]["level"] == "warning"


def test_sarif_security_signal_low_maps_to_note():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal("low")]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    assert results[0]["level"] == "note"


def test_sarif_dependency_issue_high_maps_to_error():
    report = make_report(dependency_issues=[_make_dep_issue()])
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/dependency-issue"]
    assert results
    assert results[0]["level"] == "error"


# ---------------------------------------------------------------------------
# Security Signals — render_terminal detail
# ---------------------------------------------------------------------------


def test_terminal_security_signals_shows_severity_counts():
    signals = [
        _make_signal("high"),
        _make_signal("medium"),
        _make_signal("low"),
    ]
    report = make_report(ai_analysis=AIAnalysis(security_signals=signals))
    out = _capture_terminal(report)
    assert "1 high" in out
    assert "1 medium" in out
    assert "1 low" in out


def test_terminal_security_signals_shows_disclaimer():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    out = _capture_terminal(report)
    assert "Not a security audit" in out or "not a security audit" in out.lower()


def test_terminal_security_signals_shows_line_number():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    out = _capture_terminal(report)
    assert ":47" in out


def test_terminal_security_signals_absent_when_both_empty():
    report = make_report(ai_analysis=AIAnalysis(), dependency_issues=[])
    out = _capture_terminal(report)
    assert "SECURITY SIGNALS" not in out


def test_terminal_security_signals_both_signals_and_dep_issues():
    """When both security_signals and dependency_issues are present, both appear in terminal."""
    report = make_report(
        ai_analysis=AIAnalysis(security_signals=[_make_signal()]),
        dependency_issues=[_make_dep_issue()],
    )
    out = _capture_terminal(report)
    assert "SECURITY SIGNALS" in out
    assert "src/auth/session.py" in out   # from signal
    assert "requets" in out               # from dep issue


def test_sarif_security_signal_no_region_when_line_number_is_none():
    """When line_number is None, SARIF location omits 'region' key."""
    sig = _make_signal()
    sig.location = SourceLocation(file=sig.location.file)
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    assert results
    physical = results[0]["locations"][0]["physicalLocation"]
    assert "region" not in physical


def test_terminal_security_signal_no_colon_none_when_line_number_is_none():
    """When line_number is None, ':None' must not appear in terminal output."""
    sig = _make_signal()
    sig.location = SourceLocation(file=sig.location.file)
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    out = _capture_terminal(report)
    assert ":None" not in out


# ---------------------------------------------------------------------------
# SourceLocation — field validation and defaults
# ---------------------------------------------------------------------------


def test_source_location_defaults_line_and_symbol_to_none():
    loc = SourceLocation(file="src/foo.py")
    assert loc.line is None
    assert loc.symbol is None


def test_source_location_accepts_empty_file_string():
    loc = SourceLocation(file="")
    assert loc.file == ""


def test_source_location_all_fields_populated():
    loc = SourceLocation(file="src/auth.py", line=42, symbol="login")
    assert loc.file == "src/auth.py"
    assert loc.line == 42
    assert loc.symbol == "login"


# ---------------------------------------------------------------------------
# SourceLocation.symbol — reporter output
# ---------------------------------------------------------------------------


def _make_signal_with_symbol() -> SecuritySignal:
    return SecuritySignal(
        description="New dynamic exec",
        location=SourceLocation(file="src/runner.py", line=12, symbol="execute_command"),
        signal_type="dynamic_exec",
        severity="high",
        why_unusual="No prior dynamic execution.",
        suggested_action="Confirm with PR author.",
    )


def test_markdown_security_signal_includes_symbol():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal_with_symbol()]))
    md = render_markdown(report)
    assert "execute_command" in md


def test_terminal_security_signal_includes_symbol():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal_with_symbol()]))
    out = _capture_terminal(report)
    assert "execute_command" in out


def test_sarif_security_signal_includes_logical_location_when_symbol_present():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal_with_symbol()]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    assert results
    loc = results[0]["locations"][0]
    assert "logicalLocations" in loc
    assert loc["logicalLocations"][0]["name"] == "execute_command"


def test_sarif_security_signal_no_logical_location_when_symbol_absent():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    assert results
    loc = results[0]["locations"][0]
    assert "logicalLocations" not in loc


def test_json_serialization_of_source_location_with_all_fields():
    import dataclasses
    sig = _make_signal_with_symbol()
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    data = json.loads(render_json(report))
    signals = data["ai_analysis"]["security_signals"]
    assert signals
    loc = signals[0]["location"]
    assert loc["file"] == "src/runner.py"
    assert loc["line"] == 12
    assert loc["symbol"] == "execute_command"


def test_json_serialization_of_source_location_defaults():
    sig = _make_signal()
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    data = json.loads(render_json(report))
    loc = data["ai_analysis"]["security_signals"][0]["location"]
    assert loc["file"] == "src/auth/session.py"
    assert loc["line"] == 47
    assert loc["symbol"] is None


# ---------------------------------------------------------------------------
# render_verdict_terminal
# ---------------------------------------------------------------------------


def _capture_verdict(verdict: Verdict) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True)
    render_verdict_terminal(verdict, console)
    return buf.getvalue()


def _make_verdict(**kwargs) -> Verdict:
    defaults = dict(
        status="clean",
        agent_should_continue=False,
        rationale="No blockers found.",
        blockers=[],
    )
    defaults.update(kwargs)
    return Verdict(**defaults)


def test_verdict_clean_shows_stop_message():
    out = _capture_verdict(_make_verdict())
    assert "CLEAN" in out or "agent may stop" in out.lower()


def test_verdict_has_blockers_shows_continue_message():
    out = _capture_verdict(_make_verdict(
        status="has_blockers",
        agent_should_continue=True,
        rationale="Test coverage missing.",
        blockers=[VerdictBlocker(category="test_gap", description="login untested", location="src/auth.py")],
    ))
    assert "BLOCKER" in out or "continue" in out.lower()


def test_verdict_rationale_included():
    out = _capture_verdict(_make_verdict(rationale="All signals are low severity."))
    assert "All signals are low severity." in out


def test_verdict_blockers_listed():
    out = _capture_verdict(_make_verdict(
        status="has_blockers",
        agent_should_continue=True,
        blockers=[VerdictBlocker(category="anomaly", description="Unusual coupling", location="src/db.py:42")],
    ))
    assert "Unusual coupling" in out
    assert "src/db.py:42" in out


def test_verdict_blocker_location_omitted_when_empty():
    out = _capture_verdict(_make_verdict(
        status="has_blockers",
        agent_should_continue=True,
        blockers=[VerdictBlocker(category="test_gap", description="missing test", location="")],
    ))
    # Empty location → no colon-delimited empty line
    assert ":  " not in out


def test_verdict_clean_no_blockers_section_when_empty():
    out = _capture_verdict(_make_verdict())
    # No blocker category labels when blockers list is empty
    assert "test_gap" not in out
    assert "anomaly" not in out


def test_verdict_agent_verdict_rule_present():
    out = _capture_verdict(_make_verdict())
    assert "AGENT VERDICT" in out


def test_markdown_security_signal_omits_why_unusual_when_empty():
    sig = _make_signal()
    sig.why_unusual = ""
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    md = render_markdown(report)
    assert "Why this is unusual" not in md


def test_markdown_security_signal_omits_suggested_action_when_empty():
    sig = _make_signal()
    sig.suggested_action = ""
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    md = render_markdown(report)
    assert "Suggested action" not in md


def test_sarif_security_signal_message_includes_why_unusual():
    sig = _make_signal()
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    sarif = json.loads(render_sarif(report))
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "primpact/security-signal"]
    assert results
    assert "No prior network access" in results[0]["message"]["text"]


def test_terminal_security_signal_shows_why_unusual():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    out = _capture_terminal(report)
    assert "No prior network access" in out


def test_terminal_security_signal_shows_suggested_action():
    report = make_report(ai_analysis=AIAnalysis(security_signals=[_make_signal()]))
    out = _capture_terminal(report)
    assert "Confirm with PR author" in out


def test_terminal_security_signal_omits_suggested_action_when_empty():
    sig = _make_signal()
    sig.suggested_action = ""
    report = make_report(ai_analysis=AIAnalysis(security_signals=[sig]))
    out = _capture_terminal(report)
    assert "→" not in out


def test_verdict_banner_red_when_status_has_blockers_even_if_flag_false():
    """status='has_blockers' with agent_should_continue=False still shows red."""
    v = _make_verdict(status="has_blockers", agent_should_continue=False,
                      blockers=[VerdictBlocker(category="anomaly", description="x", location="")])
    out = _capture_verdict(v)
    assert "BLOCKER" in out or "continue" in out.lower()


def test_verdict_banner_red_when_blockers_present_even_if_flag_false():
    """Non-empty blockers list shows red banner regardless of agent_should_continue."""
    v = _make_verdict(status="clean", agent_should_continue=False,
                      blockers=[VerdictBlocker(category="test_gap", description="y", location="")])
    out = _capture_verdict(v)
    assert "BLOCKER" in out or "continue" in out.lower()
