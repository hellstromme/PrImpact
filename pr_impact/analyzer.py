"""ImpactAnalyzer — pipeline orchestration service.

Encapsulates the 8-step analysis pipeline so it can be invoked from any
entry point (CLI, web API, scheduled job) without coupling to Click or Rich.

This module is a helper called exclusively by cli.py.
"""

from collections import defaultdict

from rich.console import Console

from .ai_layer import run_ai_analysis
from .classifier import classify_changed_file, get_interface_changes
from .dependency_graph import build_import_graph, get_blast_radius
from .git_analysis import ensure_commits_present, get_changed_files, get_git_churn, get_pr_metadata
from .models import AIAnalysis, BlastRadiusEntry, ChangedFile, DependencyIssue, InterfaceChange, RefsResult
from .security import check_dependency_integrity, detect_pattern_signals

import git

stderr = Console(stderr=True)


class AnalyzerExit(Exception):
    """Raised by ImpactAnalyzer.run() to signal a clean or fatal pipeline exit.

    Callers (e.g. cli.py) catch this, print ``exc.message`` to stderr if
    non-empty, and call ``sys.exit(exc.code)``.
    """

    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _invert_graph(forward: dict[str, list[str]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = defaultdict(list)
    for src, targets in forward.items():
        for tgt in targets:
            reverse[tgt].append(src)
    return dict(reverse)


class ImpactAnalyzer:
    """Orchestrates the PrImpact analysis pipeline.

    Accepts all pipeline parameters at construction time so that the same
    instance can be reused or serialised, and a separate entry point (e.g. a
    web handler) can construct it without any CLI concerns.
    """

    def __init__(
        self,
        repo: str,
        repo_obj: git.Repo,
        refs: RefsResult,
        *,
        max_depth: int = 3,
        check_osv: bool = False,
        anomaly_history: list[dict] | None = None,
        hotspots: list[dict] | None = None,
    ) -> None:
        self.repo = repo
        self.repo_obj = repo_obj
        self.refs = refs
        self.max_depth = max_depth
        self.check_osv = check_osv
        self.anomaly_history = anomaly_history
        self.hotspots = hotspots

    def run(
        self, progress: object
    ) -> tuple[list[ChangedFile], list[BlastRadiusEntry], list[InterfaceChange], AIAnalysis, dict, list[DependencyIssue]]:
        """Execute all pipeline steps with an injected progress object.

        Returns (changed_files, blast_radius, interface_changes, ai_analysis,
        metadata, dependency_issues).
        Exits with code 1 on fatal git errors, code 0 when no supported files
        changed.

        The ``progress`` parameter accepts any object with add_task/update/
        remove_task methods; tests pass a MagicMock() to suppress spinner output.
        """
        refs = self.refs

        # Ensure PR commits are present locally before diffing (no-op for up-to-date clones)
        if refs.fetch_pr_number is not None:
            try:
                ensure_commits_present(
                    self.repo, refs.base, refs.head,
                    refs.fetch_remote, refs.fetch_pr_number, refs.fetch_base_ref,
                    repo=self.repo_obj,
                )
            except RuntimeError as e:
                stderr.print(f"[yellow]Warning:[/yellow] {e}. Continuing — diff will fail if commits are still absent.")

        # Step 1: get changed files
        task = progress.add_task("Extracting changed files...", total=None)
        try:
            changed_files = get_changed_files(self.repo, refs.base, refs.head, repo=self.repo_obj)
        except Exception as e:
            raise AnalyzerExit(1, f"Could not read git repository: {e}")
        progress.update(task, description=f"Found {len(changed_files)} changed file(s)")

        if not changed_files:
            raise AnalyzerExit(0, "No supported source files changed between the two SHAs.")

        # Step 2: build import graph
        progress.update(task, description="Building import graph...")
        languages = list({f.language for f in changed_files})
        try:
            forward_graph = build_import_graph(self.repo, languages)
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Import graph failed: {e}")
            forward_graph = {}
        reverse_graph = _invert_graph(forward_graph)

        # Step 3: blast radius — BFS depth capped at 3 per architecture constraint
        progress.update(task, description="Calculating blast radius...")
        changed_paths = [f.path for f in changed_files]
        effective_depth = min(self.max_depth, 3)
        try:
            blast_radius = get_blast_radius(reverse_graph, changed_paths, effective_depth, self.repo)
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Blast radius calculation failed: {e}")
            blast_radius = []

        # Step 4: classify changed files
        progress.update(task, description="Classifying changes...")
        for f in changed_files:
            try:
                f.changed_symbols = classify_changed_file(f)
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
                entry.churn_score = get_git_churn(self.repo, entry.path, repo=self.repo_obj)
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
            dependency_issues = check_dependency_integrity(changed_files, osv_check=self.check_osv)
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] Dependency integrity check failed: {e}")
            dependency_issues = []

        # Metadata (best-effort)
        try:
            metadata = get_pr_metadata(self.repo, refs.base, refs.head)
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] PR metadata lookup failed: {e}")
            metadata = {}

        # Step 7: AI analysis (up to 5 API calls when all features active)
        call_count = 4 if pattern_signals else 3
        progress.update(task, description=f"Running AI analysis ({call_count}+ API calls)...")
        try:
            ai_analysis = run_ai_analysis(
                changed_files, blast_radius, self.repo,
                pattern_signals or None,
                anomaly_history=self.anomaly_history,
                hotspots=self.hotspots,
            )
        except Exception as e:
            stderr.print(f"[yellow]Warning:[/yellow] AI analysis failed: {e}")
            ai_analysis = AIAnalysis()

        progress.remove_task(task)

        return changed_files, blast_radius, interface_changes, ai_analysis, metadata, dependency_issues
