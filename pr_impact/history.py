"""Historical pattern learning via a local SQLite database.

Records each analysis run so future runs can calibrate anomaly detection to the
specific codebase's conventions and identify architectural hotspots.

No external service required — the database is a plain SQLite file stored in the
repo at .primpact/history.db (or the path supplied via --history-db).
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from .models import (
    AIAnalysis,
    Anomaly,
    Assumption,
    BlastRadiusEntry,
    ChangedFile,
    ChangedSymbol,
    Decision,
    DependencyIssue,
    HistoricalHotspot,
    ImpactReport,
    InterfaceChange,
    RunSummary,
    SecuritySignal,
    SemanticVerdict,
    SourceLocation,
    TestGap,
)


# --- Schema ---

_CREATE_TABLES = """\
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path   TEXT    NOT NULL,
    base_sha    TEXT    NOT NULL,
    head_sha    TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    pr_number   INTEGER,
    pr_title    TEXT
);

CREATE TABLE IF NOT EXISTS blast_entries (
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    file        TEXT    NOT NULL,
    distance    INTEGER NOT NULL,
    churn_score REAL
);

CREATE TABLE IF NOT EXISTS anomalies (
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    file        TEXT    NOT NULL,
    description TEXT    NOT NULL,
    severity    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS security_signals (
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    file        TEXT    NOT NULL,
    signal_type TEXT    NOT NULL,
    severity    TEXT    NOT NULL
);
"""


_MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN uuid TEXT",
    "ALTER TABLE runs ADD COLUMN report_json TEXT",
]


def _connect(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database at *db_path*, initialise schema."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.executescript(_CREATE_TABLES)
    # Additive column migrations — silently skipped if columns already exist
    for migration in _MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    return conn


# --- Public API ---


def save_run(
    db_path: str,
    report: ImpactReport,
    repo_path: str,
    run_uuid: str | None = None,
) -> str:
    """Persist a completed analysis run to the history database.

    Returns the run UUID (generated if not provided). Failures are silently
    ignored — history is best-effort and must never affect the exit code or
    output of the main pipeline.
    """
    run_uuid = run_uuid or str(uuid.uuid4())
    try:
        conn = _connect(db_path)
        ts = datetime.now(timezone.utc).isoformat()

        # Extract pr_number from pr_title if it follows the "#123: ..." convention
        pr_number: int | None = None
        if report.pr_title and report.pr_title.startswith("#"):
            try:
                pr_number = int(report.pr_title.split(":")[0][1:])
            except (ValueError, IndexError):
                pass

        report_json = json.dumps(dataclasses.asdict(report), default=str)

        cur = conn.execute(
            "INSERT INTO runs (repo_path, base_sha, head_sha, timestamp, pr_number, pr_title, uuid, report_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (repo_path, report.base_sha, report.head_sha, ts, pr_number, report.pr_title, run_uuid, report_json),
        )
        run_id = cur.lastrowid

        for entry in report.blast_radius:
            conn.execute(
                "INSERT INTO blast_entries (run_id, file, distance, churn_score) VALUES (?, ?, ?, ?)",
                (run_id, entry.path, entry.distance, entry.churn_score),
            )

        for anomaly in report.ai_analysis.anomalies:
            # Best-effort file extraction from location string
            file_hint = anomaly.location.split(":")[0].strip() if ":" in anomaly.location else anomaly.location
            conn.execute(
                "INSERT INTO anomalies (run_id, file, description, severity) VALUES (?, ?, ?, ?)",
                (run_id, file_hint, anomaly.description, anomaly.severity),
            )

        for sig in report.ai_analysis.security_signals:
            conn.execute(
                "INSERT INTO security_signals (run_id, file, signal_type, severity) VALUES (?, ?, ?, ?)",
                (run_id, sig.location.file, sig.signal_type, sig.severity),
            )

        conn.commit()
        conn.close()
    except Exception:
        pass  # History is never fatal
    return run_uuid


def load_hotspots(db_path: str, repo_path: str, limit: int = 10) -> list[dict]:
    """Return files most frequently appearing in blast radii for this repo.

    Returns an empty list if the database does not exist or has too few runs.
    """
    if not os.path.exists(db_path):
        return []
    conn = None
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            """
            SELECT be.file, COUNT(*) AS appearances
            FROM blast_entries be
            JOIN runs r ON be.run_id = r.id
            WHERE r.repo_path = ?
            GROUP BY be.file
            ORDER BY appearances DESC
            LIMIT ?
            """,
            (repo_path, limit),
        ).fetchall()
        return [{"file": row[0], "appearances": row[1]} for row in rows]
    except Exception:
        return []  # History is best-effort; errors never affect the pipeline
    finally:
        if conn is not None:
            conn.close()


def load_anomaly_patterns(db_path: str, repo_path: str, limit: int = 10) -> list[dict]:
    """Return recurring anomaly descriptions for AI calibration.

    Only returns patterns seen in at least 2 separate runs (to avoid noise from
    one-off false positives).
    """
    if not os.path.exists(db_path):
        return []
    conn = None
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            """
            SELECT a.file, a.description, COUNT(DISTINCT a.run_id) AS run_count
            FROM anomalies a
            JOIN runs r ON a.run_id = r.id
            WHERE r.repo_path = ?
            GROUP BY a.file, a.description
            HAVING run_count >= 2
            ORDER BY run_count DESC
            LIMIT ?
            """,
            (repo_path, limit),
        ).fetchall()
        return [{"file": row[0], "description": row[1], "run_count": row[2]} for row in rows]
    except Exception:
        return []  # History is best-effort; errors never affect the pipeline
    finally:
        if conn is not None:
            conn.close()


def get_run_count(db_path: str, repo_path: str) -> int:
    """Return the number of recorded runs for this repo."""
    if not os.path.exists(db_path):
        return 0
    conn = None
    try:
        conn = _connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE repo_path = ?", (repo_path,)
        ).fetchone()[0]
        return count
    except Exception:
        return 0  # History is best-effort; errors never affect the pipeline
    finally:
        if conn is not None:
            conn.close()


def load_runs(
    db_path: str,
    repo_path: str,
    limit: int = 50,
    offset: int = 0,
) -> list[RunSummary]:
    """Return a paginated list of run summaries for the web UI dashboard.

    Only returns runs that were stored with a uuid and report_json (v1.0+).
    Returns an empty list on any error — best-effort, never affects the pipeline.
    """
    if not os.path.exists(db_path):
        return []
    conn = None
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            """
            SELECT uuid, repo_path, pr_number, pr_title, base_sha, head_sha,
                   timestamp, report_json
            FROM runs
            WHERE repo_path = ? AND uuid IS NOT NULL AND report_json IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            (repo_path, limit, offset),
        ).fetchall()

        summaries = []
        for row in rows:
            run_uuid, rp, pr_num, pr_title, base_sha, head_sha, ts, report_json_str = row
            try:
                data = json.loads(report_json_str)
            except (json.JSONDecodeError, TypeError):
                continue

            ai = data.get("ai_analysis", {})
            blast_radius = data.get("blast_radius", [])
            anomalies = ai.get("anomalies", [])
            security_signals = ai.get("security_signals", [])
            dep_issues = data.get("dependency_issues", [])

            # Derive verdict from stored data
            verdict: str | None = None
            verdict_data = ai.get("verdict")
            if verdict_data and isinstance(verdict_data, dict):
                verdict = verdict_data.get("status")

            summaries.append(RunSummary(
                id=run_uuid,
                repo_path=rp,
                pr_number=pr_num,
                pr_title=pr_title,
                base_sha=base_sha,
                head_sha=head_sha,
                created_at=ts,
                verdict=verdict,
                blast_radius_count=len(blast_radius),
                anomaly_count=len(anomalies),
                signal_count=len(security_signals) + len(dep_issues),
            ))
        return summaries
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


