"""AST-based import and symbol extraction using tree-sitter.

All public functions return None on any parse or import failure, allowing
callers to fall back to regex-based extraction transparently.

Languages supported: python, typescript, javascript, java, go, ruby, csharp.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Optional tree-sitter dependencies — all imported at module level so the
# project's "no inline imports" convention is respected. Each grammar package
# is optional; missing ones silently remain None and trigger regex fallback.
try:
    from tree_sitter import Language as _TSLanguage
    from tree_sitter import Parser as _TSParser
    _TREE_SITTER_AVAILABLE = True
except ImportError:
    _TSLanguage = None  # type: ignore[assignment,misc]
    _TSParser = None  # type: ignore[assignment]
    _TREE_SITTER_AVAILABLE = False

try:
    import tree_sitter_python as _ts_py
except ImportError:
    _ts_py = None  # type: ignore[assignment]

try:
    import tree_sitter_typescript as _ts_typescript
except ImportError:
    _ts_typescript = None  # type: ignore[assignment]

try:
    import tree_sitter_javascript as _ts_js
except ImportError:
    _ts_js = None  # type: ignore[assignment]

try:
    import tree_sitter_java as _ts_java
except ImportError:
    _ts_java = None  # type: ignore[assignment]

try:
    import tree_sitter_go as _ts_go
except ImportError:
    _ts_go = None  # type: ignore[assignment]

try:
    import tree_sitter_ruby as _ts_ruby
except ImportError:
    _ts_ruby = None  # type: ignore[assignment]

try:
    import tree_sitter_c_sharp as _ts_csharp
except ImportError:
    _ts_csharp = None  # type: ignore[assignment]

# --- Data models ---


@dataclass
class ASTImport:
    """A single import statement parsed from source."""

    specifier: str  # Raw module/path string (e.g. ".models", "./utils", "com.example.Foo")
    imported_names: list[str] = field(default_factory=list)  # Named symbols; empty = wildcard/star/bare
    is_reexport: bool = False  # True for TS/JS `export { X } from '...'`


@dataclass
class ASTSymbol:
    """A top-level (or class-member) symbol extracted from source."""

    name: str
    kind: str  # "function" | "class" | "method"
    params: list[str] = field(default_factory=list)  # Parameter text strings (e.g. ["x: int", "y = 1"])
    return_type: str | None = None
    decorators: list[str] = field(default_factory=list)
    is_exported: bool = False
    line: int = 0  # 1-based
    # Full declaration header (without body), used for signature comparison in classifier
    signature: str = ""
    # Containing class name (populated for methods); empty string for top-level symbols
    container: str = ""


# --- Language grammar loader ---

_PARSERS: dict[str, object] = {}  # Cached Parser objects per language


def _get_parser(language: str):  # type: ignore[return]
    """Return a cached tree-sitter Parser for the given language, or None on failure."""
    if language in _PARSERS:
        return _PARSERS[language]

    if not _TREE_SITTER_AVAILABLE:
        _PARSERS[language] = None
        return None

    try:
        lang_obj: object | None = None

        if language == "python" and _ts_py is not None:
            lang_obj = _TSLanguage(_ts_py.language())
        elif language == "typescript" and _ts_typescript is not None:
            lang_obj = _TSLanguage(_ts_typescript.language_typescript())
        elif language == "javascript" and _ts_js is not None:
            lang_obj = _TSLanguage(_ts_js.language())
        elif language == "java" and _ts_java is not None:
            lang_obj = _TSLanguage(_ts_java.language())
        elif language == "go" and _ts_go is not None:
            lang_obj = _TSLanguage(_ts_go.language())
        elif language == "ruby" and _ts_ruby is not None:
            lang_obj = _TSLanguage(_ts_ruby.language())
        elif language == "csharp" and _ts_csharp is not None:
            lang_obj = _TSLanguage(_ts_csharp.language())

        if lang_obj is None:
            _PARSERS[language] = None
            return None

        parser = _TSParser(lang_obj)
        _PARSERS[language] = parser
        return parser

    except Exception:
        _PARSERS[language] = None
        return None


def _parse(source: str, language: str):
    """Parse source with tree-sitter, returning (tree, root_node) or (None, None)."""
    parser = _get_parser(language)
    if parser is None:
        return None, None
    try:
        tree = parser.parse(source.encode("utf-8", errors="replace"))
        return tree, tree.root_node
    except Exception:
        return None, None


# --- Helpers ---


def _node_text(node) -> str:
    """Decode a tree-sitter node's byte text to str."""
    try:
        return node.text.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _child_by_type(node, *types: str):
    """Return the first child whose type is in *types, or None."""
    for child in node.children:
        if child.type in types:
            return child
    return None


