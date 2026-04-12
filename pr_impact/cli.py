import dataclasses
import json
import os
import sys
from pathlib import Path

import click
import git
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text
from typing import Protocol

from .ai_layer import run_verdict_analysis
from .analyzer import ImpactAnalyzer
from .config import CONFIG_PATH, env_placeholder, load_config, read_toml_config
from .github import detect_github_remote, fetch_open_prs, fetch_pr
from .history import get_run_count, load_anomaly_patterns, load_hotspots, save_run
from .models import HistoricalHotspot, ImpactReport, RefsResult
from .reporter import render_json, render_markdown, render_sarif, render_terminal, render_verdict_terminal

stderr = Console(stderr=True)


class _ProgressProtocol(Protocol):
    """Structural type for the Rich Progress object passed to _run_pipeline.

    Defined here so tests can inject a mock without importing Rich.
    """

    def add_task(self, description: str, total: float | None = None) -> object: ...
    def update(self, task_id: object, **kwargs: object) -> None: ...
    def remove_task(self, task_id: object) -> None: ...


# Default SHAs used when no PR or explicit refs are supplied
_FALLBACK_BASE = "HEAD~1"
_FALLBACK_HEAD = "HEAD"

# Severity ordering for --fail-on-severity threshold comparison
_SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


_TITLE_ART = """\
  :::::::::  :::::::::  ::::::::::: ::::    ::::  :::::::::     :::      ::::::::  :::::::::::
  :+:    :+: :+:    :+:     :+:     +:+:+: :+:+:+ :+:    :+:  :+: :+:   :+:    :+:     :+:
  +:+    +:+ +:+    +:+     +:+     +:+ +:+:+ +:+ +:+    +:+ +:+   +:+  +:+            +:+
  +#++:++#+  +#++:++#:      +#+     +#+  +:+  +#+ +#++:++#+ +#++:++#++: +#+            +#+
  +#+        +#+    +#+     +#+     +#+       +#+ +#+       +#+     +#+ +#+            +#+
  #+#        #+#    #+#     #+#     #+#       #+# #+#       #+#     #+# #+#    #+#     #+#
  ###        ###    ### ########### ###       ### ###       ###     ###  ########      ###"""

def _print_banner() -> None:
    """Print the startup banner to stderr."""
    try:
        from importlib.metadata import version as _pkg_version
        ver = _pkg_version("pr-impact")
    except Exception:
        ver = "dev"

    content = Text()
    for ch in _TITLE_ART:
        if ch == "#":
            content.append(ch, style="bold cyan")
        elif ch == "+":
            content.append(ch, style="cyan")
        elif ch == ":":
            content.append(ch, style="dim cyan")
        else:
            content.append(ch)
    content.append("\n  ")
    content.append(f"v{ver}", style="bold white")
    content.append("  ·  blast-radius analysis for code changes", style="dim")
    stderr.print(Panel(content, expand=False, border_style="dim cyan", padding=(0, 1)))


def _stdin_is_interactive() -> bool:
    """Return True when stdin is an interactive terminal."""
    return sys.stdin.isatty()


def _warn_no_github_token() -> None:
    """Print a one-time warning that no GitHub token was found."""
    stderr.print(
        "[yellow]Warning:[/yellow] No GitHub token found. "
        "Set the [bold]GITHUB_TOKEN[/bold] environment variable in this terminal session, "
        f"or add [bold]github_token = \"{env_placeholder('GITHUB_TOKEN')}\"[/bold] to "
        f"[bold]{CONFIG_PATH}[/bold]. "
        "Unauthenticated requests will fail for private repositories."
    )


def _write_outputs(
    report: "ImpactReport",
    output: str | None,
    json_output: str | None,
    sarif_output: str | None,
) -> None:
    """Write Markdown, JSON, and/or SARIF report files if paths were given."""
    if output:
        try:
            with open(output, "w", encoding="utf-8") as fh:
                fh.write(render_markdown(report))
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Could not write report to {output}: {e}")
    if json_output:
        try:
            with open(json_output, "w", encoding="utf-8") as fh:
                fh.write(render_json(report))
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Could not write JSON to {json_output}: {e}")
    if sarif_output:
        try:
            with open(sarif_output, "w", encoding="utf-8") as fh:
                fh.write(render_sarif(report))
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Could not write SARIF to {sarif_output}: {e}")


