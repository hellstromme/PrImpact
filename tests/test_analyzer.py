"""Tests for pr_impact/analyzer.py — ImpactAnalyzer, _invert_graph, and helpers migration."""

import pytest
from unittest.mock import MagicMock, patch

from pr_impact.analyzer import ImpactAnalyzer, _invert_graph
from pr_impact.models import AIAnalysis, RefsResult, SourceLocation
from tests.helpers import make_file, make_security_signal


# ---------------------------------------------------------------------------
# _invert_graph — now lives in analyzer.py
# ---------------------------------------------------------------------------


def test_invert_graph_empty():
    assert _invert_graph({}) == {}


def test_invert_graph_single_edge():
    assert _invert_graph({"a": ["b"]}) == {"b": ["a"]}


def test_invert_graph_multiple_targets_from_one_source():
    result = _invert_graph({"a": ["b", "c"]})
    assert result == {"b": ["a"], "c": ["a"]}


def test_invert_graph_multiple_sources_to_same_target():
    result = _invert_graph({"a": ["c"], "b": ["c"]})
    assert set(result["c"]) == {"a", "b"}


def test_invert_graph_node_with_empty_list_contributes_nothing():
    assert _invert_graph({"a": []}) == {}


def test_invert_graph_does_not_mutate_input():
    original = {"a": ["b"]}
    _invert_graph(original)
    assert original == {"a": ["b"]}


def test_invert_graph_returns_plain_dict():
    result = _invert_graph({"a": ["b"]})
    assert type(result) is dict


# ---------------------------------------------------------------------------
# ImpactAnalyzer — constructor parameter storage
# ---------------------------------------------------------------------------


def test_impact_analyzer_stores_all_constructor_params():
    repo_obj = MagicMock()
    refs = RefsResult(base="abc", head="def")
    history = [{"file": "x.py", "description": "anomaly"}]
    hotspots = [{"file": "y.py", "appearances": 3}]

    analyzer = ImpactAnalyzer(
        "path/to/repo",
        repo_obj,
        refs,
        max_depth=5,
        check_osv=True,
        anomaly_history=history,
        hotspots=hotspots,
    )

    assert analyzer.repo == "path/to/repo"
    assert analyzer.repo_obj is repo_obj
    assert analyzer.refs is refs
    assert analyzer.max_depth == 5
    assert analyzer.check_osv is True
    assert analyzer.anomaly_history is history
    assert analyzer.hotspots is hotspots


def test_impact_analyzer_default_params():
    refs = RefsResult(base="abc", head="def")
    analyzer = ImpactAnalyzer(".", MagicMock(), refs)

    assert analyzer.max_depth == 3
    assert analyzer.check_osv is False
    assert analyzer.anomaly_history is None
    assert analyzer.hotspots is None


# ---------------------------------------------------------------------------
# ImpactAnalyzer.run() — progress object contract
# ---------------------------------------------------------------------------


