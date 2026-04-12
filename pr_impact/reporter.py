import dataclasses
import importlib.metadata
import json
import re
from typing import NamedTuple

from rich import box as _rich_box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .models import ImpactReport, Verdict


class _SeverityStyle(NamedTuple):
    icon: str
    color: str
    bullet: str


_SEVERITY: dict[str, _SeverityStyle] = {
    "high":   _SeverityStyle(icon="🔴", color="bright_red",  bullet="●"),
    "medium": _SeverityStyle(icon="🟡", color="yellow",       bullet="◉"),
    "low":    _SeverityStyle(icon="🔵", color="bright_blue",  bullet="○"),
}
_DEFAULT_SEVERITY = _SeverityStyle(icon="🔵", color="bright_blue", bullet="○")


def _sev(key: str) -> _SeverityStyle:
    return _SEVERITY.get(key, _DEFAULT_SEVERITY)


def _sev_color(key: str, default: str = "dim") -> str:
    style = _SEVERITY.get(key)
    return style.color if style else default


def _fmt_churn(score: float | None) -> str:
    return str(int(score)) if score is not None else "—"


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
            lines.append(f"| `{entry.path}` | {entry.distance} | {uses} | {_fmt_churn(entry.churn_score)} |")
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
            lines.append(f"{_sev(anomaly.severity).icon} **{anomaly.description}**")
            lines.append(f"  Location: `{anomaly.location}`")
            lines.append("")

    # Semantic Equivalence
    risky = [v for v in report.ai_analysis.semantic_verdicts if v.verdict == "risky"]
    equivalent = [v for v in report.ai_analysis.semantic_verdicts if v.verdict == "equivalent"]
    if risky or equivalent:
        lines.append("## Semantic Analysis")
        if risky:
            lines.append("### ⚠ Logic Changes (small diff, significant impact)")
            for v in risky:
                lines.append(f"- **`{v.symbol}`** in `{v.file}`: {v.reason}")
            lines.append("")
        if equivalent:
            lines.append("### ~ Refactors (semantically equivalent)")
            for v in equivalent:
                lines.append(f"- **`{v.symbol}`** in `{v.file}`: {v.reason}")
            lines.append("")

    # Test Gaps
    if report.ai_analysis.test_gaps:
        lines.append("## Test Gaps")
        for gap in report.ai_analysis.test_gaps:
            lines.append(f"- **{gap.behaviour}**  ")
            lines.append(f"  `{gap.location}`")
        lines.append("")

    # Security Signals
    has_signals = bool(report.ai_analysis.security_signals)
    has_dep_issues = bool(report.dependency_issues)
    if has_signals or has_dep_issues:
        lines.append("## Security Signals")
        lines.append(
            "> ⚠️ Primpact is not a security audit. These are signals for human review, "
            "not verdicts. Treat HIGH signals as \"requires explanation\", not \"is malicious\"."
        )
        lines.append("")

        for sig in report.ai_analysis.security_signals:
            icon = _sev(sig.severity).icon
            line_info = f" · line {sig.location.line}" if sig.location.line else ""
            lines.append(f"### {icon} {sig.severity.upper()} — {sig.description}")
            lines.append(f"**File:** `{sig.location.file}`{line_info}")
            if sig.why_unusual:
                lines.append(f"**Why this is unusual:** {sig.why_unusual}")
            if sig.suggested_action:
                lines.append(f"**Suggested action:** {sig.suggested_action}")
            lines.append("")

        if has_dep_issues:
            lines.append("### Dependency Issues")
            for issue in report.dependency_issues:
                icon = _sev(issue.severity).icon
                lines.append(f"- {icon} **{issue.package_name}** ({issue.issue_type}): {issue.description}")
            lines.append("")

    # Historical Hotspots
    if report.historical_hotspots:
        lines.append("## Historical Hotspots")
        lines.append(
            "_Files that have appeared most frequently in blast radii across past analyses._"
        )
        lines.append("")
        lines.append("| File | Appearances |")
        lines.append("|------|-------------|")
        for h in report.historical_hotspots:
            lines.append(f"| `{h.file}` | {h.appearances} |")
        lines.append("")

    return "\n".join(lines)


def render_json(report: ImpactReport) -> str:
    return json.dumps(dataclasses.asdict(report), indent=2, default=str)


