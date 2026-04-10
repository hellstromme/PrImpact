import os
import sys
from collections import defaultdict
from pathlib import Path

import click
import git
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from .ai_layer import run_ai_analysis, run_verdict_analysis
from .classifier import classify_changed_file, get_interface_changes
from .dependency_graph import build_import_graph, get_blast_radius
from .git_analysis import ensure_commits_present, get_changed_files, get_git_churn, get_pr_metadata
from .github import detect_github_remote, fetch_open_prs, fetch_pr
from .models import AIAnalysis, ImpactReport, RefsResult
from .reporter import render_json, render_markdown, render_sarif, render_terminal, render_verdict_terminal
from .security import check_dependency_integrity, detect_pattern_signals

stderr = Console(stderr=True)

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


CONFIG_PATH = Path.home() / ".pr_impact" / "config.toml"


def _read_toml_config() -> dict | None:
    """Parse ~/.pr_impact/config.toml and return the dict, or None on missing/error."""
    if not CONFIG_PATH.exists():
        return None
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            try:
                import tomllib  # type: ignore[no-redef]
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]
        with open(CONFIG_PATH, "rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return None


def _load_config() -> None:
    """Load ~/.pr_impact/config.toml and populate os.environ with any values found."""
    config = _read_toml_config()
    if config is None:
        if CONFIG_PATH.exists():
            stderr.print(f"[bold red]Error:[/bold red] Could not parse config file {CONFIG_PATH}")
        return

    api_key = config.get("anthropic_api_key") or config.get("ANTHROPIC_API_KEY")
    if not api_key:
        return
    if os.environ.get("ANTHROPIC_API_KEY"):
        return

    expanded = os.path.expandvars(api_key)
    if expanded == api_key and ("%" in api_key or "$" in api_key):
        stderr.print(
            f"[bold red]Error:[/bold red] Config file references an environment variable "
            f"that is not set: {api_key}"
        )
        return
    os.environ["ANTHROPIC_API_KEY"] = expanded


def _env_placeholder(var: str) -> str:
    """Return the platform-appropriate env-var placeholder for config file hints."""
    return f"%{var}%" if sys.platform == "win32" else f"${var}"


def _stdin_is_interactive() -> bool:
    """Return True when stdin is an interactive terminal."""
    return sys.stdin.isatty()


def _warn_no_github_token() -> None:
    """Print a one-time warning that no GitHub token was found."""
    stderr.print(
        "[yellow]Warning:[/yellow] No GitHub token found. "
        "Set the [bold]GITHUB_TOKEN[/bold] environment variable in this terminal session, "
        f"or add [bold]github_token = \"{_env_placeholder('GITHUB_TOKEN')}\"[/bold] to "
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
    config = _read_toml_config()
    if config is None:
        return None
    raw = config.get("github_token") or config.get("GITHUB_TOKEN")
    if not raw:
        return None
    expanded = os.path.expandvars(str(raw))
    if expanded == raw and ("%" in raw or "$" in raw):
        return None
    return expanded or None


def _invert_graph(forward: dict[str, list[str]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = defaultdict(list)
    for src, targets in forward.items():
        for tgt in targets:
            reverse[tgt].append(src)
    return dict(reverse)


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


def _run_pipeline(
    repo: str,
    repo_obj: git.Repo,
    refs: RefsResult,
    max_depth: int,
    progress,
    check_osv: bool = False,
) -> tuple:
    """Execute all pipeline steps with an injected progress object.

    Returns (changed_files, blast_radius, interface_changes, ai_analysis, metadata, dependency_issues).
    Exits with code 1 on fatal git errors, code 0 when no supported files changed.
    The ``progress`` parameter accepts any object with add_task/update/remove_task
    methods; tests pass a MagicMock() to suppress spinner output.
    """
    # Ensure PR commits are present locally before diffing (no-op for up-to-date clones)
    if refs.fetch_pr_number is not None:
        try:
            ensure_commits_present(
                repo, refs.base, refs.head,
                refs.fetch_remote, refs.fetch_pr_number, refs.fetch_base_ref,
                repo=repo_obj,
            )
        except RuntimeError as e:
            stderr.print(f"[yellow]Warning:[/yellow] {e}. Continuing — diff will fail if commits are still absent.")

    # Step 1: get changed files
    task = progress.add_task("Extracting changed files...", total=None)
    try:
        changed_files = get_changed_files(repo, refs.base, refs.head, repo=repo_obj)
    except Exception as e:
        stderr.print(f"[bold red]Error:[/bold red] Could not read git repository: {e}")
        sys.exit(1)
    progress.update(task, description=f"Found {len(changed_files)} changed file(s)")

    if not changed_files:
        stderr.print("No supported source files changed between the two SHAs.")
        sys.exit(0)

    # Step 2: build import graph
    progress.update(task, description="Building import graph...")
    languages = list({f.language for f in changed_files})
    try:
        forward_graph = build_import_graph(repo, languages)
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] Import graph failed: {e}")
        forward_graph = {}
    reverse_graph = _invert_graph(forward_graph)

    # Step 3: blast radius
    progress.update(task, description="Calculating blast radius...")
    changed_paths = [f.path for f in changed_files]
    try:
        blast_radius = get_blast_radius(reverse_graph, changed_paths, max_depth, repo)
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] Blast radius calculation failed: {e}")
        blast_radius = []

    # Step 4: classify changed files
    progress.update(task, description="Classifying changes...")
    for f in changed_files:
        try:
            classify_changed_file(f)
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Classifier failed for {f.path}: {e}")

    # Step 5: interface changes
    try:
        interface_changes = get_interface_changes(changed_files, reverse_graph)
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] Interface change detection failed: {e}")
        interface_changes = []

    # Step 6: git churn for blast radius entries
    progress.update(task, description="Computing churn scores...")
    for entry in blast_radius:
        try:
            entry.churn_score = get_git_churn(repo, entry.path, repo=repo_obj)
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Churn score failed for {entry.path}: {e}")
            entry.churn_score = None

    # Security: pattern signal detection (deterministic, fast, no API)
    progress.update(task, description="Scanning for security signals...")
    try:
        pattern_signals = detect_pattern_signals(changed_files)
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] Security signal detection failed: {e}")
        pattern_signals = []

    try:
        dependency_issues = check_dependency_integrity(changed_files, osv_check=check_osv)
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] Dependency integrity check failed: {e}")
        dependency_issues = []

    # Metadata (best-effort)
    try:
        metadata = get_pr_metadata(repo, refs.base, refs.head)
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] PR metadata lookup failed: {e}")
        metadata = {}

    # Step 7: AI analysis (4 API calls when security signals present)
    call_count = 4 if pattern_signals else 3
    progress.update(task, description=f"Running AI analysis ({call_count} API calls)...")
    try:
        ai_analysis = run_ai_analysis(changed_files, blast_radius, repo, pattern_signals or None)
    except Exception as e:
        stderr.print(f"[yellow]Warning:[/yellow] AI analysis failed: {e}")
        ai_analysis = AIAnalysis()

    progress.remove_task(task)

    return changed_files, blast_radius, interface_changes, ai_analysis, metadata, dependency_issues


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
) -> None:
    """Analyse the impact of a code change between two commit SHAs or a GitHub PR."""
    if _stdin_is_interactive():
        _print_banner()
    _load_config()

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

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=Console(stderr=True),
        transient=True,
    ) as progress:
        changed_files, blast_radius, interface_changes, ai_analysis, metadata, dependency_issues = (
            _run_pipeline(repo, repo_obj, refs, max_depth, progress, check_osv=check_osv)
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
    )

    _write_outputs(report, output, json_output, sarif_output)
    render_terminal(report, Console(), output, json_output, sarif_output)

    if fail_on_severity != "none":
        threshold = _SEVERITY_ORDER[fail_on_severity]
        breaching = [
            a for a in report.ai_analysis.anomalies
            if _SEVERITY_ORDER.get(a.severity, 0) >= threshold
        ]
        if breaching:
            stderr.print(
                f"[bold red]Exiting 1:[/bold red] {len(breaching)} anomaly/anomalies "
                f"at or above --fail-on-severity={fail_on_severity}"
            )
            sys.exit(1)

    if run_verdict or verdict_output is not None:
        import dataclasses
        import json as _json
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=Console(stderr=True),
            transient=True,
        ) as progress:
            progress.add_task("Running verdict analysis (1 API call)...", total=None)
            try:
                verdict = run_verdict_analysis(report)
            except Exception as e:
                stderr.print(f"[yellow]Warning:[/yellow] Verdict analysis failed: {e}")
                sys.exit(0)

        render_verdict_terminal(verdict, Console())

        if verdict_output is not None:
            Path(verdict_output).write_text(
                _json.dumps(dataclasses.asdict(verdict), indent=2), encoding="utf-8"
            )
            stderr.print(f"[dim]Verdict written to[/dim] [cyan]{verdict_output}[/cyan]")

        if verdict.agent_should_continue:
            sys.exit(2)


if __name__ == "__main__":
    main()
