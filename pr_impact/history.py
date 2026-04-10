"""Historical pattern learning via a local SQLite database.

Records each analysis run so future runs can calibrate anomaly detection to the
specific codebase's conventions and identify architectural hotspots.

No external service required — the database is a plain SQLite file stored in the
repo at .primpact/history.db (or the path supplied via --history-db).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from .models import ImpactReport


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


def _connect(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database at *db_path*, initialise schema."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.executescript(_CREATE_TABLES)
    conn.commit()
    return conn


# --- Public API ---


def save_run(db_path: str, report: ImpactReport, repo_path: str) -> None:
    """Persist a completed analysis run to the history database.

    Failures are silently ignored — history is best-effort and must never
    affect the exit code or output of the main pipeline.
    """
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

        cur = conn.execute(
            "INSERT INTO runs (repo_path, base_sha, head_sha, timestamp, pr_number, pr_title) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (repo_path, report.base_sha, report.head_sha, ts, pr_number, report.pr_title),
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
                (run_id, sig.file_path, sig.signal_type, sig.severity),
            )

        conn.commit()
        conn.close()
    except Exception:
        pass  # History is never fatal


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
        return []
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
        return []
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
        return 0
    finally:
        if conn is not None:
            conn.close()
