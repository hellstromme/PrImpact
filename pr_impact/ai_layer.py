"""AI analysis orchestration for PrImpact.

Coordinates 3–5 Claude API calls per run. Context building is delegated to
ai_context.py; the raw API call is delegated to ai_client.py.
Called only by cli.py.
"""

import os
import sys

import anthropic

from .ai_client import call_api
from .ai_context import (
    build_blast_radius_signatures,
    build_changed_files_before_signatures,
    build_diffs_context,
    build_historical_context,
    build_security_signals_context,
    build_signatures_before_after,
    find_neighbouring_signatures,
    find_test_files,
)
from .models import (
    AIAnalysis,
    Anomaly,
    Assumption,
    BlastRadiusEntry,
    ChangedFile,
    Decision,
    DependencyIssue,
    SemanticVerdict,
    SecuritySignal,
    SourceLocation,
    TestGap,
    Verdict,
    VerdictBlocker,
)
from .prompts import (
    PROMPT_ANOMALY_DETECTION,
    PROMPT_SECURITY_SIGNALS,
    PROMPT_SEMANTIC_EQUIVALENCE,
    PROMPT_SUMMARY_DECISIONS_ASSUMPTIONS,
    PROMPT_TEST_GAP_ANALYSIS,
    PROMPT_VERDICT,
)


def _should_run_semantic_equivalence(changed_files: list[ChangedFile]) -> bool:
    """Return True if the semantic equivalence call is worth making."""
    if not changed_files:
        return False
    # Run when any file has >20 changed lines or has interface-level changes
    for f in changed_files:
        diff_lines = f.diff.count("\n")
        if diff_lines > 20:
            return True
        for sym in f.changed_symbols:
            if sym.change_type in ("interface_changed", "interface_added", "interface_removed"):
                return True
    return False


def run_ai_analysis(
    changed_files: list[ChangedFile],
    blast_radius: list[BlastRadiusEntry],
    repo_path: str,
    pattern_signals: list[SecuritySignal] | None = None,
    anomaly_history: list[dict] | None = None,
    hotspots: list[dict] | None = None,
) -> AIAnalysis:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set — AI analysis skipped. "
            "Set the key in the environment or in ~/.pr_impact/config.toml."
        )
    client = anthropic.Anthropic(api_key=api_key)
    result = AIAnalysis()

    diffs_ctx = build_diffs_context(changed_files)
    blast_sigs = build_blast_radius_signatures(blast_radius, repo_path)

    # Call 1: summary, decisions, assumptions
    data1 = call_api(
        client,
        PROMPT_SUMMARY_DECISIONS_ASSUMPTIONS.format(
            changed_files_diff=diffs_ctx,
            blast_radius_signatures=blast_sigs,
        ),
        "call1_summary",
    )
    result.summary = data1.get("summary", "")
    result.decisions = [
        Decision(
            description=d.get("description", ""),
            rationale=d.get("rationale", ""),
            risk=d.get("risk", ""),
        )
        for d in data1.get("decisions", [])
        if isinstance(d, dict)
    ]
    result.assumptions = [
        Assumption(
            description=a.get("description", ""),
            location=a.get("location", ""),
            risk=a.get("risk", ""),
        )
        for a in data1.get("assumptions", [])
        if isinstance(a, dict)
    ]

    # Call 2: anomaly detection (with optional historical context)
    before_sigs = build_changed_files_before_signatures(changed_files)
    try:
        neighbour_sigs = find_neighbouring_signatures(changed_files, repo_path)
    except Exception as exc:
        print(f"[pr-impact] Neighbour signature collection failed: {exc}", file=sys.stderr)
        neighbour_sigs = "(none)"

    historical_ctx = build_historical_context(anomaly_history, hotspots)
    anomaly_prompt = PROMPT_ANOMALY_DETECTION.format(
        changed_files_diff=diffs_ctx,
        changed_files_before_signatures=before_sigs,
        neighbouring_signatures=neighbour_sigs,
    )
    if historical_ctx:
        anomaly_prompt = anomaly_prompt + f"\n\n{historical_ctx}"

    data2 = call_api(client, anomaly_prompt, "call2_anomalies")
    result.anomalies = [
        Anomaly(
            description=a.get("description", ""),
            location=a.get("location", ""),
            severity=a.get("severity", "low"),
        )
        for a in data2.get("anomalies", [])
        if isinstance(a, dict)
    ]

    # Call 3: test gap analysis
    try:
        test_ctx = find_test_files(changed_files, repo_path)
    except Exception as exc:
        print(f"[pr-impact] Test file collection failed: {exc}", file=sys.stderr)
        test_ctx = "(no test files found)"
    data3 = call_api(
        client,
        PROMPT_TEST_GAP_ANALYSIS.format(
            changed_files_diff=diffs_ctx,
            test_files=test_ctx,
        ),
        "call3_test_gaps",
    )
    result.test_gaps = [
        TestGap(
            behaviour=t.get("behaviour", ""),
            location=t.get("location", ""),
        )
        for t in data3.get("test_gaps", [])
        if isinstance(t, dict)
    ]

    # Call 4: contextual security scoring (only when pattern signals exist)
    if pattern_signals:
        signals_text, file_ctx = build_security_signals_context(pattern_signals, changed_files)
        data4 = call_api(
            client,
            PROMPT_SECURITY_SIGNALS.format(
                changed_files_diff=diffs_ctx,
                pattern_signals=signals_text,
                file_context=file_ctx,
            ),
            "call4_security",
        )
        # data4 may be a list (the prompt returns an array) or a dict wrapping one
        if isinstance(data4, list):
            raw_signals = data4
        elif isinstance(data4, dict):
            raw_signals = data4.get("signals", data4.get("security_signals"))
        else:
            raw_signals = None
        if isinstance(raw_signals, list) and raw_signals:
            result.security_signals = [
                SecuritySignal(
                    description=s.get("description", ""),
                    location=SourceLocation(
                        file=s.get("file_path", ""),
                        line=s.get("line_number") if isinstance(s.get("line_number"), int) else None,
                        symbol=s.get("symbol") if isinstance(s.get("symbol"), str) else None,
                    ),
                    signal_type=s.get("signal_type", ""),
                    severity=s.get("severity", "low"),
                    why_unusual=s.get("why_unusual", ""),
                    suggested_action=s.get("suggested_action", ""),
                )
                for s in raw_signals
                if isinstance(s, dict)
            ]
        else:
            # AI call returned empty or unexpected shape — fall back to raw pattern signals
            result.security_signals = pattern_signals

    # Call 5: semantic equivalence detection (optional — only when diffs are substantial)
    if _should_run_semantic_equivalence(changed_files):
        sigs_before_after = build_signatures_before_after(changed_files)
        data5 = call_api(
            client,
            PROMPT_SEMANTIC_EQUIVALENCE.format(
                changed_files_diff=diffs_ctx,
                signatures_before_after=sigs_before_after,
            ),
            "call5_semantic",
        )
        # Response may be a list or a dict wrapping a list
        if isinstance(data5, list):
            raw_verdicts = data5
        elif isinstance(data5, dict):
            raw_verdicts = data5.get("verdicts", data5.get("results", []))
        else:
            raw_verdicts = []
        if isinstance(raw_verdicts, list):
            result.semantic_verdicts = [
                SemanticVerdict(
                    file=v.get("file", ""),
                    symbol=v.get("symbol", ""),
                    verdict=v.get("verdict", "normal"),
                    reason=v.get("reason", ""),
                )
                for v in raw_verdicts
                if isinstance(v, dict) and v.get("verdict") in ("equivalent", "risky")
            ]

    return result


