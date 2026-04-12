"""Unit tests for pr_impact/utils.py."""

from pr_impact.utils import read_file_safe


def test_read_file_safe_returns_file_content(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    assert read_file_safe(str(tmp_path / "hello.txt")) == "hello world\n"


def test_read_file_safe_returns_empty_string_on_missing_file(tmp_path):
    assert read_file_safe(str(tmp_path / "nonexistent.py")) == ""


def test_read_file_safe_returns_empty_string_on_directory(tmp_path):
    # Passing a directory path rather than a file → OSError → returns ""
    assert read_file_safe(str(tmp_path)) == ""


def test_read_file_safe_handles_utf8_content(tmp_path):
    (tmp_path / "unicode.py").write_text("# café\n", encoding="utf-8")
    assert "café" in read_file_safe(str(tmp_path / "unicode.py"))


def test_read_file_safe_returns_empty_string_on_permission_error(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.open", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("denied")))
    assert read_file_safe("/some/path.py") == ""
