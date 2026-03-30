import dataclasses
import json

from .models import ImpactReport

_SEVERITY_ICON = {"high": "🔴", "medium": "🟡", "low": "🔵"}


def render_markdown(report: ImpactReport) -> str:
    lines: list[str] = []

    # Header
    lines.append("# PR Impact Report")
    lines.append(f"{report.pr_title} · {report.base_sha[:7]}..{report.head_sha[:7]}")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append(report.ai_analysis.summary or "_No summary available._")
    lines.append("")

    # Blast Radius
    lines.append("## Blast Radius")
    direct = len(report.changed_files)
    downstream = len(report.blast_radius)
    if downstream:
        max_dist = max(e.distance for e in report.blast_radius)
        lines.append(
            f"Direct changes: {direct} file(s)  "
            f"Downstream risk: {downstream} file(s) across {max_dist} dependency hop(s)"
        )
    else:
        lines.append(f"Direct changes: {direct} file(s)  No downstream dependents found.")
    lines.append("")

    if report.blast_radius:
        lines.append("| File | Distance | Uses | Churn (90d) |")
        lines.append("|------|----------|------|-------------|")
        for entry in report.blast_radius:
            uses = ", ".join(entry.imported_symbols) if entry.imported_symbols else "—"
            churn = str(int(entry.churn_score)) if entry.churn_score is not None else "—"
            lines.append(f"| `{entry.path}` | {entry.distance} | {uses} | {churn} |")
        lines.append("")

    # Interface Changes
    if report.interface_changes:
        lines.append("## Interface Changes")
        for ic in report.interface_changes:
            lines.append(f"### `{ic.symbol}` in `{ic.file}`")
            lines.append(f"**Before:** `{ic.before}`" if ic.before else "**Before:** _(new)_")
            lines.append(f"**After:** `{ic.after}`" if ic.after else "**After:** _(removed)_")
            if ic.callers:
                caller_list = ", ".join(f"`{c}`" for c in ic.callers)
                lines.append(f"**Callers:** {caller_list}")
            lines.append("")

    # Decisions and Assumptions
    has_decisions = bool(report.ai_analysis.decisions)
    has_assumptions = bool(report.ai_analysis.assumptions)
    if has_decisions or has_assumptions:
        lines.append("## Decisions and Assumptions")
        if has_decisions:
            lines.append("### Decisions")
            for i, d in enumerate(report.ai_analysis.decisions, 1):
                lines.append(f"**{i}. {d.description}**")
                lines.append(f"- Rationale: {d.rationale}")
                lines.append(f"- Risk: {d.risk}")
                lines.append("")
        if has_assumptions:
            lines.append("### Assumptions")
            for i, a in enumerate(report.ai_analysis.assumptions, 1):
                lines.append(f"**{i}. {a.description}**")
                lines.append(f"- Location: `{a.location}`")
                lines.append(f"- Risk: {a.risk}")
                lines.append("")

    # Anomalies
    if report.ai_analysis.anomalies:
        lines.append("## Anomalies")
        for anomaly in report.ai_analysis.anomalies:
            icon = _SEVERITY_ICON.get(anomaly.severity, "🔵")
            lines.append(f"{icon} **{anomaly.description}**")
            lines.append(f"  Location: `{anomaly.location}`")
            lines.append("")

    # Test Gaps
    if report.ai_analysis.test_gaps:
        lines.append("## Test Gaps")
        for gap in report.ai_analysis.test_gaps:
            lines.append(f"- **{gap.behaviour}**  ")
            lines.append(f"  `{gap.location}`")
        lines.append("")

    return "\n".join(lines)


def render_json(report: ImpactReport) -> str:
    return json.dumps(dataclasses.asdict(report), indent=2, default=str)
