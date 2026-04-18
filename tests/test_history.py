"""Unit tests for pr_impact/history.py — v0.4 functions."""

import sqlite3

import pytest

from pr_impact.history import (
    get_run_count,
    load_anomaly_patterns,
    load_hotspots,
    save_run,
)
from pr_impact.models import (
    AIAnalysis,
    Anomaly,
    BlastRadiusEntry,
)
from tests.helpers import make_report, make_security_signal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "history.db")


@pytest.fixture()
def repo_path():
    return "/fake/repo"


def _minimal_report(**overrides):
    """Build a report with sensible defaults and no anomalies/signals."""
    defaults = {
        "ai_analysis": AIAnalysis(summary="ok"),
        "blast_radius": [],
    }
    defaults.update(overrides)
    return make_report(**defaults)


# ---------------------------------------------------------------------------
# get_run_count
# ---------------------------------------------------------------------------


class TestGetRunCount:
    def test_zero_before_any_saves(self, db_path, repo_path):
        save_run(db_path, _minimal_report(), repo_path=repo_path)
        # Ensure the DB exists, then check a different repo
        assert get_run_count(db_path, "/other/repo") == 0

    def test_returns_n_after_n_saves(self, db_path, repo_path):
        for _ in range(3):
            save_run(db_path, _minimal_report(), repo_path=repo_path)
        assert get_run_count(db_path, repo_path) == 3

    def test_nonexistent_db_returns_zero(self, tmp_path):
        missing = str(tmp_path / "no_such.db")
        assert get_run_count(missing, "/any") == 0

    def test_wrong_repo_path_returns_zero(self, db_path, repo_path):
        save_run(db_path, _minimal_report(), repo_path=repo_path)
        assert get_run_count(db_path, "/wrong/repo") == 0


# ---------------------------------------------------------------------------
# load_hotspots
# ---------------------------------------------------------------------------


class TestLoadHotspots:
    def test_empty_db_returns_empty(self, db_path, repo_path):
        # Create the DB with zero runs
        save_run(db_path, _minimal_report(), repo_path=repo_path)
        # No blast entries -> empty hotspots
        assert load_hotspots(db_path, repo_path) == []

    def test_nonexistent_db_returns_empty(self, tmp_path):
        missing = str(tmp_path / "no_such.db")
        assert load_hotspots(missing, "/any") == []

    def test_single_run_single_blast_entry(self, db_path, repo_path):
        report = _minimal_report(
            blast_radius=[
                BlastRadiusEntry(path="src/utils.py", distance=1, imported_symbols=["foo"], churn_score=2.0),
            ],
        )
        save_run(db_path, report, repo_path=repo_path)

        hotspots = load_hotspots(db_path, repo_path)
        assert len(hotspots) == 1
        assert hotspots[0]["file"] == "src/utils.py"
        assert hotspots[0]["appearances"] == 1

    def test_multiple_runs_counts_and_orders_desc(self, db_path, repo_path):
        # Run 1: utils.py appears
        report1 = _minimal_report(
            blast_radius=[
                BlastRadiusEntry(path="src/utils.py", distance=1, imported_symbols=[], churn_score=None),
            ],
        )
        # Run 2: utils.py + db.py appear
        report2 = _minimal_report(
            blast_radius=[
                BlastRadiusEntry(path="src/utils.py", distance=1, imported_symbols=[], churn_score=None),
                BlastRadiusEntry(path="src/db.py", distance=2, imported_symbols=[], churn_score=None),
            ],
        )
        # Run 3: utils.py + db.py + auth.py appear
        report3 = _minimal_report(
            blast_radius=[
                BlastRadiusEntry(path="src/utils.py", distance=1, imported_symbols=[], churn_score=None),
                BlastRadiusEntry(path="src/db.py", distance=2, imported_symbols=[], churn_score=None),
                BlastRadiusEntry(path="src/auth.py", distance=1, imported_symbols=[], churn_score=None),
            ],
        )

        save_run(db_path, report1, repo_path=repo_path)
        save_run(db_path, report2, repo_path=repo_path)
        save_run(db_path, report3, repo_path=repo_path)

        hotspots = load_hotspots(db_path, repo_path)
        assert len(hotspots) == 3
        # utils.py = 3, db.py = 2, auth.py = 1  (descending)
        assert hotspots[0] == {"file": "src/utils.py", "appearances": 3}
        assert hotspots[1] == {"file": "src/db.py", "appearances": 2}
        assert hotspots[2] == {"file": "src/auth.py", "appearances": 1}

    def test_limit_param_caps_results(self, db_path, repo_path):
        report = _minimal_report(
            blast_radius=[
                BlastRadiusEntry(path=f"src/file{i}.py", distance=1, imported_symbols=[], churn_score=None)
                for i in range(5)
            ],
        )
        save_run(db_path, report, repo_path=repo_path)

        hotspots = load_hotspots(db_path, repo_path, limit=2)
        assert len(hotspots) == 2

    def test_wrong_repo_path_returns_empty(self, db_path, repo_path):
        report = _minimal_report(
            blast_radius=[
                BlastRadiusEntry(path="src/a.py", distance=1, imported_symbols=[], churn_score=None),
            ],
        )
        save_run(db_path, report, repo_path=repo_path)
        assert load_hotspots(db_path, "/wrong/repo") == []


