"""Configuration loading for PrImpact.

Reads ~/.pr_impact/config.toml and populates environment variables.
Called only by cli.py.
"""

import os
import sys
from pathlib import Path

from rich.console import Console

_stderr = Console(stderr=True)

CONFIG_PATH = Path.home() / ".pr_impact" / "config.toml"


def read_toml_config() -> dict | None:
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


def load_config() -> None:
    """Load ~/.pr_impact/config.toml and populate os.environ with any values found."""
    config = read_toml_config()
    if config is None:
        if CONFIG_PATH.exists():
            _stderr.print(f"[bold red]Error:[/bold red] Could not parse config file {CONFIG_PATH}")
        return

    api_key = config.get("anthropic_api_key") or config.get("ANTHROPIC_API_KEY")
    if not api_key:
        return
    if os.environ.get("ANTHROPIC_API_KEY"):
        return

    expanded = os.path.expandvars(api_key)
    if expanded == api_key and ("%" in api_key or "$" in api_key):
        _stderr.print(
            f"[bold red]Error:[/bold red] Config file references an environment variable "
            f"that is not set: {api_key}"
        )
        return
    os.environ["ANTHROPIC_API_KEY"] = expanded


def env_placeholder(var: str) -> str:
    """Return the platform-appropriate env-var placeholder for config file hints."""
    return f"%{var}%" if sys.platform == "win32" else f"${var}"
