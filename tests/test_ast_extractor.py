"""Unit tests for pr_impact/ast_extractor.py.

Tests cover extract_imports() and extract_symbols() for all 7 supported languages,
plus graceful fallback behaviour on bad input.
"""

import pytest

from pr_impact.ast_extractor import ASTImport, ASTSymbol, extract_imports, extract_symbols


# ---------------------------------------------------------------------------
# Graceful fallback
# ---------------------------------------------------------------------------


def test_unknown_language_returns_none_imports():
    assert extract_imports("import foo", "cobol") is None


def test_unknown_language_returns_none_symbols():
    assert extract_symbols("def foo(): pass", "cobol") is None


def test_empty_source_imports_returns_empty_list():
    result = extract_imports("", "python")
    assert result is not None
    assert result == []


def test_empty_source_symbols_returns_empty_list():
    result = extract_symbols("", "python")
    assert result is not None
    assert result == []


def test_syntax_error_source_does_not_raise():
    # tree-sitter is error-tolerant, so this should return something (not None)
    # even with broken syntax — the important thing is no exception propagates.
    result = extract_imports("from", "python")
    assert result is not None  # partial parse still works


# ---------------------------------------------------------------------------
# Python — imports
# ---------------------------------------------------------------------------


PY_IMPORTS_SOURCE = """\
import os
import sys
from pathlib import Path
from .models import Foo, Bar
from ..utils import helper
from collections import *
import json as _json
"""


def test_py_imports_bare_module():
    result = extract_imports(PY_IMPORTS_SOURCE, "python")
    assert result is not None
    specs = [r.specifier for r in result]
    assert "os" in specs
    assert "sys" in specs


def test_py_imports_from_stdlib():
    result = extract_imports(PY_IMPORTS_SOURCE, "python")
    assert result is not None
    path_import = next((r for r in result if r.specifier == "pathlib"), None)
    assert path_import is not None
    assert "Path" in path_import.imported_names


def test_py_imports_relative():
    result = extract_imports(PY_IMPORTS_SOURCE, "python")
    assert result is not None
    rel = next((r for r in result if r.specifier == ".models"), None)
    assert rel is not None
    assert "Foo" in rel.imported_names
    assert "Bar" in rel.imported_names


def test_py_imports_double_relative():
    result = extract_imports(PY_IMPORTS_SOURCE, "python")
    assert result is not None
    specs = [r.specifier for r in result]
    assert "..utils" in specs


def test_py_imports_star_has_empty_names():
    result = extract_imports(PY_IMPORTS_SOURCE, "python")
    assert result is not None
    star = next((r for r in result if r.specifier == "collections"), None)
    assert star is not None
    assert star.imported_names == []


def test_py_imports_aliased():
    result = extract_imports(PY_IMPORTS_SOURCE, "python")
    assert result is not None
    specs = [r.specifier for r in result]
    assert "json" in specs


# ---------------------------------------------------------------------------
# Python — symbols
# ---------------------------------------------------------------------------


PY_SYMBOLS_SOURCE = """\
import os


def public_func(x: int, y: str = "default") -> bool:
    return True


def _private(z):
    pass


class MyClass:
    def method(self):
        pass


@decorator
def decorated(a, b):
    pass
"""


def test_py_symbols_function():
    result = extract_symbols(PY_SYMBOLS_SOURCE, "python")
    assert result is not None
    func = next((s for s in result if s.name == "public_func"), None)
    assert func is not None
    assert func.kind == "function"
    assert func.is_exported is True
    assert "x: int" in func.params or any("x" in p for p in func.params)


def test_py_symbols_private_function():
    result = extract_symbols(PY_SYMBOLS_SOURCE, "python")
    assert result is not None
    priv = next((s for s in result if s.name == "_private"), None)
    assert priv is not None
    assert priv.is_exported is False


def test_py_symbols_class():
    result = extract_symbols(PY_SYMBOLS_SOURCE, "python")
    assert result is not None
    cls = next((s for s in result if s.name == "MyClass"), None)
    assert cls is not None
    assert cls.kind == "class"


def test_py_symbols_decorated():
    result = extract_symbols(PY_SYMBOLS_SOURCE, "python")
    assert result is not None
    dec = next((s for s in result if s.name == "decorated"), None)
    assert dec is not None
    assert any("decorator" in d for d in dec.decorators)


def test_py_symbols_return_type():
    result = extract_symbols(PY_SYMBOLS_SOURCE, "python")
    assert result is not None
    func = next((s for s in result if s.name == "public_func"), None)
    assert func is not None
    assert func.return_type == "bool"


def test_py_symbols_signature_contains_name():
    result = extract_symbols(PY_SYMBOLS_SOURCE, "python")
    assert result is not None
    func = next((s for s in result if s.name == "public_func"), None)
    assert func is not None
    assert "public_func" in func.signature