# ---------------------------------------------------------------------------
# load_anomaly_patterns
# ---------------------------------------------------------------------------


class TestLoadAnomalyPatterns:
    def test_no_anomalies_returns_empty(self, db_path, repo_path):
        save_run(db_path, _minimal_report(), repo_path=repo_path)
        assert load_anomaly_patterns(db_path, repo_path) == []

    def test_nonexistent_db_returns_empty(self, tmp_path):
        missing = str(tmp_path / "no_such.db")
        assert load_anomaly_patterns(missing, "/any") == []

    def test_anomaly_in_one_run_not_returned(self, db_path, repo_path):
        report = _minimal_report(
            ai_analysis=AIAnalysis(
                summary="x",
                anomalies=[Anomaly(description="SQL injection", location="src/db.py:42", severity="high")],
            ),
        )
        save_run(db_path, report, repo_path=repo_path)

        patterns = load_anomaly_patterns(db_path, repo_path)
        assert patterns == []

    def test_same_anomaly_in_two_runs_is_returned(self, db_path, repo_path):
        report = _minimal_report(
            ai_analysis=AIAnalysis(
                summary="x",
                anomalies=[Anomaly(description="SQL injection", location="src/db.py:42", severity="high")],
            ),
        )
        save_run(db_path, report, repo_path=repo_path)
        save_run(db_path, report, repo_path=repo_path)

        patterns = load_anomaly_patterns(db_path, repo_path)
        assert len(patterns) == 1
        assert patterns[0]["file"] == "src/db.py"
        assert patterns[0]["description"] == "SQL injection"
        assert patterns[0]["run_count"] == 2

    def test_different_anomalies_across_runs(self, db_path, repo_path):
        """Anomaly A in 3 runs, anomaly B in 2 runs, anomaly C in 1 run -> only A and B returned."""
        anomaly_a = Anomaly(description="Hardcoded secret", location="src/config.py:10", severity="high")
        anomaly_b = Anomaly(description="Missing null check", location="src/handler.py:20", severity="medium")
        anomaly_c = Anomaly(description="Dead code", location="src/old.py:1", severity="low")

        # Run 1: A + B
        r1 = _minimal_report(ai_analysis=AIAnalysis(summary="x", anomalies=[anomaly_a, anomaly_b]))
        # Run 2: A + B + C
        r2 = _minimal_report(ai_analysis=AIAnalysis(summary="x", anomalies=[anomaly_a, anomaly_b, anomaly_c]))
        # Run 3: A only
        r3 = _minimal_report(ai_analysis=AIAnalysis(summary="x", anomalies=[anomaly_a]))

        save_run(db_path, r1, repo_path=repo_path)
        save_run(db_path, r2, repo_path=repo_path)
        save_run(db_path, r3, repo_path=repo_path)

        patterns = load_anomaly_patterns(db_path, repo_path)
        assert len(patterns) == 2
        # Ordered by run_count DESC: A(3), B(2)
        assert patterns[0]["description"] == "Hardcoded secret"
        assert patterns[0]["run_count"] == 3
        assert patterns[1]["description"] == "Missing null check"
        assert patterns[1]["run_count"] == 2

    def test_limit_param(self, db_path, repo_path):
        anomaly_a = Anomaly(description="A", location="a.py:1", severity="low")
        anomaly_b = Anomaly(description="B", location="b.py:1", severity="low")

        report = _minimal_report(ai_analysis=AIAnalysis(summary="x", anomalies=[anomaly_a, anomaly_b]))
        save_run(db_path, report, repo_path=repo_path)
        save_run(db_path, report, repo_path=repo_path)

        patterns = load_anomaly_patterns(db_path, repo_path, limit=1)
        assert len(patterns) == 1