def load_run_summary(db_path: str, run_uuid: str) -> RunSummary | None:
    """Return a single RunSummary by UUID, or None if not found."""
    if not os.path.exists(db_path):
        return None
    conn = None
    try:
        conn = _connect(db_path)
        row = conn.execute(
            """SELECT uuid, repo_path, pr_number, pr_title, base_sha, head_sha,
                      timestamp, report_json
               FROM runs WHERE uuid = ?""",
            (run_uuid,),
        ).fetchone()
        if row is None:
            return None
        run_id_val, repo_path, pr_num, pr_title, base_sha, head_sha, ts, report_json_str = row
        if report_json_str is None:
            return None
        try:
            data = json.loads(report_json_str)
        except (json.JSONDecodeError, TypeError):
            return None
        ai = data.get("ai_analysis", {})
        verdict: str | None = None
        verdict_data = ai.get("verdict")
        if verdict_data and isinstance(verdict_data, dict):
            verdict = verdict_data.get("status")
        return RunSummary(
            id=run_id_val,
            repo_path=repo_path,
            pr_number=pr_num,
            pr_title=pr_title,
            base_sha=base_sha,
            head_sha=head_sha,
            created_at=ts,
            verdict=verdict,
            blast_radius_count=len(data.get("blast_radius", [])),
            anomaly_count=len(ai.get("anomalies", [])),
            signal_count=len(ai.get("security_signals", [])) + len(data.get("dependency_issues", [])),
        )
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