# ---------------------------------------------------------------------------
# TypeScript — imports
# ---------------------------------------------------------------------------


TS_IMPORTS_SOURCE = """\
import { Foo, Bar } from './models';
import type { Baz } from '../types';
import * as Utils from './utils';
import DefaultExport from './default';
export { Foo } from './models';
"""


def test_ts_imports_named():
    result = extract_imports(TS_IMPORTS_SOURCE, "typescript")
    assert result is not None
    imp = next((r for r in result if "./models" in r.specifier and not r.is_reexport), None)
    assert imp is not None
    assert "Foo" in imp.imported_names
    assert "Bar" in imp.imported_names


def test_ts_imports_type():
    result = extract_imports(TS_IMPORTS_SOURCE, "typescript")
    assert result is not None
    specs = [r.specifier for r in result]
    assert "../types" in specs


def test_ts_imports_reexport():
    result = extract_imports(TS_IMPORTS_SOURCE, "typescript")
    assert result is not None
    reexp = next((r for r in result if r.is_reexport), None)
    assert reexp is not None
    assert reexp.specifier == "./models"


# ---------------------------------------------------------------------------
# TypeScript — symbols
# ---------------------------------------------------------------------------


TS_SYMBOLS_SOURCE = """\
export function greet(name: string): string {
    return `Hello ${name}`;
}

export class Animal {
    constructor(public name: string) {}
}

export abstract class Shape {
    abstract area(): number;
}

export const add = (a: number, b: number): number => a + b;

function internal(): void {}
"""


def test_ts_symbols_exported_function():
    result = extract_symbols(TS_SYMBOLS_SOURCE, "typescript")
    assert result is not None
    func = next((s for s in result if s.name == "greet"), None)
    assert func is not None
    assert func.is_exported is True
    assert func.kind == "function"


def test_ts_symbols_exported_class():
    result = extract_symbols(TS_SYMBOLS_SOURCE, "typescript")
    assert result is not None
    cls = next((s for s in result if s.name == "Animal"), None)
    assert cls is not None
    assert cls.kind == "class"
    assert cls.is_exported is True


def test_ts_symbols_abstract_class():
    result = extract_symbols(TS_SYMBOLS_SOURCE, "typescript")
    assert result is not None
    names = [s.name for s in result]
    assert "Shape" in names


def test_ts_symbols_arrow_function():
    result = extract_symbols(TS_SYMBOLS_SOURCE, "typescript")
    assert result is not None
    arrow = next((s for s in result if s.name == "add"), None)
    assert arrow is not None
    assert arrow.kind == "function"


def test_ts_symbols_internal_not_exported():
    result = extract_symbols(TS_SYMBOLS_SOURCE, "typescript")
    assert result is not None
    internal = next((s for s in result if s.name == "internal"), None)
    assert internal is not None
    assert internal.is_exported is False


# ---------------------------------------------------------------------------
# JavaScript — imports (CommonJS require)
# ---------------------------------------------------------------------------


JS_CJS_SOURCE = """\
const fs = require('fs');
const { join, resolve } = require('path');
const myModule = require('./myModule');
"""


def test_js_commonjs_require():
    result = extract_imports(JS_CJS_SOURCE, "javascript")
    assert result is not None
    specs = [r.specifier for r in result]
    assert "fs" in specs
    assert "path" in specs
    assert "./myModule" in specs


JS_ESM_SOURCE = """\
import { useState } from 'react';
import styles from './styles.css';
"""


def test_js_esm_imports():
    result = extract_imports(JS_ESM_SOURCE, "javascript")
    assert result is not None
    react_imp = next((r for r in result if r.specifier == "react"), None)
    assert react_imp is not None
    assert "useState" in react_imp.imported_names


# ---------------------------------------------------------------------------
# Java — imports
# ---------------------------------------------------------------------------


JAVA_SOURCE = """\
package com.example;

import java.util.List;
import java.util.Map;
import static java.util.Collections.emptyList;
import com.example.utils.*;

public class MyService {
    public void doWork(List<String> items) {
    }

    private void helper() {
    }
}
"""


def test_java_imports():
    result = extract_imports(JAVA_SOURCE, "java")
    assert result is not None
    specs = [r.specifier for r in result]
    assert any("java.util.List" in s for s in specs)
    assert any("java.util.Map" in s for s in specs)


def test_java_imports_wildcard():
    result = extract_imports(JAVA_SOURCE, "java")
    assert result is not None
    wildcard = next((r for r in result if "utils.*" in r.specifier or "utils" in r.specifier), None)
    assert wildcard is not None
    assert wildcard.imported_names == []