def _children_by_type(node, *types: str) -> list:
    return [c for c in node.children if c.type in types]


def _unquote(s: str) -> str:
    """Strip surrounding single or double quotes from a string."""
    s = s.strip()
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        return s[1:-1]
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Python
# ─────────────────────────────────────────────────────────────────────────────


def _py_imports(root) -> list[ASTImport]:
    """Python import extraction using direct node text parsing.

    Walks all descendants (not just top-level children) so imports inside
    ``if TYPE_CHECKING:``, ``try/except ImportError``, and similar blocks are
    also captured.
    """
    results: list[ASTImport] = []

    def _walk(node) -> None:
        if node.type == "import_statement":
            # e.g. `import os` or `import json as _json`
            for child in node.children:
                if child.type == "dotted_name":
                    results.append(ASTImport(specifier=_node_text(child), imported_names=[]))
                elif child.type == "aliased_import":
                    dn = _child_by_type(child, "dotted_name")
                    if dn:
                        results.append(ASTImport(specifier=_node_text(dn), imported_names=[]))

        elif node.type == "import_from_statement":
            # Reconstruct specifier from raw text.
            # e.g. "from .models import Foo, Bar" → specifier=".models", names=["Foo","Bar"]
            # e.g. "from foo.bar import *" → specifier="foo.bar", names=[]
            raw = _node_text(node)
            text_after_from = raw[5:]  # strip "from "
            if " import " not in text_after_from:
                return
            module_part, _, names_part = text_after_from.partition(" import ")
            specifier = module_part.strip()
            names_raw = names_part.strip().strip("()")
            if names_raw == "*":
                names: list[str] = []
            else:
                names = [
                    n.strip().split(" as ")[0].strip()
                    for n in names_raw.split(",")
                    if n.strip() and n.strip() not in ("(", ")")
                ]
            results.append(ASTImport(specifier=specifier, imported_names=names))

        else:
            # Recurse into block-level nodes (if, try, with, etc.) so imports
            # inside ``if TYPE_CHECKING:`` or ``try/except ImportError:`` are found.
            for child in node.children:
                _walk(child)

    for child in root.children:
        _walk(child)
    return results


def _py_symbols(root) -> list[ASTSymbol]:
    results: list[ASTSymbol] = []
    # Collect decorators for the next def/class
    pending_decorators: list[str] = []

    for node in root.children:
        if node.type == "decorated_definition":
            decs = [_node_text(d) for d in node.children if d.type == "decorator"]
            inner = _child_by_type(node, "function_definition", "class_definition")
            if inner:
                sym = _py_extract_def(inner, is_exported=True, decorators=decs)
                if sym:
                    results.append(sym)
        elif node.type == "function_definition":
            sym = _py_extract_def(node, is_exported=True, decorators=[])
            if sym:
                results.append(sym)
        elif node.type == "class_definition":
            sym = _py_extract_def(node, is_exported=True, decorators=[])
            if sym:
                results.append(sym)

    return results


def _node_header(node) -> str:
    """Return the first line of a node's text (the declaration header, without body)."""
    try:
        full = node.text.decode("utf-8", errors="replace")
        return full.split("\n")[0].strip()
    except Exception:
        return ""