def _parse_location(location: str) -> dict | None:
    """Parse an AI-generated location string into a SARIF location dict.

    Handles two forms:
    - "path/to/file.py:12"        → region.startLine set to 12
    - "path/to/file.py:func_name" → logicalLocations entry with that name
    - "path/to/file.py, ..."      → URI only, best-effort via regex fallback

    Returns None if no recognisable file path can be extracted.
    """
    if ":" in location:
        path, _, remainder = location.partition(":")
        path = path.strip()
        remainder = remainder.strip()
    else:
        m = re.match(r"^([\w./\-]+\.\w+)", location)
        if not m:
            return None
        path = m.group(1)
        remainder = ""

    if not re.match(r"^[\w./\-]+\.\w+$", path):
        return None

    physical_loc: dict = {"artifactLocation": {"uri": path}}
    loc: dict = {"physicalLocation": physical_loc}

    if remainder:
        try:
            physical_loc["region"] = {"startLine": int(remainder)}
        except ValueError:
            loc["logicalLocations"] = [{"name": remainder}]

    return loc


def render_sarif(report: ImpactReport) -> str:
    """Render an ImpactReport as SARIF 2.1.0 JSON.

    Anomalies map to results with level error/warning/note.
    Test gaps map to results with level note under a separate rule.
    """
    try:
        version = importlib.metadata.version("pr-impact")
    except importlib.metadata.PackageNotFoundError:
        version = "0.0.0"

    _severity_to_level = {"high": "error", "medium": "warning", "low": "note"}

    rules = []
    results = []

    if report.ai_analysis.anomalies:
        rules.append({
            "id": "primpact/anomaly",
            "name": "Anomaly",
            "shortDescription": {"text": "Anomaly detected in PR changes"},
        })
        for anomaly in report.ai_analysis.anomalies:
            level = _severity_to_level.get(anomaly.severity, "note")
            loc = _parse_location(anomaly.location)
            result: dict = {
                "ruleId": "primpact/anomaly",
                "level": level,
                "message": {"text": anomaly.description},
            }
            if loc:
                result["locations"] = [loc]
            results.append(result)

    if report.ai_analysis.test_gaps:
        rules.append({
            "id": "primpact/test-gap",
            "name": "TestGap",
            "shortDescription": {"text": "Behaviour not covered by tests"},
        })
        for gap in report.ai_analysis.test_gaps:
            loc = _parse_location(gap.location)
            result = {
                "ruleId": "primpact/test-gap",
                "level": "note",
                "message": {"text": gap.behaviour},
            }
            if loc:
                result["locations"] = [loc]
            results.append(result)

    if report.ai_analysis.security_signals:
        rules.append({
            "id": "primpact/security-signal",
            "name": "SecuritySignal",
            "shortDescription": {"text": "Security pattern detected in PR changes"},
        })
        for sig in report.ai_analysis.security_signals:
            level = _severity_to_level.get(sig.severity, "note")
            message = sig.description
            if sig.why_unusual:
                message += f" — {sig.why_unusual}"
            result = {
                "ruleId": "primpact/security-signal",
                "level": level,
                "message": {"text": message},
            }
            if sig.location.file:
                physical_loc: dict = {"artifactLocation": {"uri": sig.location.file}}
                if sig.location.line is not None:
                    physical_loc["region"] = {"startLine": sig.location.line}
                result["locations"] = [{"physicalLocation": physical_loc}]
            results.append(result)

    if report.dependency_issues:
        rules.append({
            "id": "primpact/dependency-issue",
            "name": "DependencyIssue",
            "shortDescription": {"text": "Dependency integrity issue detected"},
        })
        for issue in report.dependency_issues:
            level = _severity_to_level.get(issue.severity, "note")
            results.append({
                "ruleId": "primpact/dependency-issue",
                "level": level,
                "message": {"text": issue.description},
            })

    risky_verdicts = [v for v in report.ai_analysis.semantic_verdicts if v.verdict == "risky"]
    if risky_verdicts:
        rules.append({
            "id": "primpact/semantic-risk",
            "name": "SemanticRisk",
            "shortDescription": {"text": "Small diff with disproportionate semantic impact"},
        })
        for v in risky_verdicts:
            loc = _parse_location(v.file)
            result = {
                "ruleId": "primpact/semantic-risk",
                "level": "warning",
                "message": {"text": f"{v.symbol}: {v.reason}"},
            }
            if loc:
                result["locations"] = [loc]
            results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "primpact",
                        "version": version,
                        "informationUri": "https://github.com/hellstromme/primpact",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def _render_header_section(console: Console, report: ImpactReport, sha_range: str) -> None:
    header_content = (
        "[bold bright_blue]PR IMPACT REPORT[/bold bright_blue]\n"
        f"{report.pr_title}  ·  {sha_range}\n"
        f"[dim]{len(report.changed_files)} files changed  ·  "
        f"{len(report.blast_radius)} downstream  ·  "
        f"{len(report.interface_changes)} interface changes[/dim]"
    )
    console.print(Panel(header_content, box=_rich_box.SIMPLE_HEAVY, border_style="bright_blue"))


