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
from .analyzer import AnalyzerExit, ImpactAnalyzer
from .config import CONFIG_PATH, env_placeholder, load_config, read_toml_config
from .config_file import load_config_file
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


def _resolve_explicit_pr(
    owner: str,
    repo_name: str,
    pr_number: int,
    github_token: str | None,
    fetch_remote: str,
) -> RefsResult:
    """Path A: fetch refs for an explicit --pr number from the GitHub API."""
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


def _resolve_interactive_pr(
    owner: str,
    repo_name: str,
    github_token: str | None,
    fetch_remote: str,
) -> RefsResult:
    """Path D: list open PRs and prompt the user to pick one (interactive terminals only)."""
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
        return _resolve_explicit_pr(owner, repo_name, pr_number, github_token, fetch_remote)

    if _stdin_is_interactive():
        return _resolve_interactive_pr(owner, repo_name, github_token, fetch_remote)

    # Path E: non-interactive (CI / piped) — skip discovery, fall back silently
    return RefsResult(base=_FALLBACK_BASE, head=_FALLBACK_HEAD)


def _validate_ref_options(
    pr_number: int | None, base: str | None, head: str | None
) -> None:
    """Exit 1 if --pr is combined with --base or --head (mutually exclusive)."""
    if pr_number is not None and (base is not None or head is not None):
        stderr.print(
            "[bold red]Error:[/bold red] --pr cannot be combined with --base or --head. "
            "When --pr is given, the PR provides both SHAs from GitHub."
        )
        sys.exit(1)


def _normalize_direct_refs(
    pr_number: int | None, base: str | None, head: str | None
) -> tuple[str | None, str | None]:
    """Apply HEAD / HEAD~1 defaults when explicit SHAs are given without --pr."""
    if pr_number is None and (base is not None or head is not None):
        if head is None:
            head = "HEAD"
        if base is None:
            base = f"{head}~1"
    return base, head


def _load_historical_context(
    db_path: str, repo: str, no_history: bool
) -> tuple[list[dict] | None, list[dict] | None]:
    """Load anomaly history and hotspots from the history DB.

    Returns (anomaly_history, hotspots); both None when history is disabled or empty.
    """
    if no_history:
        return None, None
    run_count = get_run_count(db_path, repo)
    if run_count < 1:
        return None, None
    hotspots = load_hotspots(db_path, repo) or None
    anomaly_history = load_anomaly_patterns(db_path, repo) or None
    if hotspots or anomaly_history:
        stderr.print(f"[dim]History: {run_count} prior runs — calibrating anomaly detection.[/dim]")
    return anomaly_history, hotspots


def _build_pr_title(refs: RefsResult, metadata: dict) -> str:
    """Pick a display title: PR title from GitHub, first commit line, or SHA range."""
    if refs.pr_title is not None:
        return refs.pr_title
    commits = metadata.get("commits", [])
    if commits:
        return commits[0].splitlines()[0]
    return f"Changes {refs.base[:7]}..{refs.head[:7]}"