def _py_extract_def(node, is_exported: bool, decorators: list[str]) -> ASTSymbol | None:
    name_node = _child_by_type(node, "identifier")
    if not name_node:
        return None
    name = _node_text(name_node)

    if node.type == "function_definition":
        params = _py_params(node)
        ret_node = None
        # Return type comes after "->"
        found_arrow = False
        for child in node.children:
            if child.type == "->":
                found_arrow = True
            elif found_arrow and child.type == "type":
                ret_node = child
                break
        param_str = ", ".join(params)
        ret = f" -> {_node_text(ret_node)}" if ret_node else ""
        sig = f"def {name}({param_str}){ret}"
        return ASTSymbol(
            name=name,
            kind="function",
            params=params,
            return_type=_node_text(ret_node) if ret_node else None,
            decorators=decorators,
            is_exported=not name.startswith("_"),
            line=node.start_point[0] + 1,
            signature=sig,
        )
    else:
        # Class: capture full header (class Name(bases):)
        sig = _node_header(node).rstrip(":")
        return ASTSymbol(
            name=name,
            kind="class",
            decorators=decorators,
            is_exported=not name.startswith("_"),
            line=node.start_point[0] + 1,
            signature=sig,
        )


def _py_params(func_node) -> list[str]:
    params_node = _child_by_type(func_node, "parameters")
    if not params_node:
        return []
    param_types = {
        "identifier", "typed_parameter", "typed_default_parameter",
        "default_parameter", "list_splat_pattern", "dictionary_splat_pattern",
        "keyword_separator", "positional_separator",
    }
    return [
        _node_text(c) for c in params_node.children
        if c.type in param_types
    ]


# ─────────────────────────────────────────────────────────────────────────────
# TypeScript / JavaScript
# ─────────────────────────────────────────────────────────────────────────────


def _ts_imports(root) -> list[ASTImport]:
    results: list[ASTImport] = []

    for node in root.children:
        if node.type == "import_statement":
            # import { Foo, Bar } from './models'
            # import type { Foo } from './models'
            # import './side-effect'
            specifier = _ts_string_specifier(node)
            if specifier is None:
                continue
            names = _ts_named_imports(node)
            results.append(ASTImport(specifier=specifier, imported_names=names, is_reexport=False))

        elif node.type == "export_statement":
            # export { Foo } from './other'  ← re-export
            # export * from './other'         ← star re-export
            specifier = _ts_string_specifier(node)
            if specifier is None:
                continue
            names = _ts_named_imports(node)
            results.append(ASTImport(specifier=specifier, imported_names=names, is_reexport=True))

    # CommonJS: require('./path') — walk entire tree to find call_expression nodes
    _ts_collect_requires(root, results)
    return results


def _ts_collect_requires(node, results: list[ASTImport]) -> None:
    """Recursively walk AST to find require() call expressions."""
    if node.type == "call_expression":
        fn = node.children[0] if node.children else None
        if fn and fn.type == "identifier" and _node_text(fn) == "require":
            # Arguments node: the first argument is the string path
            args = _child_by_type(node, "arguments")
            if args:
                str_node = _child_by_type(args, "string")
                if str_node:
                    # Get string_fragment or fall back to unquoting full text
                    frag = _child_by_type(str_node, "string_fragment")
                    specifier = _node_text(frag) if frag else _unquote(_node_text(str_node))
                    if specifier:
                        results.append(ASTImport(specifier=specifier, imported_names=[]))
    for child in node.children:
        _ts_collect_requires(child, results)


def _ts_string_specifier(node) -> str | None:
    """Extract the string import path from a TS import/export node."""
    for child in node.children:
        if child.type == "string":
            return _unquote(_node_text(child))
    return None


def _ts_named_imports(node) -> list[str]:
    names: list[str] = []

    def _collect_specifiers(n) -> None:
        for child in n.children:
            if child.type in ("import_specifier", "export_specifier"):
                ident = _child_by_type(child, "identifier")
                if ident:
                    names.append(_node_text(ident))
            elif child.type in ("import_clause", "named_imports", "export_clause", "named_exports"):
                _collect_specifiers(child)

    _collect_specifiers(node)
    return names