# ---------------------------------------------------------------------------
# save_run — anomaly and security_signal persistence
# ---------------------------------------------------------------------------


class TestSaveRunPersistence:
    def test_anomalies_written_to_db(self, db_path, repo_path):
        report = _minimal_report(
            ai_analysis=AIAnalysis(
                summary="test",
                anomalies=[
                    Anomaly(description="SQL injection", location="src/db.py:42", severity="high"),
                    Anomaly(description="Missing auth", location="src/api.py:10", severity="medium"),
                ],
            ),
        )
        save_run(db_path, report, repo_path=repo_path)

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT file, description, severity FROM anomalies ORDER BY file").fetchall()

        assert len(rows) == 2
        assert rows[0] == ("src/api.py", "Missing auth", "medium")
        assert rows[1] == ("src/db.py", "SQL injection", "high")

    def test_anomaly_file_parsed_from_location(self, db_path, repo_path):
        """File column should be everything before the first colon."""
        report = _minimal_report(
            ai_analysis=AIAnalysis(
                summary="test",
                anomalies=[
                    Anomaly(description="issue", location="path/to/file.py:99:extra", severity="low"),
                ],
            ),
        )
        save_run(db_path, report, repo_path=repo_path)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT file FROM anomalies").fetchone()
        assert row[0] == "path/to/file.py"

    def test_anomaly_location_without_colon(self, db_path, repo_path):
        """When location has no colon, the full string becomes the file column."""
        report = _minimal_report(
            ai_analysis=AIAnalysis(
                summary="test",
                anomalies=[
                    Anomaly(description="issue", location="whole_path.py", severity="low"),
                ],
            ),
        )
        save_run(db_path, report, repo_path=repo_path)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT file FROM anomalies").fetchone()
        assert row[0] == "whole_path.py"

    def test_security_signals_written_to_db(self, db_path, repo_path):
        sig = make_security_signal(file_path="src/auth.py", line_number=55)
        report = _minimal_report(
            ai_analysis=AIAnalysis(
                summary="test",
                security_signals=[sig],
            ),
        )
        save_run(db_path, report, repo_path=repo_path)

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT file, signal_type, severity FROM security_signals").fetchall()

        assert len(rows) == 1
        assert rows[0] == ("src/auth.py", "shell_invoke", "high")

    def test_save_run_with_no_anomalies_or_signals(self, db_path, repo_path):
        report = _minimal_report()
        save_run(db_path, report, repo_path=repo_path)

        with sqlite3.connect(db_path) as conn:
            anomaly_count = conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
            signal_count = conn.execute("SELECT COUNT(*) FROM security_signals").fetchone()[0]

        assert anomaly_count == 0
        assert signal_count == 0