def _report_from_dict(data: dict) -> ImpactReport:
    """Reconstruct an ImpactReport from the dict produced by dataclasses.asdict().

    Private to history.py — only used when rehydrating stored runs. Kept here
    rather than in reporter.py to preserve reporter's single rendering responsibility
    and avoid a history→reporter coupling.
    """

    def _source_location(d: dict) -> SourceLocation:
        return SourceLocation(
            file=d.get("file", ""),
            line=d.get("line"),
            symbol=d.get("symbol"),
        )

    def _security_signal(d: dict) -> SecuritySignal:
        loc = d.get("location", {})
        return SecuritySignal(
            description=d.get("description", ""),
            location=_source_location(loc) if isinstance(loc, dict) else SourceLocation(file=str(loc)),
            signal_type=d.get("signal_type", ""),
            severity=d.get("severity", "medium"),
            why_unusual=d.get("why_unusual", ""),
            suggested_action=d.get("suggested_action", ""),
        )

    def _ai_analysis(d: dict) -> AIAnalysis:
        return AIAnalysis(
            summary=d.get("summary", ""),
            decisions=[
                Decision(
                    description=x.get("description", ""),
                    rationale=x.get("rationale", ""),
                    risk=x.get("risk", ""),
                )
                for x in d.get("decisions", [])
            ],
            assumptions=[
                Assumption(
                    description=x.get("description", ""),
                    location=x.get("location", ""),
                    risk=x.get("risk", ""),
                )
                for x in d.get("assumptions", [])
            ],
            anomalies=[
                Anomaly(
                    description=x.get("description", ""),
                    location=x.get("location", ""),
                    severity=x.get("severity", "medium"),
                )
                for x in d.get("anomalies", [])
            ],
            test_gaps=[
                TestGap(
                    behaviour=x.get("behaviour", ""),
                    location=x.get("location", ""),
                    severity=x.get("severity", "medium"),
                    gap_type=x.get("gap_type", ""),
                )
                for x in d.get("test_gaps", [])
            ],
            security_signals=[_security_signal(x) for x in d.get("security_signals", [])],
            semantic_verdicts=[
                SemanticVerdict(
                    file=x.get("file", ""),
                    symbol=x.get("symbol", ""),
                    verdict=x.get("verdict", "normal"),
                    reason=x.get("reason", ""),
                )
                for x in d.get("semantic_verdicts", [])
            ],
        )

    def _changed_file(d: dict) -> ChangedFile:
        return ChangedFile(
            path=d.get("path", ""),
            language=d.get("language", "unknown"),
            diff=d.get("diff", ""),
            content_before=d.get("content_before", ""),
            content_after=d.get("content_after", ""),
            changed_symbols=[
                ChangedSymbol(
                    name=x.get("name", ""),
                    kind=x.get("kind", "file"),
                    change_type=x.get("change_type", ""),
                    signature_before=x.get("signature_before"),
                    signature_after=x.get("signature_after"),
                    params=x.get("params", []),
                    decorators=x.get("decorators", []),
                    return_type=x.get("return_type"),
                )
                for x in d.get("changed_symbols", [])
            ],
        )

    return ImpactReport(
        pr_title=data.get("pr_title", ""),
        base_sha=data.get("base_sha", ""),
        head_sha=data.get("head_sha", ""),
        changed_files=[_changed_file(f) for f in data.get("changed_files", [])],
        blast_radius=[
            BlastRadiusEntry(
                path=x.get("path", ""),
                distance=x.get("distance", 1),
                imported_symbols=x.get("imported_symbols", []),
                churn_score=x.get("churn_score"),
            )
            for x in data.get("blast_radius", [])
        ],
        interface_changes=[
            InterfaceChange(
                file=x.get("file", ""),
                symbol=x.get("symbol", ""),
                before=x.get("before", ""),
                after=x.get("after", ""),
                callers=x.get("callers", []),
            )
            for x in data.get("interface_changes", [])
        ],
        ai_analysis=_ai_analysis(data.get("ai_analysis", {})),
        dependency_issues=[
            DependencyIssue(
                package_name=x.get("package_name", ""),
                issue_type=x.get("issue_type", ""),
                description=x.get("description", ""),
                severity=x.get("severity", "medium"),
                license=x.get("license"),
            )
            for x in data.get("dependency_issues", [])
        ],
        historical_hotspots=[
            HistoricalHotspot(file=x.get("file", ""), appearances=x.get("appearances", 0))
            for x in data.get("historical_hotspots", [])
        ],
    )


def load_run(db_path: str, run_uuid: str) -> ImpactReport | None:
    """Rehydrate a single ImpactReport from the history database by UUID.

    Returns None if the run is not found or cannot be deserialised.
    """
    if not os.path.exists(db_path):
        return None
    conn = None
    try:
        conn = _connect(db_path)
        row = conn.execute(
            "SELECT report_json FROM runs WHERE uuid = ?",
            (run_uuid,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        data = json.loads(row[0])
        return _report_from_dict(data)
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()
