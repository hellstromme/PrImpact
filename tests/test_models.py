"""Unit tests for pr_impact/models.py."""

from pr_impact.models import (
    AIAnalysis,
    ChangedFile,
    ChangedSymbol,
    DependencyIssue,
    HistoricalHotspot,
    ImpactReport,
    RunSummary,
    SemanticVerdict,
)


class TestChangedSymbol:
    """Tests for ChangedSymbol dataclass construction and defaults."""

    def test_v04_fields_have_correct_defaults(self):
        sym = ChangedSymbol(
            name="foo",
            kind="function",
            change_type="modified",
            signature_before="def foo():",
            signature_after="def foo(x):",
        )
        assert sym.params == []
        assert sym.decorators == []
        assert sym.return_type is None

    def test_v04_fields_can_be_populated(self):
        sym = ChangedSymbol(
            name="bar",
            kind="function",
            change_type="added",
            signature_before=None,
            signature_after="def bar(x: int) -> str:",
            params=["x: int"],
            decorators=["@cached"],
            return_type="str",
        )
        assert sym.params == ["x: int"]
        assert sym.decorators == ["@cached"]
        assert sym.return_type == "str"

    def test_required_fields_stored_correctly(self):
        sym = ChangedSymbol(
            name="MyClass",
            kind="class",
            change_type="deleted",
            signature_before="class MyClass:",
            signature_after=None,
        )
        assert sym.name == "MyClass"
        assert sym.kind == "class"
        assert sym.change_type == "deleted"
        assert sym.signature_before == "class MyClass:"
        assert sym.signature_after is None


class TestSemanticVerdict:
    """Tests for SemanticVerdict dataclass."""

    def test_all_four_fields(self):
        v = SemanticVerdict(
            file="a.py", symbol="login", verdict="risky", reason="signature changed"
        )
        assert v.file == "a.py"
        assert v.symbol == "login"
        assert v.verdict == "risky"
        assert v.reason == "signature changed"


class TestHistoricalHotspot:
    """Tests for HistoricalHotspot dataclass."""

    def test_two_fields(self):
        h = HistoricalHotspot(file="src/auth.py", appearances=5)
        assert h.file == "src/auth.py"
        assert h.appearances == 5


class TestRunSummary:
    """Tests for RunSummary dataclass."""

    def test_all_fields_populated(self):
        rs = RunSummary(
            id="abc-123",
            repo_path="/repo",
            pr_number=42,
            pr_title="fix: bug",
            base_sha="aaa",
            head_sha="bbb",
            created_at="2026-01-01T00:00:00",
            verdict="clean",
            blast_radius_count=3,
            anomaly_count=1,
            signal_count=0,
        )
        assert rs.id == "abc-123"
        assert rs.pr_number == 42
        assert rs.verdict == "clean"
        assert rs.blast_radius_count == 3
        assert rs.anomaly_count == 1
        assert rs.signal_count == 0

    def test_optional_fields_accept_none(self):
        rs = RunSummary(
            id="x",
            repo_path="/r",
            pr_number=None,
            pr_title=None,
            base_sha="a",
            head_sha="b",
            created_at="2026-01-01",
            verdict=None,
            blast_radius_count=0,
            anomaly_count=0,
            signal_count=0,
        )
        assert rs.pr_number is None
        assert rs.pr_title is None
        assert rs.verdict is None


class TestImpactReport:
    """Tests for ImpactReport dataclass defaults."""

    def test_historical_hotspots_defaults_to_empty_list(self):
        report = ImpactReport(
            pr_title="t",
            base_sha="a",
            head_sha="b",
            changed_files=[],
            blast_radius=[],
            interface_changes=[],
            ai_analysis=AIAnalysis(),
        )
        assert report.historical_hotspots == []
        assert report.dependency_issues == []

    def test_with_populated_hotspots(self):
        hotspots = [HistoricalHotspot(file="x.py", appearances=3)]
        report = ImpactReport(
            pr_title="t",
            base_sha="a",
            head_sha="b",
            changed_files=[],
            blast_radius=[],
            interface_changes=[],
            ai_analysis=AIAnalysis(),
            historical_hotspots=hotspots,
        )
        assert len(report.historical_hotspots) == 1
        assert report.historical_hotspots[0].file == "x.py"