def _render_summary_section(console: Console, report: ImpactReport) -> None:
    summary = report.ai_analysis.summary
    console.print(Rule("SUMMARY", style="bright_blue"))
    console.print()
    console.print(f"  {summary}" if summary else "  [dim]No summary available.[/dim]")
    console.print()


def _render_blast_radius_section(console: Console, report: ImpactReport) -> None:
    direct = len(report.changed_files)
    downstream = len(report.blast_radius)
    max_dist = max((e.distance for e in report.blast_radius), default=0)
    console.print(Rule("BLAST RADIUS", style="bright_blue"))
    console.print(
        f"  [dim]{direct} direct  ·  {downstream} downstream  ·  max {max_dist} hop(s)[/dim]"
    )
    console.print()
    if report.blast_radius:
        table = Table(
            box=None,
            show_header=True,
            header_style="dim",
            pad_edge=False,
            padding=(0, 2),
        )
        table.add_column("File", style="cyan", no_wrap=True)
        table.add_column("Dist", justify="center")
        table.add_column("Uses")
        table.add_column("Churn", justify="right", style="dim")
        for entry in report.blast_radius:
            uses = ", ".join(entry.imported_symbols) if entry.imported_symbols else "—"
            if entry.distance == 1:
                row_style = "bold"
            elif entry.distance == 3:
                row_style = "dim"
            else:
                row_style = ""
            table.add_row(entry.path, str(entry.distance), uses, _fmt_churn(entry.churn_score), style=row_style)
        console.print(table)
        console.print()


def _render_interface_changes_section(console: Console, report: ImpactReport) -> None:
    if not report.interface_changes:
        return
    console.print(Rule("INTERFACE CHANGES", style="bright_blue"))
    console.print(
        f"  [dim]{len(report.interface_changes)} symbol(s) with changed signatures[/dim]"
    )
    console.print()
    for ic in report.interface_changes:
        before_val = ic.before if ic.before else "(new)"
        after_val = ic.after if ic.after else "(removed)"
        body = Text()
        body.append("  before  ", style="dim")
        body.append(before_val + "\n", style="italic")
        body.append("  after   ", style="dim")
        body.append(after_val, style="italic")
        if ic.callers:
            body.append("\n  callers ", style="dim")
            body.append("  ".join(ic.callers), style="cyan dim")
        console.print(
            Panel(
                body,
                title=(
                    f"[bold cyan]{ic.symbol}[/bold cyan]  [dim]·[/dim]  [cyan]{ic.file}[/cyan]"
                ),
                title_align="left",
                box=_rich_box.ROUNDED,
                border_style="cyan",
            )
        )
    console.print()


def _render_decisions_section(console: Console, report: ImpactReport) -> None:
    has_decisions = bool(report.ai_analysis.decisions)
    has_assumptions = bool(report.ai_analysis.assumptions)
    if not has_decisions and not has_assumptions:
        return
    console.print(Rule("DECISIONS & ASSUMPTIONS", style="bright_blue"))
    console.print()
    if has_decisions:
        console.print("  [bold]Decisions[/bold]")
        console.print()
        for i, d in enumerate(report.ai_analysis.decisions, 1):
            risk_lower = d.risk.lower() if d.risk else ""
            risk_color = _sev_color(risk_lower)
            console.print(f"  [bold]{i}[/bold]  {d.description}")
            console.print(f"     [dim]Rationale:[/dim] {d.rationale}")
            console.print(f"     [dim]Risk:[/dim] [{risk_color}]{d.risk}[/{risk_color}]")
            console.print()
    if has_assumptions:
        console.print("  [bold]Assumptions[/bold]")
        console.print()
        for i, a in enumerate(report.ai_analysis.assumptions, 1):
            risk_lower = a.risk.lower() if a.risk else ""
            risk_color = _sev_color(risk_lower)
            console.print(
                f"  [bold]{i}[/bold]  {a.description}"
                f"  [{risk_color}]{a.risk.upper()} RISK[/{risk_color}]"
            )
            console.print(f"     [cyan dim]{a.location}[/cyan dim]")
            console.print()


