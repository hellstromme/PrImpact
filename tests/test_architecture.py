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
    for pyfile in sorted(pkg.glob("*.py")):
        if pyfile.stem in exempt:
            continue
        source = pyfile.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
                imported_module = node.module or ""
                if imported_module and imported_module not in ("models", "prompts"):
                    violations.append(f"{pyfile.name} imports .{imported_module}")

    assert violations == [], "Cross-module imports detected:\n" + "\n".join(violations)