def _format_pr_title(number: int, raw_title: str | None) -> str:
    """Return a display string like '#42: feat: add login'."""
    return f"#{number}: {raw_title or f'PR {number}'}"


def _get_github_token() -> str | None:
    """Return a GitHub token from env or config file, or None if absent."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    config = read_toml_config()
    if config is None:
        return None
    raw = config.get("github_token") or config.get("GITHUB_TOKEN")
    if not raw:
        return None
    expanded = os.path.expandvars(str(raw))
    if expanded == raw and ("%" in raw or "$" in raw):
        return None
    return expanded or None


def _print_pr_table(prs: list[dict]) -> None:
    """Print a Rich table of open PRs to stderr for interactive selection."""
    title = "Open Pull Requests (first 100 shown)" if len(prs) == 100 else f"Open Pull Requests ({len(prs)} found)"
    table = Table(title=title, box=None, show_header=True, header_style="bold dim", pad_edge=False)
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Author", style="dim")
    table.add_column("Status", style="dim")
    for pr in prs:
        status = "draft" if pr.get("draft") else "open"
        table.add_row(
            str(pr["number"]),
            pr.get("title", ""),
            pr.get("user", {}).get("login", "unknown"),
            status,
        )
    stderr.print(table)


def _resolve_refs(
    repo_obj: git.Repo,
    pr_number: int | None,
    base: str | None,
    head: str | None,
) -> RefsResult:
    """Resolve base/head SHAs and PR metadata from GitHub or fall back to HEAD~1..HEAD.

    Called only when --pr was given or neither --base nor --head was supplied.
    Exits with code 1 on unrecoverable errors to preserve CliRunner semantics.
    """
    github_token = _get_github_token()
    _interactive = pr_number is None  # True → no explicit --pr, attempt discovery

    if github_token is None and not _interactive:
        _warn_no_github_token()

    github_remote = detect_github_remote([(r.name, r.urls) for r in repo_obj.remotes])

    if github_remote is None:
        if not _interactive:
            # Path B: --pr given but no GitHub remote detectable
            stderr.print(
                "[bold red]Error:[/bold red] Could not detect a GitHub remote in this repository. "
                "Use --base and --head to specify SHAs directly."
            )
            sys.exit(1)
        # Path C: no --pr, no remote → fall back silently
        return RefsResult(base=_FALLBACK_BASE, head=_FALLBACK_HEAD)

    owner, repo_name, fetch_remote = github_remote

    if not _interactive:
        # Path A: explicit --pr with a known remote
        try:
            pr_data = fetch_pr(owner, repo_name, pr_number, github_token)
        except RuntimeError as e:
            stderr.print(f"[bold red]Error:[/bold red] {e}")
            sys.exit(1)
        return RefsResult(
            base=pr_data["base"]["sha"],
            head=pr_data["head"]["sha"],
            pr_title=_format_pr_title(pr_number, pr_data.get("title")),
            fetch_pr_number=pr_number,
            fetch_base_ref=pr_data.get("base", {}).get("ref"),
            fetch_remote=fetch_remote,
        )

    if _stdin_is_interactive():
        # Path D: interactive terminal — list open PRs and let the user pick
        if github_token is None:
            _warn_no_github_token()
        try:
            open_prs = fetch_open_prs(owner, repo_name, github_token)
        except RuntimeError as e:
            stderr.print(f"[yellow]Warning:[/yellow] Could not fetch open PRs: {e}. Falling back to HEAD~1 → HEAD.")
            return RefsResult(base=_FALLBACK_BASE, head=_FALLBACK_HEAD)

        if not open_prs:
            stderr.print("[yellow]No open PRs found — analysing HEAD~1 → HEAD.[/yellow]")
            return RefsResult(base=_FALLBACK_BASE, head=_FALLBACK_HEAD)

        _print_pr_table(open_prs)
        sys.stderr.flush()
        pr_numbers = {str(p["number"]) for p in open_prs}
        chosen = click.prompt("Enter PR number to analyse", prompt_suffix=" > ")
        if chosen not in pr_numbers:
            stderr.print(f"[bold red]Error:[/bold red] '{chosen}' is not a valid PR number from the list.")
            sys.exit(1)
        selected = next(p for p in open_prs if str(p["number"]) == chosen)
        selected_num = selected["number"]
        return RefsResult(
            base=selected["base"]["sha"],
            head=selected["head"]["sha"],
            pr_title=_format_pr_title(selected_num, selected.get("title")),
            fetch_pr_number=selected_num,
            fetch_base_ref=selected.get("base", {}).get("ref"),
            fetch_remote=fetch_remote,
        )

    # Path E: non-interactive (CI / piped) — skip discovery, fall back silently
    return RefsResult(base=_FALLBACK_BASE, head=_FALLBACK_HEAD)


@click.group()
def main() -> None:
    pass


@main.command()
@click.option("--repo", required=True, help="Path to the local git repository")
@click.option("--pr", "pr_number", type=int, default=None, help="GitHub PR number to analyse")
@click.option("--head", default=None, help="Head commit SHA (default: HEAD)")
@click.option("--base", default=None, help="Base commit SHA (default: <head>~1)")
@click.option("--output", default=None, help="Write Markdown report to this file")
@click.option("--json", "json_output", default=None, help="Write JSON sidecar to this file")
@click.option("--sarif", "sarif_output", default=None, help="Write SARIF 2.1.0 output to this file")
@click.option(
    "--max-depth", default=3, show_default=True, help="Maximum BFS depth for blast radius"
)
@click.option(
    "--fail-on-severity",
    default="none",
    show_default=True,
    type=click.Choice(["none", "low", "medium", "high"]),
    help="Exit 1 if any anomaly meets or exceeds this severity level",
)
@click.option(
    "--check-osv",
    is_flag=True,
    default=False,
    help="Query the OSV vulnerability database for new dependencies (requires network access)",
)
@click.option(
    "--verdict",
    "run_verdict",
    is_flag=True,
    default=False,
    help="Run agent verdict analysis and print result to terminal (exit 2 if blockers found)",
)
@click.option(
    "--verdict-json",
    "verdict_output",
    default=None,
    help="Also write agent verdict JSON to this file (implies --verdict)",
)
@click.option(
    "--history-db",
    "history_db",
    default=None,
    help="Path to the history SQLite database (default: <repo>/.primpact/history.db)",
)
@click.option(
    "--no-history",
    "no_history",
    is_flag=True,
    default=False,
    help="Skip reading from and writing to the history database",
)
def analyse(
    repo: str,
    pr_number: int | None,
    base: str | None,
    head: str | None,
    output: str | None,
    json_output: str | None,
    sarif_output: str | None,
    max_depth: int,
    fail_on_severity: str,
    check_osv: bool,
    run_verdict: bool,
    verdict_output: str | None,
    history_db: str | None,
    no_history: bool,
) -> None:
    """Analyse the impact of a code change between two commit SHAs or a GitHub PR."""
    if _stdin_is_interactive():
        _print_banner()
    try:
        load_config()
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] Could not load config: {e}")

    # --pr cannot be combined with --base or --head
    if pr_number is not None and (base is not None or head is not None):
        stderr.print(
            "[bold red]Error:[/bold red] --pr cannot be combined with --base or --head. "
            "When --pr is given, the PR provides both SHAs from GitHub."
        )
        sys.exit(1)

    # Direct SHA mode: apply head/base defaults before opening the repo
    if pr_number is None and (base is not None or head is not None):
        if head is None:
            head = "HEAD"
        if base is None:
            base = f"{head}~1"

    # Open the repo early — needed for both PR resolution and the pipeline
    try:
        repo_obj = git.Repo(repo)
    except Exception as e:
        stderr.print(f"[bold red]Error:[/bold red] Could not open git repository: {e}")
        sys.exit(1)

    # Resolve refs: GitHub PR lookup or HEAD~1..HEAD fallback
    if pr_number is not None or (base is None and head is None):
        refs = _resolve_refs(repo_obj, pr_number, base, head)
    else:
        refs = RefsResult(base=base, head=head)

    # Resolve history DB path and load historical context
    _db_path = history_db or os.path.join(repo, ".primpact", "history.db")
    anomaly_history: list[dict] | None = None
    hotspots: list[dict] | None = None
    if not no_history:
        run_count = get_run_count(_db_path, repo)
        if run_count >= 1:
            hotspots = load_hotspots(_db_path, repo) or None
            anomaly_history = load_anomaly_patterns(_db_path, repo) or None
            if hotspots or anomaly_history:
                stderr.print(
                    f"[dim]History: {run_count} prior runs — calibrating anomaly detection.[/dim]"
                )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=Console(stderr=True),
        transient=True,
    ) as progress:
        analyzer = ImpactAnalyzer(
            repo, repo_obj, refs,
            max_depth=max_depth,
            check_osv=check_osv,
            anomaly_history=anomaly_history,
            hotspots=hotspots,
        )
        changed_files, blast_radius, interface_changes, ai_analysis, metadata, dependency_issues = (
            analyzer.run(progress)
        )

    # Build title: from resolved PR or fall back to first commit message / SHA range
    if refs.pr_title is not None:
        pr_title = refs.pr_title
    else:
        commits = metadata.get("commits", [])
        pr_title = (
            commits[0].splitlines()[0]
            if commits
            else f"Changes {refs.base[:7]}..{refs.head[:7]}"
        )

    report = ImpactReport(
        pr_title=pr_title,
        base_sha=refs.base,
        head_sha=refs.head,
        changed_files=changed_files,
        blast_radius=blast_radius,
        interface_changes=interface_changes,
        ai_analysis=ai_analysis,
        dependency_issues=dependency_issues,
        historical_hotspots=[
            HistoricalHotspot(file=h["file"], appearances=h["appearances"])
            for h in (hotspots or [])
        ],
    )

    _write_outputs(report, output, json_output, sarif_output)
    render_terminal(report, Console(), output, json_output, sarif_output)

    # Save run to history (fire-and-forget; never affects exit code)
    if not no_history:
        save_run(_db_path, report, repo)

    # Collect both exit conditions before exiting so neither silently suppresses the other.
    # exit 2 (verdict blockers) takes precedence over exit 1 (--fail-on-severity),
    # because exit 2 carries a concrete remediation list for an agent loop.
    severity_exit = False
    if fail_on_severity != "none":
        threshold = _SEVERITY_ORDER[fail_on_severity]
        breaching = [
            a for a in report.ai_analysis.anomalies
            if _SEVERITY_ORDER.get(a.severity, 0) >= threshold
        ]
        if breaching:
            stderr.print(
                f"[bold red]--fail-on-severity={fail_on_severity}:[/bold red] "
                f"{len(breaching)} anomaly/anomalies at or above threshold"
            )
            severity_exit = True

    verdict_continue = False
    if run_verdict or verdict_output is not None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=Console(stderr=True),
            transient=True,
        ) as progress:
            progress.add_task("Running verdict analysis (1 API call)...", total=None)
            try:
                verdict = run_verdict_analysis(report.ai_analysis, report.dependency_issues)
            except Exception as e:
                stderr.print(f"[yellow]Warning:[/yellow] Verdict analysis failed: {e}")
                verdict = None

        if verdict is not None:
            render_verdict_terminal(verdict, Console())

            if verdict_output is not None:
                try:
                    Path(verdict_output).write_text(
                        json.dumps(dataclasses.asdict(verdict), indent=2), encoding="utf-8"
                    )
                    stderr.print(f"[dim]Verdict written to[/dim] [cyan]{verdict_output}[/cyan]")
                except Exception as e:
                    stderr.print(f"[yellow]Warning:[/yellow] Could not write verdict to {verdict_output!r}: {e}")

            if verdict.agent_should_continue:
                verdict_continue = True

    if verdict_continue:
        sys.exit(2)
    elif severity_exit:
        sys.exit(1)


if __name__ == "__main__":
    main()