def _render_anomalies_section(console: Console, report: ImpactReport) -> None:
    if not report.ai_analysis.anomalies:
        return
    high = sum(1 for a in report.ai_analysis.anomalies if a.severity == "high")
    medium = sum(1 for a in report.ai_analysis.anomalies if a.severity == "medium")
    low = sum(1 for a in report.ai_analysis.anomalies if a.severity == "low")
    console.print(Rule("ANOMALIES", style="bright_blue"))
    console.print(
        f"  [bright_red]{high} high[/bright_red]  ·  "
        f"[yellow]{medium} medium[/yellow]  ·  "
        f"[bright_blue]{low} low[/bright_blue]"
    )
    console.print()
    for anomaly in report.ai_analysis.anomalies:
        color = _sev_color(anomaly.severity, "bright_blue")
        bullet = _sev(anomaly.severity).bullet
        console.print(
            f"  [{color}]{bullet}[/{color}] {anomaly.description}"
            f"  [{color}][bold]{anomaly.severity.upper()}[/bold][/{color}]"
        )
        console.print(f"    [cyan dim]{anomaly.location}[/cyan dim]")
        console.print()


def _render_semantic_section(console: Console, report: ImpactReport) -> None:
    risky_verdicts = [v for v in report.ai_analysis.semantic_verdicts if v.verdict == "risky"]
    equiv_verdicts = [v for v in report.ai_analysis.semantic_verdicts if v.verdict == "equivalent"]
    if not risky_verdicts and not equiv_verdicts:
        return
    console.print(Rule("SEMANTIC ANALYSIS", style="bright_blue"))
    console.print()
    if risky_verdicts:
        console.print("  [bold yellow]⚠ Logic changes (small diff, significant impact)[/bold yellow]")
        console.print()
        for v in risky_verdicts:
            console.print(f"  [yellow]◉[/yellow] [bold]{v.symbol}[/bold]  [cyan dim]{v.file}[/cyan dim]")
            console.print(f"    [dim]{v.reason}[/dim]")
        console.print()
    if equiv_verdicts:
        console.print("  [bold dim]~ Refactors (semantically equivalent)[/bold dim]")
        console.print()
        for v in equiv_verdicts:
            console.print(f"  [dim]~[/dim] [bold]{v.symbol}[/bold]  [cyan dim]{v.file}[/cyan dim]")
            console.print(f"    [dim]{v.reason}[/dim]")
        console.print()


def _render_test_gaps_section(console: Console, report: ImpactReport) -> None:
    if not report.ai_analysis.test_gaps:
        return
    console.print(Rule("TEST GAPS", style="bright_blue"))
    console.print(f"  [dim]{len(report.ai_analysis.test_gaps)} behaviour(s) not covered[/dim]")
    console.print()
    for gap in report.ai_analysis.test_gaps:
        console.print(f"  [dim]◇[/dim]  {gap.behaviour}")
        console.print(f"     [cyan dim]{gap.location}[/cyan dim]")
    console.print()


