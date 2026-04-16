"""Structural tests that enforce package-level architecture constraints."""

import ast
from pathlib import Path

_PKG_DIR = Path(__file__).parent.parent / "pr_impact"


def test_pipeline_modules_do_not_cross_import():
    """No pipeline module may import another pipeline module.

    Shared modules exempt from this rule: models, prompts (pure data),
    ast_extractor and history (shared utilities), cli (orchestrator), __init__.
    All other pipeline modules must only import from that exempt set.
    """
    pkg = _PKG_DIR
    # Exempt: cli/analyzer (orchestrator layer), models/prompts (shared data),
    # ast_extractor/history (shared utilities), __init__
    exempt = {
        "__init__", "models", "prompts", "cli", "analyzer", "ast_extractor", "history",
    }

    web_pkg = pkg / "web"
    violations: list[str] = []
    for pyfile in sorted(pkg.rglob("*.py")):
        # The web/ subpackage has its own internal import structure (routers importing
        # each other) and is not a pipeline module — exempt it specifically.
        # All other subpackages remain subject to the cross-import rule.
        if pyfile.is_relative_to(web_pkg):
            continue
        if pyfile.stem in exempt:
            continue
        source = pyfile.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            imported_modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    # relative import: "from .foo import bar" or "from . import foo"
                    if node.module:
                        imported_modules = [node.module.split(".", 1)[0]]
                    else:
                        imported_modules = [alias.name.split(".", 1)[0] for alias in node.names]
                elif node.module and node.module.startswith("pr_impact."):
                    # absolute intra-package: "from pr_impact.foo import bar"
                    parts = node.module.split(".")
                    if len(parts) >= 2:
                        imported_modules = [parts[1]]
            elif isinstance(node, ast.Import):
                # absolute: "import pr_impact.foo"
                imported_modules = [
                    alias.name.split(".")[1]
                    for alias in node.names
                    if alias.name.startswith("pr_impact.") and len(alias.name.split(".")) >= 2
                ]
            for imported_module in imported_modules:
                if imported_module not in (
                    "models", "prompts", "ast_extractor", "history", "utils",
                    # Helper modules — each called by exactly one pipeline module:
                    "ai_client", "ai_context",       # called by ai_layer only
                    "language_resolvers",             # called by dependency_graph only
                    "config",                         # called by cli only
                    "config_file",                   # called by cli only
                ):
                    violations.append(f"{pyfile.name} imports {imported_module}")

    assert violations == [], "Cross-module imports detected:\n" + "\n".join(violations)


def test_pipeline_modules_do_not_write_to_stdout():
    """Pipeline modules must not call print() or sys.stdout.write() -- output goes to stderr."""
    pkg = Path(__file__).parent.parent / "pr_impact"

    pipeline_modules = [
        "git_analysis.py",
        "dependency_graph.py",
        "classifier.py",
        "ai_layer.py",
        "security.py",
    ]
    violations: list[str] = []

    for module_name in pipeline_modules:
        path = pkg / module_name
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check for print() calls that do NOT redirect to stderr
                if isinstance(node.func, ast.Name) and node.func.id == "print":
                    # Allow print(..., file=sys.stderr) — that's the correct stderr pattern
                    file_kw = next((kw for kw in node.keywords if kw.arg == "file"), None)
                    redirects_to_stderr = (
                        file_kw is not None
                        and isinstance(file_kw.value, ast.Attribute)
                        and file_kw.value.attr == "stderr"
                    )
                    if not redirects_to_stderr:
                        violations.append(f"{module_name}:{node.lineno}: print() call")
                # Check for sys.stdout.write() calls
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "write"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "stdout"
                ):
                    violations.append(
                        f"{module_name}:{node.lineno}: sys.stdout.write()"
                    )

    assert violations == [], (
        "Pipeline modules must not write to stdout:\n" + "\n".join(violations)
    )
