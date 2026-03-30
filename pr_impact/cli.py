import sys
from collections import defaultdict

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .classifier import classify_changed_file, get_interface_changes
from .dependency_graph import build_import_graph, get_blast_radius
from .git_analysis import get_changed_files, get_git_churn, get_pr_metadata

stderr = Console(stderr=True)


def _invert_graph(forward: dict[str, list[str]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = defaultdict(list)
    for src, targets in forward.items():
        for tgt in targets:
            reverse[tgt].append(src)
    return dict(reverse)


@click.group()
def main():
    pass


@main.command()
@click.option("--repo", required=True, help="Path to the local git repository")
@click.option("--base", required=True, help="Base commit SHA")
@click.option("--head", required=True, help="Head commit SHA")
@click.option("--output", default=None, help="Write Markdown report to this file")
@click.option("--json", "json_output", default=None, help="Write JSON sidecar to this file")
@click.option("--max-depth", default=3, show_default=True, help="Maximum BFS depth for blast radius")
def analyse(repo: str, base: str, head: str, output: str | None, json_output: str | None, max_depth: int):
    """Analyse the impact of a code change between two commit SHAs."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=Console(stderr=True),
        transient=True,
    ) as progress:

        # Step 1: get changed files
        task = progress.add_task("Extracting changed files...", total=None)
        try:
            changed_files = get_changed_files(repo, base, head)
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
            entry.churn_score = get_git_churn(repo, entry.path)

        # Metadata (best-effort)
        metadata = get_pr_metadata(repo, base, head)
        progress.remove_task(task)

    # --- Summary output to stderr ---
    stderr.print(f"\n[bold]PrImpact analysis complete[/bold]")
    stderr.print(f"  Changed files  : {len(changed_files)}")
    stderr.print(f"  Blast radius   : {len(blast_radius)} downstream file(s)")
    stderr.print(f"  Interface Δ    : {len(interface_changes)} changed/removed symbol(s)")

    interface_types: dict[str, int] = {}
    for f in changed_files:
        for sym in f.changed_symbols:
            interface_types[sym.change_type] = interface_types.get(sym.change_type, 0) + 1
    if interface_types:
        stderr.print("  Symbol changes :")
        for ct, count in sorted(interface_types.items()):
            stderr.print(f"    {ct}: {count}")

    if metadata.get("authors"):
        stderr.print(f"  Authors        : {', '.join(metadata['authors'])}")

    stderr.print("\n[dim](AI analysis and report rendering not yet implemented — Part 2)[/dim]")
