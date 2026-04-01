import re

from .models import ChangedFile, ChangedSymbol, InterfaceChange

# --- Signature extraction patterns ---

_PY_FUNC = re.compile(
    r"^(?P<indent>[ \t]*)(?P<async>async\s+)?def\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)(?P<ret>[^:]*)?:",
    re.MULTILINE,
)
_PY_CLASS = re.compile(
    r"^(?P<indent>[ \t]*)class\s+(?P<name>\w+)(?P<bases>[^:]*):",
    re.MULTILINE,
)

_TS_FUNC = re.compile(
    r"^(?P<export>export\s+)?(?P<default>default\s+)?(?P<async>async\s+)?function\s+(?P<name>\w+)\s*(?P<generics><[^>]*>)?\s*\((?P<params>[^)]*)\)(?P<ret>[^{;]*)?",
    re.MULTILINE,
)
_TS_CLASS = re.compile(
    r"^(?P<export>export\s+)?(?P<abstract>abstract\s+)?class\s+(?P<name>\w+)(?P<rest>[^{]*)\{",
    re.MULTILINE,
)
_TS_ARROW = re.compile(
    r"^(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*(?::\s*[^=]+)?\s*=\s*(?P<async>async\s+)?\(",
    re.MULTILINE,
)

_CS_CLASS = re.compile(
    r"^[ \t]*(?:(?P<access>public|protected|private|internal|file)\s+)?(?:(?:abstract|sealed|static|partial|new)\s+)*class\s+(?P<name>\w+)(?P<rest>[^{]*)\{",
    re.MULTILINE,
)
_CS_INTERFACE = re.compile(
    r"^[ \t]*(?:(?P<access>public|protected|private|internal|file)\s+)?(?:partial\s+)?interface\s+(?P<name>\w+)(?P<rest>[^{]*)\{",
    re.MULTILINE,
)
_CS_RECORD = re.compile(
    r"^[ \t]*(?:(?P<access>public|protected|private|internal|file)\s+)?(?:(?:abstract|sealed|partial|new)\s+)*record\s+(?P<name>\w+)(?P<rest>[^{;]*)[{;]",
    re.MULTILINE,
)
_CS_STRUCT = re.compile(
    r"^[ \t]*(?:(?P<access>public|protected|private|internal|file)\s+)?(?:(?:readonly|ref|partial|new)\s+)*struct\s+(?P<name>\w+)(?P<rest>[^{]*)\{",
    re.MULTILINE,
)
_CS_METHOD = re.compile(
    r"^[ \t]*(?P<access>public|protected|internal)(?:\s+(?:abstract|virtual|override|sealed|static|async|new|extern))*\s+\S.*?\s+(?P<name>\w+)\s*(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)

_PY_IMPORT_LINE = re.compile(r"^(?:import\s+|from\s+)", re.MULTILINE)
_JS_IMPORT_LINE = re.compile(r"^(?:import\s+|const\s+\w+\s*=\s*require)", re.MULTILINE)
_CS_IMPORT_LINE = re.compile(r"^using\s+", re.MULTILINE)

_DIFF_ADDED = re.compile(r"^\+(?!\+\+)(.*)$", re.MULTILINE)
_DIFF_REMOVED = re.compile(r"^-(?!--)(.*)$", re.MULTILINE)
# Matches "class " at start of signature or after a prefix keyword like "export "/"abstract "
_KIND_CLASS = re.compile(r"(?:^|\s)class\s")


def _extract_python_defs(content: str) -> dict[str, str]:
    """Return {name: full_signature_line} for top-level Python functions and classes."""
    defs: dict[str, str] = {}
    for m in _PY_FUNC.finditer(content):
        if m.group("indent") == "":
            sig = (
                ("async " if m.group("async") else "")
                + f"def {m.group('name')}({m.group('params')})"
                + (m.group("ret") or "")
            ).strip()
            defs[m.group("name")] = sig
    for m in _PY_CLASS.finditer(content):
        if m.group("indent") == "":
            sig = f"class {m.group('name')}{m.group('bases')}".strip()
            defs[m.group("name")] = sig
    return defs


def _extract_ts_defs(content: str) -> dict[str, str]:
    """Return {name: full_signature_line} for exported TS/JS functions and classes."""
    defs: dict[str, str] = {}
    for m in _TS_FUNC.finditer(content):
        name = m.group("name")
        sig = (
            ("export " if m.group("export") else "")
            + ("async " if m.group("async") else "")
            + f"function {name}({m.group('params')})"
            + (m.group("ret") or "")
        ).strip()
        defs[name] = sig
    for m in _TS_CLASS.finditer(content):
        name = m.group("name")
        sig = (
            ("export " if m.group("export") else "")
            + ("abstract " if m.group("abstract") else "")
            + f"class {name}{m.group('rest')}"
        ).strip()
        defs[name] = sig
    for m in _TS_ARROW.finditer(content):
        name = m.group("name")
        if m.group("export"):
            defs[name] = m.group(0).strip()
    return defs


def _is_exported_python(name: str, content: str) -> bool:
    """Heuristic: not prefixed with _ and appears in __all__ if defined, else true."""
    if name.startswith("_"):
        return False
    all_match = re.search(r"__all__\s*=\s*\[([^\]]*)\]", content)
    if all_match:
        return name in all_match.group(1)
    return True


def _is_exported_ts(name: str, content: str) -> bool:
    return bool(re.search(rf"\bexport\b[^;{{]*\b{re.escape(name)}\b", content))


def _extract_csharp_defs(content: str) -> dict[str, str]:
    """Return {name: signature} for C# types and public/protected/internal methods.

    All keys are the bare name. For overloaded methods the last definition wins,
    which is consistent with how Python and TypeScript are handled and avoids
    treating a parameter-list change as a remove + add pair.
    Signatures include the access modifier so _is_exported_csharp can inspect them.
    """
    defs: dict[str, str] = {}
    for pattern, kind_word in (
        (_CS_CLASS, "class"),
        (_CS_INTERFACE, "interface"),
        (_CS_RECORD, "record"),
        (_CS_STRUCT, "struct"),
    ):
        for m in pattern.finditer(content):
            name = m.group("name")
            access = (m.group("access") + " ") if m.group("access") else ""
            defs[name] = f"{access}{kind_word} {name}{m.group('rest')}".strip()
    for m in _CS_METHOD.finditer(content):
        name = m.group("name")
        defs[name] = m.group(0).strip()
    return defs


def _is_exported_csharp(sig: str) -> bool:
    """Return True if the captured signature is declared with public access."""
    return sig.startswith("public ")


def _extract_import_lines(content: str, language: str) -> set[str]:
    if language == "python":
        return {
            line.strip() for line in content.splitlines() if _PY_IMPORT_LINE.match(line.strip())
        }
    elif language in ("javascript", "typescript"):
        return {
            line.strip() for line in content.splitlines() if _JS_IMPORT_LINE.match(line.strip())
        }
    elif language == "csharp":
        return {
            line.strip() for line in content.splitlines() if _CS_IMPORT_LINE.match(line.strip())
        }
    return set()


def _names_touched_in_diff(diff: str) -> set[str]:
    """Collect symbol names that appear on added or removed diff lines."""
    names: set[str] = set()
    for m in _DIFF_ADDED.finditer(diff):
        names.update(re.findall(r"\b(\w+)\b", m.group(1)))
    for m in _DIFF_REMOVED.finditer(diff):
        names.update(re.findall(r"\b(\w+)\b", m.group(1)))
    return names


def classify_changed_file(file: ChangedFile) -> list[ChangedSymbol]:
    symbols: list[ChangedSymbol] = []

    # --- File-level cases ---
    if not file.content_before and not file.content_after:
        return symbols

    if not file.content_before:
        symbols.append(
            ChangedSymbol(
                name=file.path,
                kind="file",
                change_type="new_file",
                signature_before=None,
                signature_after=None,
            )
        )
        file.changed_symbols = symbols
        return symbols

    if not file.content_after:
        symbols.append(
            ChangedSymbol(
                name=file.path,
                kind="file",
                change_type="deleted_file",
                signature_before=None,
                signature_after=None,
            )
        )
        file.changed_symbols = symbols
        return symbols

    # --- Extract definitions ---
    if file.language == "python":
        defs_before = _extract_python_defs(file.content_before)
        defs_after = _extract_python_defs(file.content_after)
        _exported = _is_exported_python
    elif file.language in ("javascript", "typescript"):
        defs_before = _extract_ts_defs(file.content_before)
        defs_after = _extract_ts_defs(file.content_after)
        _exported = _is_exported_ts
    else:
        file.changed_symbols = symbols
        return symbols

    touched = _names_touched_in_diff(file.diff)
    all_names = set(defs_before) | set(defs_after)

    for name in all_names:
        if name not in touched:
            continue

        sig_before = defs_before.get(name)
        sig_after = defs_after.get(name)
        exported_before = _exported(name, file.content_before)
        exported_after = _exported(name, file.content_after)

        if sig_before is None and sig_after is not None:
            change_type = "interface_added" if exported_after else "internal"
        elif sig_before is not None and sig_after is None:
            change_type = "interface_removed" if exported_before else "internal"
        elif sig_before != sig_after:
            change_type = "interface_changed" if (exported_before or exported_after) else "internal"
        else:
            change_type = "internal"

        kind = "function"
        sig = sig_before or sig_after or ""
        if _KIND_CLASS.search(sig):
            kind = "class"

        symbols.append(
            ChangedSymbol(
                name=name,
                kind=kind,
                change_type=change_type,
                signature_before=sig_before,
                signature_after=sig_after,
            )
        )

    # --- Dependency changes ---
    imports_before = _extract_import_lines(file.content_before, file.language)
    imports_after = _extract_import_lines(file.content_after, file.language)

    for line in imports_after - imports_before:
        symbols.append(
            ChangedSymbol(
                name=line[:80],
                kind="import",
                change_type="dependency_added",
                signature_before=None,
                signature_after=line,
            )
        )
    for line in imports_before - imports_after:
        symbols.append(
            ChangedSymbol(
                name=line[:80],
                kind="import",
                change_type="dependency_removed",
                signature_before=line,
                signature_after=None,
            )
        )

    file.changed_symbols = symbols
    return symbols


def get_interface_changes(
    changed_files: list[ChangedFile],
    reverse_graph: dict[str, list[str]],
) -> list[InterfaceChange]:
    interface_change_types = {"interface_changed", "interface_removed"}
    results: list[InterfaceChange] = []

    for file in changed_files:
        callers = reverse_graph.get(file.path, [])
        for sym in file.changed_symbols:
            if sym.change_type in interface_change_types:
                results.append(
                    InterfaceChange(
                        file=file.path,
                        symbol=sym.name,
                        before=sym.signature_before or "",
                        after=sym.signature_after or "",
                        callers=callers,
                    )
                )

    return results
