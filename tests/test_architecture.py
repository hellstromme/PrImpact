"""Structural tests that enforce package-level architecture constraints."""

import ast
from pathlib import Path


def test_pipeline_modules_only_import_models_and_prompts():
    """No pipeline module may import another pipeline module.

    Only models.py and prompts.py (pure-data modules) are shared across the
    package. cli.py is the sole orchestrator and is exempt from this rule.
    """
    pkg = Path(__file__).parent.parent / "pr_impact"
    # Exempt: cli (orchestrator), models/prompts (shared data), __init__
    exempt = {"__init__", "models", "prompts", "cli"}

    violations: list[str] = []
    for pyfile in sorted(pkg.rglob("*.py")):
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
                if imported_module not in ("models", "prompts"):
                    violations.append(f"{pyfile.name} imports {imported_module}")

    assert violations == [], "Cross-module imports detected:\n" + "\n".join(violations)