def _ts_symbols(root) -> list[ASTSymbol]:
    results: list[ASTSymbol] = []

    for node in root.children:
        if node.type == "export_statement":
            _ts_process_export(node, results, is_exported=True)
        elif node.type in ("function_declaration", "generator_function_declaration"):
            sym = _ts_extract_function(node, is_exported=False)
            if sym:
                results.append(sym)
        elif node.type in ("class_declaration", "abstract_class_declaration"):
            sym = _ts_extract_class(node, is_exported=False)
            if sym:
                results.append(sym)
        elif node.type in ("lexical_declaration", "variable_declaration"):
            _ts_extract_arrow(node, results, is_exported=False)

    return results


def _ts_process_export(export_node, results: list[ASTSymbol], is_exported: bool) -> None:
    for child in export_node.children:
        if child.type in ("function_declaration", "generator_function_declaration"):
            sym = _ts_extract_function(child, is_exported=True)
            if sym:
                results.append(sym)
        elif child.type in ("class_declaration", "abstract_class_declaration"):
            sym = _ts_extract_class(child, is_exported=True)
            if sym:
                results.append(sym)
        elif child.type in ("lexical_declaration", "variable_declaration"):
            _ts_extract_arrow(child, results, is_exported=True)


def _ts_extract_function(node, is_exported: bool) -> ASTSymbol | None:
    name_node = _child_by_type(node, "identifier")
    if not name_node:
        return None
    name = _node_text(name_node)
    params = _ts_params(node)
    ret_type = _ts_return_type(node)
    param_str = ", ".join(params)
    ret = f": {ret_type}" if ret_type else ""
    prefix = "export " if is_exported else ""
    sig = f"{prefix}function {name}({param_str}){ret}"
    return ASTSymbol(
        name=name,
        kind="function",
        params=params,
        return_type=ret_type,
        is_exported=is_exported,
        line=node.start_point[0] + 1,
        signature=sig,
    )


def _ts_extract_class(node, is_exported: bool) -> ASTSymbol | None:
    name_node = _child_by_type(node, "type_identifier", "identifier")
    if not name_node:
        return None
    name = _node_text(name_node)
    # Build signature from header text (first line before {)
    header = _node_header(node).split("{")[0].strip()
    prefix = "export " if is_exported else ""
    if not header.startswith(("class ", "abstract class ")):
        header = f"class {name}"
    sig = f"{prefix}{header}"
    return ASTSymbol(
        name=name,
        kind="class",
        is_exported=is_exported,
        line=node.start_point[0] + 1,
        signature=sig,
    )


def _ts_extract_arrow(decl_node, results: list[ASTSymbol], is_exported: bool) -> None:
    """Extract arrow functions from const/let declarations."""
    for child in decl_node.children:
        if child.type == "variable_declarator":
            name_node = _child_by_type(child, "identifier")
            arrow = None
            for c in child.children:
                if c.type == "arrow_function":
                    arrow = c
                    break
            if name_node and arrow:
                name = _node_text(name_node)
                params = _ts_params(arrow)
                ret_type = _ts_return_type(arrow)
                param_str = ", ".join(params)
                ret = f": {ret_type}" if ret_type else ""
                prefix = "export " if is_exported else ""
                sig = f"{prefix}const {name} = ({param_str}){ret} =>"
                results.append(ASTSymbol(
                    name=name,
                    kind="function",
                    params=params,
                    return_type=ret_type,
                    is_exported=is_exported,
                    line=decl_node.start_point[0] + 1,
                    signature=sig,
                ))


def _ts_params(func_node) -> list[str]:
    params_node = _child_by_type(func_node, "formal_parameters")
    if not params_node:
        return []
    param_types = {
        "required_parameter", "optional_parameter", "rest_parameter",
        "identifier", "assignment_pattern", "object_pattern", "array_pattern",
    }
    return [_node_text(c) for c in params_node.children if c.type in param_types]


def _ts_return_type(func_node) -> str | None:
    rt = _child_by_type(func_node, "type_annotation")
    if rt:
        # type_annotation = ": SomeType" — strip the colon
        text = _node_text(rt).lstrip(":").strip()
        return text or None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Java
# ─────────────────────────────────────────────────────────────────────────────


