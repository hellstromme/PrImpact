import dataclasses
import json
from typing import NamedTuple

from rich import box as _rich_box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .models import ImpactReport


class _SeverityStyle(NamedTuple):
    icon: str
    color: str
    bullet: str


_SEVERITY: dict[str, _SeverityStyle] = {
    "high":   _SeverityStyle("🔴", "bright_red",  "●"),
    "medium": _SeverityStyle("🟡", "yellow",       "◉"),
    "low":    _SeverityStyle("🔵", "bright_blue",  "○"),
}
_SEVERITY_DEFAULT = _SeverityStyle("🔵", "bright_blue", "○")


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
            icon = _SEVERITY.get(anomaly.severity, _SEVERITY_DEFAULT).icon
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


def render_terminal(
    report: ImpactReport,
    console: Console,
    output: str | None = None,
    json_output: str | None = None,
) -> None:
    sha_range = f"{report.base_sha[:7]}..{report.head_sha[:7]}"

    # ── Header ────────────────────────────────────────────────────────────────
    header_content = (
        "[bold bright_blue]PR IMPACT REPORT[/bold bright_blue]\n"
        f"{report.pr_title}  ·  {sha_range}\n"
        f"[dim]{len(report.changed_files)} files changed  ·  "
        f"{len(report.blast_radius)} downstream  ·  "
        f"{len(report.interface_changes)} interface changes[/dim]"
    )
    console.print(Panel(header_content, box=_rich_box.SIMPLE_HEAVY, border_style="bright_blue"))

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = report.ai_analysis.summary
    console.print(Rule("SUMMARY", style="bright_blue"))
    console.print()
    console.print(f"  {summary}" if summary else "  [dim]No summary available.[/dim]")
    console.print()

    # ── Blast Radius ──────────────────────────────────────────────────────────
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
            churn = str(int(entry.churn_score)) if entry.churn_score is not None else "—"
            if entry.distance == 1:
                row_style = "bold"
            elif entry.distance == 3:
                row_style = "dim"
            else:
                row_style = ""
            table.add_row(entry.path, str(entry.distance), uses, churn, style=row_style)

        console.print(table)
        console.print()

    # ── Interface Changes ─────────────────────────────────────────────────────
    if report.interface_changes:
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

    # ── Decisions & Assumptions ───────────────────────────────────────────────
    has_decisions = bool(report.ai_analysis.decisions)
    has_assumptions = bool(report.ai_analysis.assumptions)
    if has_decisions or has_assumptions:
        console.print(Rule("DECISIONS & ASSUMPTIONS", style="bright_blue"))
        console.print()

        if has_decisions:
            console.print("  [bold]Decisions[/bold]")
            console.print()
            for i, d in enumerate(report.ai_analysis.decisions, 1):
                risk_lower = d.risk.lower() if d.risk else ""
                risk_color = _SEVERITY[risk_lower].color if risk_lower in _SEVERITY else "dim"
                console.print(f"  [bold]{i}[/bold]  {d.description}")
                console.print(f"     [dim]Rationale:[/dim] {d.rationale}")
                console.print(f"     [dim]Risk:[/dim] [{risk_color}]{d.risk}[/{risk_color}]")
                console.print()

        if has_assumptions:
            console.print("  [bold]Assumptions[/bold]")
            console.print()
            for i, a in enumerate(report.ai_analysis.assumptions, 1):
                risk_lower = a.risk.lower() if a.risk else ""
                risk_color = _SEVERITY[risk_lower].color if risk_lower in _SEVERITY else "dim"
                console.print(
                    f"  [bold]{i}[/bold]  {a.description}"
                    f"  [{risk_color}]{a.risk.upper()} RISK[/{risk_color}]"
                )
                console.print(f"     [cyan dim]{a.location}[/cyan dim]")
                console.print()

    # ── Anomalies ─────────────────────────────────────────────────────────────
    if report.ai_analysis.anomalies:
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
            style = _SEVERITY.get(anomaly.severity, _SEVERITY_DEFAULT)
            color = style.color
            bullet = style.bullet
            console.print(
                f"  [{color}]{bullet}[/{color}] {anomaly.description}"
                f"  [{color}][bold]{anomaly.severity.upper()}[/bold][/{color}]"
            )
            console.print(f"    [cyan dim]{anomaly.location}[/cyan dim]")
            console.print()

    # ── Test Gaps ─────────────────────────────────────────────────────────────
    if report.ai_analysis.test_gaps:
        console.print(Rule("TEST GAPS", style="bright_blue"))
        console.print(f"  [dim]{len(report.ai_analysis.test_gaps)} behaviour(s) not covered[/dim]")
        console.print()
        for gap in report.ai_analysis.test_gaps:
            console.print(f"  [dim]◇[/dim]  {gap.behaviour}")
            console.print(f"     [cyan dim]{gap.location}[/cyan dim]")
        console.print()

    # ── Footer ────────────────────────────────────────────────────────────────
    if output or json_output:
        console.print(Rule(style="bright_blue"))
        if output:
            console.print(f"  [dim]Report written to[/dim]  [cyan]{output}[/cyan]")
        if json_output:
            console.print(f"  [dim]JSON written to[/dim]   [cyan]{json_output}[/cyan]")
        console.print(Rule(style="bright_blue"))