def run_verdict_analysis(
    ai_analysis: AIAnalysis,
    dependency_issues: list[DependencyIssue],
) -> Verdict:
    """Run the verdict API call given a completed AI analysis and dependency issues.

    Accepts the minimal fields the verdict prompt needs rather than the full ImpactReport,
    keeping this function consistent with the rest of the AI layer's input pattern.

    Raises ValueError when the API key is missing or the response cannot be parsed
    as a verdict dict. The caller (cli.py) is responsible for graceful degradation.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set — verdict analysis skipped."
        )
    client = anthropic.Anthropic(api_key=api_key)

    def _fmt_anomalies() -> str:
        if not ai_analysis.anomalies:
            return "(none)"
        return "\n".join(
            f"- [{a.severity.upper()}] {a.description} ({a.location})"
            for a in ai_analysis.anomalies
        )

    def _fmt_test_gaps() -> str:
        if not ai_analysis.test_gaps:
            return "(none)"
        return "\n".join(
            f"- {t.behaviour} ({t.location})" for t in ai_analysis.test_gaps
        )

    def _fmt_security_signals() -> str:
        if not ai_analysis.security_signals:
            return "(none)"
        return "\n".join(
            f"- [{s.severity.upper()}] {s.signal_type}: {s.description} ({s.location.file})"
            for s in ai_analysis.security_signals
        )

    def _fmt_dependency_issues() -> str:
        if not dependency_issues:
            return "(none)"
        return "\n".join(
            f"- [{i.severity.upper()}] {i.issue_type}: {i.description}"
            for i in dependency_issues
        )

    prompt = PROMPT_VERDICT.format(
        summary=ai_analysis.summary or "(no summary)",
        anomaly_count=len(ai_analysis.anomalies),
        anomalies=_fmt_anomalies(),
        test_gap_count=len(ai_analysis.test_gaps),
        test_gaps=_fmt_test_gaps(),
        security_signal_count=len(ai_analysis.security_signals),
        security_signals=_fmt_security_signals(),
        dependency_issue_count=len(dependency_issues),
        dependency_issues=_fmt_dependency_issues(),
    )

    data = call_api(client, prompt, "call_verdict")

    if not isinstance(data, dict):
        raise ValueError(f"Verdict response was not a JSON object (got {type(data).__name__})")

    blockers = [
        VerdictBlocker(
            category=b.get("category", ""),
            description=b.get("description", ""),
            location=b.get("location", ""),
        )
        for b in data.get("blockers", [])
        if isinstance(b, dict)
    ]
    raw_continue = data.get("agent_should_continue", False)
    if isinstance(raw_continue, str):
        should_continue = raw_continue.strip().lower() in ("true", "1")
    else:
        should_continue = raw_continue is True or raw_continue == 1
    return Verdict(
        status=data.get("status", "clean"),
        agent_should_continue=should_continue,
        rationale=data.get("rationale", ""),
        blockers=blockers,
    )