# ---------------------------------------------------------------------------
# Fault tolerance — all readers gracefully handle missing DB
# ---------------------------------------------------------------------------


class TestFaultTolerance:
    def test_load_hotspots_nonexistent_db(self, tmp_path):
        assert load_hotspots(str(tmp_path / "gone.db"), "/r") == []

    def test_load_anomaly_patterns_nonexistent_db(self, tmp_path):
        assert load_anomaly_patterns(str(tmp_path / "gone.db"), "/r") == []

    def test_get_run_count_nonexistent_db(self, tmp_path):
        assert get_run_count(str(tmp_path / "gone.db"), "/r") == 0

    def test_save_run_bad_path_returns_uuid_without_raising(self):
        """save_run swallows errors — returns a UUID even if write fails."""
        # NUL-byte path is invalid on all platforms
        result = save_run("\x00bad", _minimal_report(), repo_path="/r")
        assert isinstance(result, str)
        assert len(result) == 36


# ---------------------------------------------------------------------------
# _report_from_dict — blast_graph field
# ---------------------------------------------------------------------------


class TestReportFromDictBlastGraph:
    """Tests for blast_graph parsing in _report_from_dict (exercised via load_run)."""

    def test_roundtrip_with_nodes_and_edges(self, db_path, repo_path):
        """A blast_graph stored via save_run is faithfully restored by load_run."""
        from pr_impact.models import BlastGraph, GraphEdge, GraphNode
        from pr_impact.history import load_run

        graph = BlastGraph(
            nodes=[
                GraphNode(id="src/a.py", path="src/a.py", type="changed",
                          distance=0, language="python", churn_score=None),
                GraphNode(id="src/b.py", path="src/b.py", type="affected",
                          distance=1, language="python", churn_score=3.5),
            ],
            edges=[GraphEdge(source="src/a.py", target="src/b.py", symbols=["foo", "bar"])],
        )
        report = _minimal_report(blast_graph=graph)
        save_run(db_path, report, repo_path)
        loaded = load_run(db_path, list(
            __import__("sqlite3").connect(db_path)
            .execute("SELECT uuid FROM runs ORDER BY id DESC LIMIT 1")
            .fetchone()
        )[0])
        assert loaded is not None
        assert loaded.blast_graph is not None
        assert len(loaded.blast_graph.nodes) == 2
        by_id = {n.id: n for n in loaded.blast_graph.nodes}
        assert by_id["src/a.py"].type == "changed"
        assert by_id["src/b.py"].churn_score == 3.5
        assert loaded.blast_graph.edges[0].symbols == ["foo", "bar"]

    def test_missing_blast_graph_deserialises_as_none(self, db_path, repo_path):
        """Reports saved without blast_graph deserialise with blast_graph=None."""
        from pr_impact.history import load_run

        report = _minimal_report()  # blast_graph defaults to None
        save_run(db_path, report, repo_path)
        loaded = load_run(db_path, list(
            __import__("sqlite3").connect(db_path)
            .execute("SELECT uuid FROM runs ORDER BY id DESC LIMIT 1")
            .fetchone()
        )[0])
        assert loaded is not None
        assert loaded.blast_graph is None

    def test_blast_graph_node_fields_fully_preserved(self, db_path, repo_path):
        """All GraphNode fields (id, path, type, distance, language, churn_score) survive a round-trip."""
        from pr_impact.models import BlastGraph, GraphNode
        from pr_impact.history import load_run

        n = GraphNode(id="deep/module.ts", path="deep/module.ts", type="affected",
                      distance=2, language="typescript", churn_score=12.0)
        report = _minimal_report(blast_graph=BlastGraph(nodes=[n], edges=[]))
        save_run(db_path, report, repo_path)
        run_uuid = list(
            __import__("sqlite3").connect(db_path)
            .execute("SELECT uuid FROM runs ORDER BY id DESC LIMIT 1")
            .fetchone()
        )[0]
        loaded = load_run(db_path, run_uuid)
        restored = loaded.blast_graph.nodes[0]
        assert restored.id == "deep/module.ts"
        assert restored.type == "affected"
        assert restored.distance == 2
        assert restored.language == "typescript"
        assert restored.churn_score == 12.0


