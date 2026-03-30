"""Shared fixtures and factories used across the test suite."""

from pr_impact.models import (
    AIAnalysis,
    Anomaly,
    Assumption,
    BlastRadiusEntry,
    ChangedFile,
    Decision,
    ImpactReport,
    InterfaceChange,
    TestGap,
)


def make_file(
    path: str = "module.py",
    language: str = "python",
    diff: str = "",
    before: str = "",
    after: str = "",
) -> ChangedFile:
    return ChangedFile(
        path=path,
        language=language,
        diff=diff,
        content_before=before,
        content_after=after,
    )


def make_report(**overrides) -> ImpactReport:
    """Return a fully populated ImpactReport suitable for reporter tests."""
    defaults: dict = {
        "pr_title": "feat: add login",
        "base_sha": "abc1234567",
        "head_sha": "def5678901",
        "changed_files": [
            make_file(
                path="src/auth.py", before="def login(): pass\n", after="def login(user): pass\n"
            ),
        ],
        "blast_radius": [
            BlastRadiusEntry(
                path="src/consumer.py",
                distance=1,
                imported_symbols=["login"],
                churn_score=3.0,
            )
        ],
        "interface_changes": [
            InterfaceChange(
                file="src/auth.py",
                symbol="login",
                before="def login()",
                after="def login(user)",
                callers=["src/consumer.py"],
            )
        ],
        "ai_analysis": AIAnalysis(
            summary="The login function now requires a user argument.",
            decisions=[
                Decision(
                    description="Add user param", rationale="Needed for auth", risk="Breaks callers"
                )
            ],
            assumptions=[
                Assumption(
                    description="user is not None",
                    location="src/auth.py:login",
                    risk="AttributeError",
                )
            ],
            anomalies=[
                Anomaly(description="Direct DB call", location="src/auth.py:5", severity="high")
            ],
            test_gaps=[TestGap(behaviour="login with invalid user", location="src/auth.py:login")],
        ),
    }
    defaults.update(overrides)
    return ImpactReport(**defaults)
