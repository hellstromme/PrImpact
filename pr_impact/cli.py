import os
import sys
from collections import defaultdict
from pathlib import Path

import click
import git
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .ai_layer import run_ai_analysis
from .classifier import classify_changed_file, get_interface_changes
from .dependency_graph import build_import_graph, get_blast_radius
from .git_analysis import get_changed_files, get_git_churn, get_pr_metadata
from .github import detect_github_remote, fetch_open_prs, fetch_pr
from .models import AIAnalysis, ImpactReport
from .reporter import render_json, render_markdown, render_terminal

stderr = Console(stderr=True)

CONFIG_PATH = Path.home() / ".pr_impact" / "config.toml"


def _load_config() -> None:
    """Load ~/.pr_impact/config.toml and populate os.environ with any values found."""
    if not CONFIG_PATH.exists():
        return
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            try:
                import tomllib  # type: ignore[no-redef]
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]
        with open(CONFIG_PATH, "rb") as fh:
            config = tomllib.load(fh)
    except Exception as e:
        stderr.print(f"[bold red]Error:[/bold red] Could not parse config file {CONFIG_PATH}: {e}")
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


def _get_github_token() -> str | None:
    """Return a GitHub token from env or config file, or None if absent."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
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
            config = tomllib.load(fh)
    except Exception:
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
@click.option(
    "--max-depth", default=3, show_default=True, help="Maximum BFS depth for blast radius"
)
def analyse(
    repo: str,
    pr_number: int | None,
    base: str | None,
    head: str | None,
    output: str | None,
    json_output: str | None,
    max_depth: int,
) -> None:
    """Analyse the impact of a code change between two commit SHAs or a GitHub PR."""
    _load_config()

    # --pr cannot be combined with --base or --head
    if pr_number is not None and (base is not None or head is not None):
        stderr.print(
            "[bold red]Error:[/bold red] --pr cannot be combined with --base or --head. "
            "When --pr is given, the PR provides both SHAs from GitHub."
        )
        sys.exit(1)

    # Direct SHA mode: apply head/base defaults now
    if pr_number is None and (base is not None or head is not None):
        if head is None:
            head = "HEAD"
        if base is None:
            base = f"{head}~1"

    # pr_title: set from GitHub data if using --pr or interactive mode
    pr_title: str | None = None

    # Open the repo early — needed for both PR resolution and the pipeline
    try:
        repo_obj = git.Repo(repo)
    except Exception as e:
        stderr.print(f"[bold red]Error:[/bold red] Could not open git repository: {e}")
        sys.exit(1)

    # GitHub PR resolution (runs before the progress spinner so interactive prompts work)
    if pr_number is not None or (base is None and head is None):
        github_token = _get_github_token()
        if github_token is None:
            stderr.print(
                "[yellow]Warning:[/yellow] No GitHub token found. "
                "Set the [bold]GITHUB_TOKEN[/bold] environment variable in this terminal session, "
                "or add [bold]github_token = \"%GITHUB_TOKEN%\"[/bold] to "
                f"[bold]{CONFIG_PATH}[/bold]. "
                "Unauthenticated requests will fail for private repositories."
            )
        owner_repo = detect_github_remote(repo_obj)
        if owner_repo is None:
            stderr.print(
                "[bold red]Error:[/bold red] Could not detect a GitHub remote in this repository. "
                "Use --base and --head to specify SHAs directly."
            )
            sys.exit(1)
        owner, repo_name = owner_repo

        if pr_number is not None:
            try:
                pr_data = fetch_pr(owner, repo_name, pr_number, github_token)
            except RuntimeError as e:
                stderr.print(f"[bold red]Error:[/bold red] {e}")
                sys.exit(1)
            base = pr_data["base"]["sha"]
            head = pr_data["head"]["sha"]
            pr_title = f"#{pr_number}: {pr_data.get('title') or f'PR {pr_number}'}"
        else:
            # Interactive: list open PRs and let the user pick
            try:
                open_prs = fetch_open_prs(owner, repo_name, github_token)
            except RuntimeError as e:
                stderr.print(f"[bold red]Error:[/bold red] Could not fetch open PRs: {e}")
                sys.exit(1)
            if not open_prs:
                stderr.print("[yellow]No open PRs found.[/yellow]")
                if not click.confirm(
                    "Analyse the last two commits (HEAD~1 → HEAD) instead?",
                    default=True,
                ):
                    sys.exit(0)
                base = "HEAD~1"
                head = "HEAD"
            else:
                _print_pr_table(open_prs)
                pr_numbers = {str(p["number"]) for p in open_prs}
                chosen = click.prompt("Enter PR number to analyse", prompt_suffix=" > ")
                if chosen not in pr_numbers:
                    stderr.print(
                        f"[bold red]Error:[/bold red] '{chosen}' is not a valid PR number from the list."
                    )
                    sys.exit(1)
                selected = next(p for p in open_prs if str(p["number"]) == chosen)
                base = selected["base"]["sha"]
                head = selected["head"]["sha"]
                selected_num = selected["number"]
                pr_title = f"#{selected_num}: {selected.get('title') or f'PR {selected_num}'}"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=Console(stderr=True),
        transient=True,
    ) as progress:
        # Step 1: get changed files
        task = progress.add_task("Extracting changed files...", total=None)
        try:
            changed_files = get_changed_files(repo, base, head, repo=repo_obj)
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
            entry.churn_score = get_git_churn(repo, entry.path, repo=repo_obj)

        # Metadata (best-effort)
        metadata = get_pr_metadata(repo, base, head)

        # Step 7: AI analysis
        progress.update(task, description="Running AI analysis (3 API calls)...")
        try:
            ai_analysis = run_ai_analysis(changed_files, blast_radius, repo)
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] AI analysis failed: {e}")
            ai_analysis = AIAnalysis()

        progress.remove_task(task)

    # Step 8: render report
    if pr_title is None:
        commits = metadata.get("commits", [])
        pr_title = commits[0].splitlines()[0] if commits else f"Changes {base[:7]}..{head[:7]}"

    report = ImpactReport(
        pr_title=pr_title,
        base_sha=base,
        head_sha=head,
        changed_files=changed_files,
        blast_radius=blast_radius,
        interface_changes=interface_changes,
        ai_analysis=ai_analysis,
    )

    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))

    if json_output:
        with open(json_output, "w", encoding="utf-8") as fh:
            fh.write(render_json(report))

    stdout_console = Console()
    render_terminal(report, stdout_console, output, json_output)


if __name__ == "__main__":
    main()