def _java_imports(root) -> list[ASTImport]:
    results: list[ASTImport] = []
    for node in root.children:
        if node.type == "import_declaration":
            raw = _node_text(node)  # e.g. "import com.example.Foo;"
            # Strip "import" prefix, optional "static", and trailing ";"
            raw = raw.strip()
            if raw.startswith("import"):
                raw = raw[6:].strip()
            if raw.startswith("static"):
                raw = raw[6:].strip()
            specifier = raw.rstrip(";").strip()
            # Last component is the imported name (or * for wildcard)
            last = specifier.rsplit(".", 1)[-1] if "." in specifier else specifier
            names = [] if last == "*" else [last]
            results.append(ASTImport(specifier=specifier, imported_names=names))
    return results


def _java_symbols(root) -> list[ASTSymbol]:
    results: list[ASTSymbol] = []
    for node in root.children:
        if node.type == "class_declaration":
            _java_extract_class(node, results)
        elif node.type == "interface_declaration":
            name_node = _child_by_type(node, "identifier")
            if name_node:
                results.append(ASTSymbol(
                    name=_node_text(name_node),
                    kind="class",
                    is_exported=True,
                    line=node.start_point[0] + 1,
                ))
    return results


def _java_extract_class(class_node, results: list[ASTSymbol]) -> None:
    name_node = _child_by_type(class_node, "identifier")
    if not name_node:
        return
    class_name = _node_text(name_node)
    results.append(ASTSymbol(
        name=class_name,
        kind="class",
        is_exported=True,
        line=class_node.start_point[0] + 1,
    ))
    # Extract methods from the class body
    body = _child_by_type(class_node, "class_body")
    if body:
        for child in body.children:
            if child.type == "method_declaration":
                sym = _java_extract_method(child, container=class_name)
                if sym:
                    results.append(sym)
            elif child.type == "class_declaration":
                _java_extract_class(child, results)


