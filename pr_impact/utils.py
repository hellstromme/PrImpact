"""Shared I/O primitives used by multiple pipeline helper modules.

Importable by any module in the package (shared module, like models.py).
"""


def read_file_safe(path: str) -> str:
    """Read a file by absolute path with UTF-8 encoding; return '' on any error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""  # callers must handle empty string