def _render_security_section(console: Console, report: ImpactReport) -> None:
    has_signals = bool(report.ai_analysis.security_signals)
    has_dep_issues = bool(report.dependency_issues)
    if not has_signals and not has_dep_issues:
        return
    high_s = sum(1 for s in report.ai_analysis.security_signals if s.severity == "high")
    med_s = sum(1 for s in report.ai_analysis.security_signals if s.severity == "medium")
    low_s = sum(1 for s in report.ai_analysis.security_signals if s.severity == "low")
    console.print(Rule("SECURITY SIGNALS", style="bright_red"))
    console.print(
        "  [dim]⚠ Not a security audit — signals for human review, not verdicts[/dim]"
    )
    if has_signals:
        console.print(
            f"  [bright_red]{high_s} high[/bright_red]  ·  "
            f"[yellow]{med_s} medium[/yellow]  ·  "
            f"[bright_blue]{low_s} low[/bright_blue]"
        )
    console.print()
    for sig in report.ai_analysis.security_signals:
        color = _sev_color(sig.severity, "bright_blue")
        bullet = _sev(sig.severity).bullet
        line_info = f" :{sig.location.line}" if sig.location.line else ""
        console.print(
            f"  [{color}]{bullet}[/{color}] {sig.description}"
            f"  [{color}][bold]{sig.severity.upper()}[/bold][/{color}]"
        )
        console.print(f"    [cyan dim]{sig.location.file}{line_info}[/cyan dim]")
        if sig.why_unusual:
            console.print(f"    [dim]{sig.why_unusual}[/dim]")
        if sig.suggested_action:
            console.print(f"    [dim italic]→ {sig.suggested_action}[/dim italic]")
        console.print()
    if has_dep_issues:
        console.print("  [bold]Dependency Issues[/bold]")
        console.print()
        for issue in report.dependency_issues:
            color = _sev_color(issue.severity, "bright_blue")
            bullet = _sev(issue.severity).bullet
            console.print(
                f"  [{color}]{bullet}[/{color}] [{color}]{issue.package_name}[/{color}]"
                f" [dim]({issue.issue_type})[/dim]"
            )
            console.print(f"    [dim]{issue.description}[/dim]")
            console.print()


def _render_hotspots_section(console: Console, report: ImpactReport) -> None:
    if not report.historical_hotspots:
        return
    console.print(Rule("HISTORICAL HOTSPOTS", style="bright_blue"))
    console.print(
        "  [dim]Files most frequently in blast radii across past analyses[/dim]"
    )
    console.print()
    table = Table(
        box=None,
        show_header=True,
        header_style="dim",
        pad_edge=False,
        padding=(0, 2),
    )
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Appearances", justify="right", style="dim")
    for h in report.historical_hotspots:
        table.add_row(h.file, str(h.appearances))
    console.print(table)
    console.print()


def _render_footer_section(
    console: Console,
    output: str | None,
    json_output: str | None,
    sarif_output: str | None,
) -> None:
    if not output and not json_output and not sarif_output:
        return
    console.print(Rule(style="bright_blue"))
    if output:
        console.print(f"  [dim]Report written to[/dim]  [cyan]{output}[/cyan]")
    if json_output:
        console.print(f"  [dim]JSON written to[/dim]   [cyan]{json_output}[/cyan]")
    if sarif_output:
        console.print(f"  [dim]SARIF written to[/dim]  [cyan]{sarif_output}[/cyan]")
    console.print(Rule(style="bright_blue"))


def render_terminal(
    report: ImpactReport,
    console: Console,
    output: str | None = None,
    json_output: str | None = None,
    sarif_output: str | None = None,
) -> None:
    sha_range = f"{report.base_sha[:7]}..{report.head_sha[:7]}"
    _render_header_section(console, report, sha_range)
    _render_summary_section(console, report)
    _render_blast_radius_section(console, report)
    _render_interface_changes_section(console, report)
    _render_decisions_section(console, report)
    _render_anomalies_section(console, report)
    _render_semantic_section(console, report)
    _render_test_gaps_section(console, report)
    _render_security_section(console, report)
    _render_hotspots_section(console, report)
    _render_footer_section(console, output, json_output, sarif_output)


def render_verdict_terminal(verdict: Verdict, console: Console) -> None:
    """Render the agent verdict panel to the console."""
    has_blockers = (
        verdict.agent_should_continue
        or verdict.status == "has_blockers"
        or bool(verdict.blockers)
    )
    if has_blockers:
        rule_style = "bright_red"
        status_text = Text("● BLOCKERS FOUND — agent should continue", style="bold bright_red")
    else:
        rule_style = "green"
        status_text = Text("✓ CLEAN — agent may stop", style="bold green")

    console.print(Rule("AGENT VERDICT", style=rule_style))
    console.print(f"  {status_text}")
    if verdict.rationale:
        console.print(f"  [dim]{verdict.rationale}[/dim]")
    console.print()

    if verdict.blockers:
        for b in verdict.blockers:
            console.print(f"  [bold bright_red]●[/bold bright_red] [{b.category}] {b.description}")
            if b.location:
                console.print(f"    [cyan dim]{b.location}[/cyan dim]")
        console.print()

    console.print(Rule(style=rule_style))