def _java_extract_method(method_node, container: str = "") -> ASTSymbol | None:
    name_node = _child_by_type(method_node, "identifier")
    if not name_node:
        return None
    params_node = _child_by_type(method_node, "formal_parameters")
    params: list[str] = []
    if params_node:
        params = [_node_text(c) for c in params_node.children if c.type == "formal_parameter"]
    return ASTSymbol(
        name=_node_text(name_node),
        kind="method",
        params=params,
        is_exported=True,
        line=method_node.start_point[0] + 1,
        container=container,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Go
# ─────────────────────────────────────────────────────────────────────────────


def _go_imports(root) -> list[ASTImport]:
    results: list[ASTImport] = []

    def _extract_import_spec(spec_node) -> None:
        # import_spec can be: `"path"` or `alias "path"` or `. "path"`
        string_node = _child_by_type(spec_node, "interpreted_string_literal", "raw_string_literal")
        if string_node:
            specifier = _unquote(_node_text(string_node))
            results.append(ASTImport(specifier=specifier, imported_names=[]))

    for node in root.children:
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_spec":
                    _extract_import_spec(child)
                elif child.type == "import_spec_list":
                    for grandchild in child.children:
                        if grandchild.type == "import_spec":
                            _extract_import_spec(grandchild)

    return results


def _go_symbols(root) -> list[ASTSymbol]:
    results: list[ASTSymbol] = []
    for node in root.children:
        if node.type == "function_declaration":
            sym = _go_extract_func(node)
            if sym:
                results.append(sym)
        elif node.type == "method_declaration":
            sym = _go_extract_func(node, kind="method")
            if sym:
                results.append(sym)
    return results


def _go_extract_func(node, kind: str = "function") -> ASTSymbol | None:
    name_node = _child_by_type(node, "identifier", "field_identifier")
    if not name_node:
        return None
    name = _node_text(name_node)

    # Collect all parameter_list children; first is receiver (for methods), last is return type
    param_lists = _children_by_type(node, "parameter_list")
    # For function_declaration: param_lists = [params]
    # For method_declaration:   param_lists = [receiver, params] or [receiver, params, return_params]
    params: list[str] = []
    ret_node = None
    if kind == "method" and len(param_lists) >= 2:
        # param_lists[0] is receiver, param_lists[1] is actual params, param_lists[2] (if any) is return
        params_node = param_lists[1]
        params = [_node_text(c) for c in params_node.children if c.type == "parameter_declaration"]
        if len(param_lists) >= 3:
            ret_node = param_lists[2]
    elif param_lists:
        params_node = param_lists[0]
        params = [_node_text(c) for c in params_node.children if c.type == "parameter_declaration"]
        # Return type: next sibling after params_node that is a type node
        found_params = False
        for child in node.children:
            if child is params_node:
                found_params = True
            elif found_params and child.type in (
                "type_identifier", "pointer_type", "qualified_type",
                "slice_type", "map_type", "channel_type", "interface_type", "struct_type",
                "parameter_list",  # multiple return values
            ):
                ret_node = child
                break

    # exported if name starts with uppercase
    is_exported = bool(name and name[0].isupper())
    return ASTSymbol(
        name=name,
        kind=kind,
        params=params,
        return_type=_node_text(ret_node) if ret_node else None,
        is_exported=is_exported,
        line=node.start_point[0] + 1,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Ruby
# ─────────────────────────────────────────────────────────────────────────────


def _ruby_imports(root) -> list[ASTImport]:
    results: list[ASTImport] = []
    for node in root.children:
        if node.type == "call":
            fn_node = _child_by_type(node, "identifier")
            if fn_node and _node_text(fn_node) in ("require", "require_relative"):
                arg_list = _child_by_type(node, "argument_list")
                if arg_list:
                    str_node = _child_by_type(arg_list, "string")
                    if str_node:
                        # Get string_content child (without quotes)
                        content = _child_by_type(str_node, "string_content")
                        if content:
                            specifier = _node_text(content)
                        else:
                            specifier = _unquote(_node_text(str_node))
                        results.append(ASTImport(specifier=specifier, imported_names=[]))
    return results


def _ruby_symbols(root) -> list[ASTSymbol]:
    results: list[ASTSymbol] = []
    for node in root.children:
        if node.type == "method":
            sym = _ruby_extract_method(node)
            if sym:
                results.append(sym)
        elif node.type == "class":
            sym = _ruby_extract_class(node)
            class_name = sym.name if sym else ""
            if sym:
                results.append(sym)
            # Also extract methods from within the class body
            body = _child_by_type(node, "body_statement")
            if body:
                for child in body.children:
                    if child.type == "method":
                        method_sym = _ruby_extract_method(child, container=class_name)
                        if method_sym:
                            results.append(method_sym)
    return results


def _ruby_extract_method(node, container: str = "") -> ASTSymbol | None:
    name_node = _child_by_type(node, "identifier")
    if not name_node:
        return None
    name = _node_text(name_node)
    params_node = _child_by_type(node, "method_parameters")
    params: list[str] = []
    if params_node:
        params = [
            _node_text(c)
            for c in params_node.children
            if c.type in (
                "identifier", "optional_parameter", "splat_parameter",
                "double_splat_parameter", "block_parameter", "keyword_parameter",
            )
        ]
    return ASTSymbol(
        name=name,
        kind="method",
        params=params,
        is_exported=not name.startswith("_"),
        line=node.start_point[0] + 1,
        container=container,
    )


def _ruby_extract_class(node) -> ASTSymbol | None:
    name_node = _child_by_type(node, "constant")
    if not name_node:
        return None
    return ASTSymbol(
        name=_node_text(name_node),
        kind="class",
        is_exported=True,
        line=node.start_point[0] + 1,
    )


# ─────────────────────────────────────────────────────────────────────────────
# C#
# ─────────────────────────────────────────────────────────────────────────────


def _cs_imports(root) -> list[ASTImport]:
    results: list[ASTImport] = []
    for node in root.children:
        if node.type == "using_directive":
            raw = _node_text(node)  # "using System.Collections;"
            specifier = raw.strip()
            if specifier.startswith("using"):
                specifier = specifier[5:].strip()
            specifier = specifier.rstrip(";").strip()
            results.append(ASTImport(specifier=specifier, imported_names=[specifier.rsplit(".", 1)[-1]]))
        elif node.type == "namespace_declaration":
            # Recurse into namespace for using directives at the namespace level
            decl_list = _child_by_type(node, "declaration_list")
            if decl_list:
                for child in decl_list.children:
                    if child.type == "using_directive":
                        raw = _node_text(child).strip()
                        if raw.startswith("using"):
                            raw = raw[5:].strip()
                        specifier = raw.rstrip(";").strip()
                        results.append(ASTImport(specifier=specifier, imported_names=[specifier.rsplit(".", 1)[-1]]))
    return results


def _cs_symbols(root) -> list[ASTSymbol]:
    results: list[ASTSymbol] = []

    def _collect(node) -> None:
        if node.type == "class_declaration":
            _cs_extract_class(node, results)
        elif node.type == "interface_declaration":
            name_node = _child_by_type(node, "identifier")
            if name_node:
                results.append(ASTSymbol(name=_node_text(name_node), kind="class", is_exported=True, line=node.start_point[0] + 1))
        elif node.type == "namespace_declaration":
            decl_list = _child_by_type(node, "declaration_list")
            if decl_list:
                for child in decl_list.children:
                    _collect(child)

    for child in root.children:
        _collect(child)
    return results


def _cs_extract_class(class_node, results: list[ASTSymbol]) -> None:
    name_node = _child_by_type(class_node, "identifier")
    if not name_node:
        return
    class_name = _node_text(name_node)
    results.append(ASTSymbol(
        name=class_name,
        kind="class",
        is_exported=True,
        line=class_node.start_point[0] + 1,
    ))
    body = _child_by_type(class_node, "declaration_list")
    if body:
        for child in body.children:
            if child.type == "method_declaration":
                sym = _cs_extract_method(child, container=class_name)
                if sym:
                    results.append(sym)
            elif child.type == "class_declaration":
                _cs_extract_class(child, results)


def _cs_extract_method(method_node, container: str = "") -> ASTSymbol | None:
    name_node = _child_by_type(method_node, "identifier")
    if not name_node:
        return None
    params_node = _child_by_type(method_node, "parameter_list")
    params: list[str] = []
    if params_node:
        params = [_node_text(c) for c in params_node.children if c.type == "parameter"]
    return ASTSymbol(
        name=_node_text(name_node),
        kind="method",
        params=params,
        is_exported=True,
        line=method_node.start_point[0] + 1,
        container=container,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Language dispatch tables — add a new language here; no changes to the
# public functions below are needed.
# ─────────────────────────────────────────────────────────────────────────────

_IMPORT_EXTRACTORS: dict[str, object] = {
    "python": _py_imports,
    "typescript": _ts_imports,
    "javascript": _ts_imports,
    "java": _java_imports,
    "go": _go_imports,
    "ruby": _ruby_imports,
    "csharp": _cs_imports,
}

_SYMBOL_EXTRACTORS: dict[str, object] = {
    "python": _py_symbols,
    "typescript": _ts_symbols,
    "javascript": _ts_symbols,
    "java": _java_symbols,
    "go": _go_symbols,
    "ruby": _ruby_symbols,
    "csharp": _cs_symbols,
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def extract_imports(source: str, language: str) -> list[ASTImport] | None:
    """Parse *source* and return all import statements as ASTImport objects.

    Returns None if tree-sitter is unavailable or parsing fails — callers should
    fall back to their regex-based extraction path.
    """
    _, root = _parse(source, language)
    if root is None:
        return None
    extractor = _IMPORT_EXTRACTORS.get(language)
    if extractor is None:
        return None
    try:
        return extractor(root)  # type: ignore[operator]
    except Exception:
        return None


def extract_symbols(source: str, language: str) -> list[ASTSymbol] | None:
    """Parse *source* and return top-level symbols (functions, classes, methods).

    Returns None if tree-sitter is unavailable or parsing fails — callers should
    fall back to their regex-based extraction path.
    """
    _, root = _parse(source, language)
    if root is None:
        return None
    extractor = _SYMBOL_EXTRACTORS.get(language)
    if extractor is None:
        return None
    try:
        return extractor(root)  # type: ignore[operator]
    except Exception:
        return None