def test_impact_analyzer_run_raises_when_progress_is_none():
    """None progress raises immediately when .add_task is called — caller's contract."""
    refs = RefsResult(base="abc", head="def")
    with (
        patch("pr_impact.analyzer.get_changed_files", return_value=[make_file("foo.py")]),
        pytest.raises((AttributeError, TypeError)),
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(None)


def test_impact_analyzer_run_raises_when_progress_lacks_required_methods():
    """An object without add_task raises AttributeError — protocol is not optional."""
    refs = RefsResult(base="abc", head="def")

    class BareObject:
        pass

    with (
        patch("pr_impact.analyzer.get_changed_files", return_value=[make_file("foo.py")]),
        pytest.raises(AttributeError),
    ):
        ImpactAnalyzer(".", MagicMock(), refs, max_depth=3).run(BareObject())


# ---------------------------------------------------------------------------
# ImpactAnalyzer.run() — invalid history / hotspots data passed through
# ---------------------------------------------------------------------------

def _full_patches():
    return [
        patch("pr_impact.analyzer.get_changed_files", return_value=[make_file("foo.py")]),
        patch("pr_impact.analyzer.build_import_graph", return_value={}),
        patch("pr_impact.analyzer.get_blast_radius", return_value=[]),
        patch("pr_impact.analyzer.get_git_churn", return_value=0.0),
        patch("pr_impact.analyzer.get_pr_metadata", return_value={}),
        patch("pr_impact.analyzer.run_ai_analysis", return_value=AIAnalysis(summary="ok")),
        patch("pr_impact.analyzer.detect_pattern_signals", return_value=[]),
        patch("pr_impact.analyzer.check_dependency_integrity", return_value=[]),
    ]


def test_impact_analyzer_run_with_anomaly_history_invalid_entries_does_not_crash():
    """Invalid entries in anomaly_history are passed through to run_ai_analysis, which handles them."""
    refs = RefsResult(base="abc", head="def")
    bad_history = [None, 42, "not-a-dict", {"file": "ok.py", "description": "ok"}]
    patches = _full_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        result = ImpactAnalyzer(".", MagicMock(), refs, anomaly_history=bad_history).run(MagicMock())
    assert len(result) == 6


def test_impact_analyzer_run_with_hotspots_invalid_entries_does_not_crash():
    """Invalid entries in hotspots are passed through to run_ai_analysis, which handles them."""
    refs = RefsResult(base="abc", head="def")
    bad_hotspots = [None, "not-a-dict", {"file": "ok.py", "appearances": 1}]
    patches = _full_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        result = ImpactAnalyzer(".", MagicMock(), refs, hotspots=bad_hotspots).run(MagicMock())
    assert len(result) == 6


# ---------------------------------------------------------------------------
# ImpactAnalyzer.run() — consistent 6-tuple when steps fail
# ---------------------------------------------------------------------------


def test_impact_analyzer_run_returns_six_tuple_when_import_graph_fails():
    refs = RefsResult(base="abc", head="def")
    patches = _full_patches()
    patches[1] = patch("pr_impact.analyzer.build_import_graph", side_effect=RuntimeError("boom"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        result = ImpactAnalyzer(".", MagicMock(), refs).run(MagicMock())
    assert len(result) == 6
    changed, blast, interface, ai, metadata, deps = result
    assert changed  # pipeline completed


def test_impact_analyzer_run_returns_six_tuple_when_blast_radius_fails():
    refs = RefsResult(base="abc", head="def")
    patches = _full_patches()
    patches[2] = patch("pr_impact.analyzer.get_blast_radius", side_effect=RuntimeError("boom"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        result = ImpactAnalyzer(".", MagicMock(), refs).run(MagicMock())
    assert len(result) == 6
    _, blast, _, _, _, _ = result
    assert blast == []


def test_impact_analyzer_run_returns_six_tuple_when_ai_fails():
    refs = RefsResult(base="abc", head="def")
    patches = _full_patches()
    patches[5] = patch("pr_impact.analyzer.run_ai_analysis", side_effect=ValueError("no key"))
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
        result = ImpactAnalyzer(".", MagicMock(), refs).run(MagicMock())
    assert len(result) == 6
    _, _, _, ai, _, _ = result
    assert ai.summary == ""


# ---------------------------------------------------------------------------
# make_security_signal helper — backward-compat file_path/line_number kwargs
# ---------------------------------------------------------------------------


def test_make_security_signal_default_produces_source_location():
    sig = make_security_signal()
    assert isinstance(sig.location, SourceLocation)
    assert sig.location.file == "src/auth.py"
    assert sig.location.line == 10


def test_make_security_signal_file_path_kwarg_sets_location_file():
    sig = make_security_signal(file_path="src/other.py")
    assert sig.location.file == "src/other.py"


def test_make_security_signal_line_number_kwarg_sets_location_line():
    sig = make_security_signal(line_number=99)
    assert sig.location.line == 99


def test_make_security_signal_line_number_none_sets_none():
    sig = make_security_signal(line_number=None)
    assert sig.location.line is None


def test_make_security_signal_does_not_pass_file_path_to_model():
    """SecuritySignal constructor no longer accepts file_path — factory must absorb it."""
    from pr_impact.models import SecuritySignal
    sig = make_security_signal(file_path="x.py", line_number=1)
    assert not hasattr(sig, "file_path")
    assert not hasattr(sig, "line_number")


# ---------------------------------------------------------------------------
# SourceLocation.symbol — populated and accessible on SecuritySignal
# ---------------------------------------------------------------------------


def test_security_signal_location_symbol_field_populated_and_accessible():
    loc = SourceLocation(file="src/auth.py", line=5, symbol="process_token")
    sig = make_security_signal()
    sig.location = loc
    assert sig.location.symbol == "process_token"


def test_source_location_symbol_defaults_to_none():
    loc = SourceLocation(file="src/foo.py", line=1)
    assert loc.symbol is None


# ---------------------------------------------------------------------------
# ImpactAnalyzer.run() — imported_symbols population for blast radius entries
# ---------------------------------------------------------------------------


from pr_impact.models import BlastRadiusEntry


def _patches_with_blast(blast_entries, changed_files=None):
    """Full patch set with configurable blast radius and changed files."""
    from tests.helpers import make_file as _make_file
    files = changed_files if changed_files is not None else [_make_file("foo.py")]
    return [
        patch("pr_impact.analyzer.get_changed_files", return_value=files),
        patch("pr_impact.analyzer.build_import_graph", return_value={}),
        patch("pr_impact.analyzer.get_blast_radius", return_value=blast_entries),
        patch("pr_impact.analyzer.get_git_churn", return_value=0.0),
        patch("pr_impact.analyzer.get_pr_metadata", return_value={}),
        patch("pr_impact.analyzer.run_ai_analysis", return_value=AIAnalysis(summary="ok")),
        patch("pr_impact.analyzer.detect_pattern_signals", return_value=[]),
        patch("pr_impact.analyzer.check_dependency_integrity", return_value=[]),
    ]


def _run_with(patches, extra_patches=(), repo="."):
    import contextlib
    refs = RefsResult(base="abc", head="def")
    all_patches = patches + list(extra_patches)
    with contextlib.ExitStack() as stack:
        for p in all_patches:
            stack.enter_context(p)
        return ImpactAnalyzer(repo, MagicMock(), refs).run(MagicMock())


def test_distance1_imported_symbols_populated():
    entry = BlastRadiusEntry(path="consumer.py", distance=1, imported_symbols=[], churn_score=None)
    ps = _patches_with_blast([entry])
    _, blast, *_ = _run_with(
        ps,
        [patch("pr_impact.analyzer.get_imported_symbols", return_value=["login", "logout"])],
    )
    assert set(blast[0].imported_symbols) == {"login", "logout"}


def test_distance2_imported_symbols_not_populated():
    entry = BlastRadiusEntry(path="indirect.py", distance=2, imported_symbols=[], churn_score=None)
    ps = _patches_with_blast([entry])
    _, blast, *_ = _run_with(
        ps,
        [patch("pr_impact.analyzer.get_imported_symbols", return_value=["something"])],
    )
    assert blast[0].imported_symbols == []


def test_distance1_imported_symbols_exception_silenced():
    entry = BlastRadiusEntry(path="consumer.py", distance=1, imported_symbols=[], churn_score=None)
    ps = _patches_with_blast([entry])
    _, blast, *_ = _run_with(
        ps,
        [patch("pr_impact.analyzer.get_imported_symbols", side_effect=OSError("disk error"))],
    )
    assert blast[0].imported_symbols == []


def test_distance1_imported_symbols_deduplicated():
    """Symbols returned for multiple changed files are merged and deduplicated."""
    from tests.helpers import make_file as _mf
    entry = BlastRadiusEntry(path="consumer.py", distance=1, imported_symbols=[], churn_score=None)
    changed = [_mf("a.py"), _mf("b.py")]
    ps = _patches_with_blast([entry], changed_files=changed)
    # get_imported_symbols returns "login" for both calls → should appear once
    _, blast, *_ = _run_with(
        ps,
        [patch("pr_impact.analyzer.get_imported_symbols", return_value=["login"])],
    )
    assert blast[0].imported_symbols == ["login"]


def test_distance1_absolute_paths_passed_to_get_imported_symbols():
    from tests.helpers import make_file as _mf
    entry = BlastRadiusEntry(path="src/consumer.py", distance=1, imported_symbols=[], churn_score=None)
    ps = _patches_with_blast([entry], changed_files=[_mf("src/auth.py")])
    with patch("pr_impact.analyzer.get_imported_symbols", return_value=[]) as mock_gis:
        _run_with(ps, [], repo="/repo/root")
    mock_gis.assert_called_once_with(
        "/repo/root/src/consumer.py",
        "/repo/root/src/auth.py",
    )


def test_distance1_and_distance2_mixed_only_d1_processed():
    """Only distance-1 entries trigger get_imported_symbols; distance-2 entries are skipped."""
    from tests.helpers import make_file as _mf
    d1 = BlastRadiusEntry(path="direct.py", distance=1, imported_symbols=[], churn_score=None)
    d2 = BlastRadiusEntry(path="indirect.py", distance=2, imported_symbols=[], churn_score=None)
    ps = _patches_with_blast([d1, d2])
    with patch("pr_impact.analyzer.get_imported_symbols", return_value=["sym"]) as mock_gis:
        _, blast, *_ = _run_with(ps, [], repo=".")
    # Called once for d1, never for d2
    assert mock_gis.call_count == 1
    assert set(blast[0].imported_symbols) == {"sym"}
    assert blast[1].imported_symbols == []