def _build_report(
    pr_title: str,
    refs: RefsResult,
    changed_files: list,
    blast_radius: list,
    interface_changes: list,
    ai_analysis,
    dependency_issues: list,
    hotspots: list[dict] | None,
) -> ImpactReport:
    """Package all pipeline results into an ImpactReport."""
    return ImpactReport(
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


def _check_severity_threshold(fail_on_severity: str, anomalies: list) -> bool:
    """Return True if any anomaly meets or exceeds the threshold; prints a warning if so."""
    if fail_on_severity == "none":
        return False
    threshold = _SEVERITY_ORDER[fail_on_severity]
    breaching = [a for a in anomalies if _SEVERITY_ORDER.get(a.severity, 0) >= threshold]
    if breaching:
        stderr.print(
            f"[bold red]--fail-on-severity={fail_on_severity}:[/bold red] "
            f"{len(breaching)} anomaly/anomalies at or above threshold"
        )
        return True
    return False


def _run_verdict_if_requested(
    run_verdict: bool,
    verdict_output: str | None,
    ai_analysis,
    dependency_issues: list,
) -> tuple:
    """Run verdict analysis when requested; render and write results.

    Returns (verdict | None, agent_should_continue: bool).
    """
    if not run_verdict and verdict_output is None:
        return None, False

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=Console(stderr=True),
        transient=True,
    ) as progress:
        progress.add_task("Running verdict analysis (1 API call)...", total=None)
        try:
            verdict = run_verdict_analysis(ai_analysis, dependency_issues)
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Verdict analysis failed: {e}")
            return None, False

    render_verdict_terminal(verdict, Console())

    if verdict_output is not None:
        try:
            Path(verdict_output).write_text(
                json.dumps(dataclasses.asdict(verdict), indent=2), encoding="utf-8"
            )
            stderr.print(f"[dim]Verdict written to[/dim] [cyan]{verdict_output}[/cyan]")
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Could not write verdict to {verdict_output!r}: {e}")

    return verdict, verdict.agent_should_continue


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
@click.option(
    "--run-id",
    "run_id",
    default=None,
    help="Explicit UUID for this run (auto-generated if omitted); used to deep-link from the web UI",
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
    run_id: str | None,
) -> None:
    """Analyse the impact of a code change between two commit SHAs or a GitHub PR."""
    if _stdin_is_interactive():
        _print_banner()
    try:
        load_config()
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] Could not load config: {e}")

    # Load .primpact.yml if present (best-effort — never blocks the pipeline)
    try:
        pr_config = load_config_file(repo)
    except Exception:
        pr_config = None

    _validate_ref_options(pr_number, base, head)
    base, head = _normalize_direct_refs(pr_number, base, head)

    try:
        repo_obj = git.Repo(repo)
    except Exception as e:
        stderr.print(f"[bold red]Error:[/bold red] Could not open git repository: {e}")
        sys.exit(1)

    if pr_number is not None or (base is None and head is None):
        refs = _resolve_refs(repo_obj, pr_number, base, head)
    else:
        refs = RefsResult(base=base, head=head)

    db_path = history_db or os.path.join(repo, ".primpact", "history.db")
    anomaly_history, hotspots = _load_historical_context(db_path, repo, no_history)

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
            pr_config=pr_config,
        )
        try:
            changed_files, blast_radius, interface_changes, ai_analysis, metadata, dependency_issues = (
                analyzer.run(progress)
            )
        except AnalyzerExit as exc:
            if exc.message:
                if exc.code != 0:
                    stderr.print(f"[bold red]Error:[/bold red] {exc.message}")
                else:
                    stderr.print(exc.message)
            sys.exit(exc.code)

    report = _build_report(
        _build_pr_title(refs, metadata),
        refs, changed_files, blast_radius, interface_changes,
        ai_analysis, dependency_issues, hotspots,
    )
    _write_outputs(report, output, json_output, sarif_output)
    render_terminal(report, Console(), output, json_output, sarif_output)

    # Save run to history (fire-and-forget; never affects exit code)
    if not no_history:
        stored_uuid = save_run(db_path, report, repo, run_uuid=run_id)
        stderr.print(f"[dim]Run ID: {stored_uuid}[/dim]")

    # Collect both exit conditions before exiting so neither silently suppresses the other.
    # exit 2 (verdict blockers) takes precedence over exit 1 (--fail-on-severity),
    # because exit 2 carries a concrete remediation list for an agent loop.
    effective_fail_severity = fail_on_severity
    if effective_fail_severity == "none" and pr_config and pr_config.fail_on_severity:
        effective_fail_severity = pr_config.fail_on_severity
    severity_exit = _check_severity_threshold(effective_fail_severity, report.ai_analysis.anomalies)
    _, verdict_continue = _run_verdict_if_requested(
        run_verdict, verdict_output, report.ai_analysis, report.dependency_issues
    )

    if verdict_continue:
        sys.exit(2)
    elif severity_exit:
        sys.exit(1)


@main.command()
@click.option("--port", default=8080, show_default=True, help="Port to listen on")
@click.option("--host", default="localhost", show_default=True, help="Host to bind to")
@click.option("--open", "open_browser", is_flag=True, default=False, help="Open the browser after starting")
@click.option(
    "--history-db",
    "history_db",
    default=None,
    help="Path to the history SQLite database (default: .primpact/history.db)",
)
def serve(port: int, host: str, open_browser: bool, history_db: str | None) -> None:
    """Start the PrImpact web UI server."""
    try:
        import uvicorn
        from .web.server import create_app
    except ImportError:
        raise click.ClickException(
            "Web dependencies are not installed. Run: pip install 'primpact[web]'"
        )

    db_path = history_db or os.environ.get("PRIMPACT_DB_PATH", ".primpact/history.db")
    app = create_app(db_path=db_path)

    url = f"http://{host}:{port}"
    stderr.print(f"[bold green]PrImpact[/bold green] serving at [link={url}]{url}[/link]")
    stderr.print("[dim]Press Ctrl+C to stop[/dim]")

    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