# ---------------------------------------------------------------------------
# signal_annotations — schema, compute_signal_key, persistence, upsert
# ---------------------------------------------------------------------------


class TestSignalAnnotations:
    from pr_impact.history import (
        compute_signal_key as _csk,
        load_signal_annotations as _load,
        save_signal_annotation as _save,
    )

    def test_schema_creates_signal_annotations_table(self, db_path, repo_path):
        save_run(db_path, _minimal_report(), repo_path=repo_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_annotations'"
            ).fetchone()
        assert row is not None

    def test_compute_signal_key_is_deterministic(self):
        from pr_impact.history import compute_signal_key
        k1 = compute_signal_key("signal", "src/auth.py", "shell_invoke", "New shell invoke")
        k2 = compute_signal_key("signal", "src/auth.py", "shell_invoke", "New shell invoke")
        assert k1 == k2

    def test_compute_signal_key_is_16_hex_chars(self):
        from pr_impact.history import compute_signal_key
        k = compute_signal_key("dep", "evil-pkg", "typosquat", "desc")
        assert len(k) == 16
        assert all(c in "0123456789abcdef" for c in k)

    def test_compute_signal_key_differs_for_different_identifiers(self):
        from pr_impact.history import compute_signal_key
        k1 = compute_signal_key("signal", "src/a.py", "type_x", "desc")
        k2 = compute_signal_key("signal", "src/b.py", "type_x", "desc")
        assert k1 != k2

    def test_save_annotation_persists_all_fields(self, db_path, repo_path):
        from pr_impact.history import save_signal_annotation
        result = save_signal_annotation(
            db_path, repo_path, "testkey12345678",
            muted=True, mute_reason="false positive", assigned_to="alice",
        )
        assert result["muted"] is True
        assert result["mute_reason"] == "false positive"
        assert result["assigned_to"] == "alice"
        assert result["updated_at"] is not None
        assert result["signal_key"] == "testkey12345678"

    def test_save_annotation_upsert_updates_existing(self, db_path, repo_path):
        from pr_impact.history import save_signal_annotation
        save_signal_annotation(db_path, repo_path, "testkey12345678", muted=True)
        result = save_signal_annotation(
            db_path, repo_path, "testkey12345678", muted=False, assigned_to="bob"
        )
        assert result["muted"] is False
        assert result["assigned_to"] == "bob"

    def test_save_annotation_empty_string_clears_assigned_to(self, db_path, repo_path):
        from pr_impact.history import save_signal_annotation
        save_signal_annotation(db_path, repo_path, "testkey12345678", assigned_to="carol")
        result = save_signal_annotation(
            db_path, repo_path, "testkey12345678", assigned_to=""
        )
        assert result["assigned_to"] is None

    def test_load_annotations_returns_saved_entries(self, db_path, repo_path):
        from pr_impact.history import save_signal_annotation, load_signal_annotations
        save_signal_annotation(
            db_path, repo_path, "key0000000000001", muted=True, mute_reason="fp"
        )
        data = load_signal_annotations(db_path, repo_path, ["key0000000000001"])
        assert "key0000000000001" in data
        assert data["key0000000000001"]["muted"] is True

    def test_load_annotations_omits_missing_keys(self, db_path, repo_path):
        from pr_impact.history import load_signal_annotations
        # No annotations saved; result should be empty
        data = load_signal_annotations(db_path, repo_path, ["nonexistent_key_x"])
        assert data == {}

    def test_load_annotations_returns_empty_for_empty_key_list(self, db_path, repo_path):
        from pr_impact.history import load_signal_annotations
        data = load_signal_annotations(db_path, repo_path, [])
        assert data == {}