class TestAIAnalysis:
    """Tests for AIAnalysis dataclass defaults."""

    def test_semantic_verdicts_defaults_to_empty_list(self):
        ai = AIAnalysis()
        assert ai.semantic_verdicts == []
        assert ai.security_signals == []
        assert ai.summary == ""
        assert ai.decisions == []
        assert ai.assumptions == []
        assert ai.anomalies == []
        assert ai.test_gaps == []


class TestDependencyIssue:
    """Tests for DependencyIssue dataclass."""

    def test_license_field_defaults_to_none(self):
        di = DependencyIssue(
            package_name="requests",
            issue_type="vulnerability",
            description="CVE-2023-1234",
            severity="high",
        )
        assert di.license is None
        assert di.package_name == "requests"
        assert di.issue_type == "vulnerability"
        assert di.severity == "high"

    def test_license_field_can_be_set(self):
        di = DependencyIssue(
            package_name="flask",
            issue_type="version_change",
            description="major bump",
            severity="medium",
            license="MIT",
        )
        assert di.license == "MIT"


class TestChangedFile:
    """Tests for ChangedFile dataclass."""

    def test_changed_symbols_defaults_to_empty_list(self):
        cf = ChangedFile(
            path="src/main.py",
            language="python",
            diff="@@ ...",
            content_before="",
            content_after="print('hi')",
        )
        assert cf.changed_symbols == []
        assert cf.path == "src/main.py"
        assert cf.language == "python"


class TestImpactReportBlastGraph:
    """Tests for ImpactReport serialization and blast_graph field."""

    def test_blast_graph_field_survives_asdict_roundtrip(self):
        """dataclasses.asdict preserves all blast_graph node and edge fields."""
        import dataclasses
        from pr_impact.models import AIAnalysis, BlastGraph, GraphEdge, GraphNode

        graph = BlastGraph(
            nodes=[
                GraphNode(id="a.py", path="a.py", type="changed",
                          distance=0, language="python", churn_score=None),
                GraphNode(id="b.py", path="b.py", type="affected",
                          distance=1, language="python", churn_score=2.5),
            ],
            edges=[GraphEdge(source="a.py", target="b.py", symbols=["myFn"])],
        )
        report = ImpactReport(
            pr_title="test",
            base_sha="aaa",
            head_sha="bbb",
            changed_files=[],
            blast_radius=[],
            interface_changes=[],
            ai_analysis=AIAnalysis(),
            dependency_issues=[],
            blast_graph=graph,
        )
        d = dataclasses.asdict(report)
        bg = d["blast_graph"]
        assert bg is not None
        assert len(bg["nodes"]) == 2
        assert bg["nodes"][0]["id"] == "a.py"
        assert bg["nodes"][0]["type"] == "changed"
        assert bg["nodes"][1]["churn_score"] == 2.5
        assert bg["edges"][0]["symbols"] == ["myFn"]

    def test_blast_graph_is_none_by_default(self):
        """ImpactReport.blast_graph defaults to None when not supplied."""
        from pr_impact.models import AIAnalysis
        report = ImpactReport(
            pr_title="",
            base_sha="a",
            head_sha="b",
            changed_files=[],
            blast_radius=[],
            interface_changes=[],
            ai_analysis=AIAnalysis(),
            dependency_issues=[],
        )
        assert report.blast_graph is None

    def test_graphnode_type_accepts_changed_and_affected(self):
        """GraphNode can be constructed with both valid type values."""
        from pr_impact.models import GraphNode
        changed = GraphNode(id="a.py", path="a.py", type="changed",
                            distance=0, language=None, churn_score=None)
        affected = GraphNode(id="b.py", path="b.py", type="affected",
                             distance=1, language=None, churn_score=None)
        assert changed.type == "changed"
        assert affected.type == "affected"