def test_java_symbols_class():
    result = extract_symbols(JAVA_SOURCE, "java")
    assert result is not None
    cls = next((s for s in result if s.name == "MyService"), None)
    assert cls is not None
    assert cls.kind == "class"


def test_java_symbols_method():
    result = extract_symbols(JAVA_SOURCE, "java")
    assert result is not None
    method = next((s for s in result if s.name == "doWork"), None)
    assert method is not None
    assert method.kind == "method"


# ---------------------------------------------------------------------------
# Go — imports
# ---------------------------------------------------------------------------


GO_SOURCE = """\
package main

import (
    "fmt"
    "os"
    "github.com/example/pkg"
)

func main() {
    fmt.Println("hello")
}

func (r *Receiver) Method(x int) string {
    return ""
}

func Exported(a, b int) int {
    return a + b
}
"""


def test_go_imports_grouped():
    result = extract_imports(GO_SOURCE, "go")
    assert result is not None
    specs = [r.specifier for r in result]
    assert "fmt" in specs
    assert "os" in specs
    assert "github.com/example/pkg" in specs


def test_go_symbols_function():
    result = extract_symbols(GO_SOURCE, "go")
    assert result is not None
    main_func = next((s for s in result if s.name == "main"), None)
    assert main_func is not None
    assert main_func.kind == "function"


def test_go_symbols_exported():
    result = extract_symbols(GO_SOURCE, "go")
    assert result is not None
    exported = next((s for s in result if s.name == "Exported"), None)
    assert exported is not None
    assert exported.is_exported is True


def test_go_symbols_method_no_receiver_in_params():
    result = extract_symbols(GO_SOURCE, "go")
    assert result is not None
    method = next((s for s in result if s.name == "Method"), None)
    assert method is not None
    assert method.kind == "method"
    # Receiver should NOT appear in params list
    receiver_in_params = any("Receiver" in p for p in method.params)
    assert not receiver_in_params, f"Receiver appeared in params: {method.params}"


# ---------------------------------------------------------------------------
# Ruby — imports
# ---------------------------------------------------------------------------


RUBY_SOURCE = """\
require 'json'
require_relative '../models/user'

class UserService
  def create(name)
    User.new(name)
  end

  def _internal
  end
end
"""


def test_ruby_imports_require():
    result = extract_imports(RUBY_SOURCE, "ruby")
    assert result is not None
    specs = [r.specifier for r in result]
    assert "json" in specs


def test_ruby_imports_require_relative():
    result = extract_imports(RUBY_SOURCE, "ruby")
    assert result is not None
    specs = [r.specifier for r in result]
    assert any("models/user" in s or "../models/user" in s for s in specs)


def test_ruby_symbols_class():
    result = extract_symbols(RUBY_SOURCE, "ruby")
    assert result is not None
    cls = next((s for s in result if s.name == "UserService"), None)
    assert cls is not None
    assert cls.kind == "class"


def test_ruby_symbols_method():
    result = extract_symbols(RUBY_SOURCE, "ruby")
    assert result is not None
    method = next((s for s in result if s.name == "create"), None)
    assert method is not None
    assert method.kind == "method"


# ---------------------------------------------------------------------------
# C# — imports
# ---------------------------------------------------------------------------


CS_SOURCE = """\
using System;
using System.Collections.Generic;
using Microsoft.Extensions.Logging;

namespace MyApp.Services
{
    public class UserService
    {
        public string GetUser(int id)
        {
            return id.ToString();
        }

        private void LogEvent(string msg) { }
    }
}
"""


def test_cs_imports():
    result = extract_imports(CS_SOURCE, "csharp")
    assert result is not None
    specs = [r.specifier for r in result]
    assert any("System" in s for s in specs)
    assert any("Collections" in s or "Generic" in s for s in specs)


def test_cs_symbols_class():
    result = extract_symbols(CS_SOURCE, "csharp")
    assert result is not None
    cls = next((s for s in result if s.name == "UserService"), None)
    assert cls is not None
    assert cls.kind == "class"


def test_cs_symbols_method():
    result = extract_symbols(CS_SOURCE, "csharp")
    assert result is not None
    method = next((s for s in result if s.name == "GetUser"), None)
    assert method is not None
    assert method.kind == "method"


# ---------------------------------------------------------------------------
# ASTSymbol / ASTImport dataclass defaults
# ---------------------------------------------------------------------------


def test_ast_import_defaults():
    imp = ASTImport(specifier="./foo")
    assert imp.imported_names == []
    assert imp.is_reexport is False


def test_ast_symbol_defaults():
    sym = ASTSymbol(name="foo", kind="function")
    assert sym.params == []
    assert sym.decorators == []
    assert sym.return_type is None
    assert sym.is_exported is False
    assert sym.line == 0
    assert sym.signature == ""
