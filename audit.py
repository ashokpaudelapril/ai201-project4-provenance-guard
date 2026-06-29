"""
Structured audit log backed by SQLite.

Every attribution decision — including all signal scores, the combined
confidence, the label, and any appeal — is stored here. The GET /log
endpoint surfaces recent entries.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "audit.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id                  TEXT    NOT NULL UNIQUE,
            creator_id                  TEXT    NOT NULL,
            timestamp                   TEXT    NOT NULL,
            text_preview                TEXT    NOT NULL,
            attribution                 TEXT    NOT NULL,
            confidence                  REAL    NOT NULL,
            llm_score                   REAL    NOT NULL,
            stylo_score                 REAL    NOT NULL,
            lexical_score               REAL,
            sentence_variance_score     REAL,
            ttr_score                   REAL,
            punctuation_diversity_score REAL,
            avg_word_length_score       REAL,
            long_word_ratio_score       REAL,
            label                       TEXT    NOT NULL,
            status                      TEXT    NOT NULL DEFAULT 'classified',
            appeal_reasoning            TEXT,
            appeal_timestamp            TEXT
        )
        """
    )
    # Migrate existing DB if lexical_score column is missing
    existing = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
    for col, definition in [
        ("lexical_score", "REAL"),
        ("avg_word_length_score", "REAL"),
        ("long_word_ratio_score", "REAL"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE audit_log ADD COLUMN {col} {definition}")
    conn.commit()


def log_submission(
    content_id: str,
    creator_id: str,
    text: str,
    attribution: str,
    confidence: float,
    llm_score: float,
    stylo_breakdown: dict,
    lexical_breakdown: dict,
    label: str,
) -> None:
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO audit_log (
            content_id, creator_id, timestamp, text_preview,
            attribution, confidence, llm_score,
            stylo_score, sentence_variance_score, ttr_score, punctuation_diversity_score,
            lexical_score, avg_word_length_score, long_word_ratio_score,
            label, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'classified')
        """,
        (
            content_id,
            creator_id,
            datetime.now(timezone.utc).isoformat(),
            text[:200],
            attribution,
            round(confidence, 4),
            round(llm_score, 4),
            round(stylo_breakdown["stylo_score"], 4),
            stylo_breakdown.get("sentence_variance_score"),
            stylo_breakdown.get("ttr_score"),
            stylo_breakdown.get("punctuation_diversity_score"),
            round(lexical_breakdown["lexical_score"], 4),
            lexical_breakdown.get("avg_word_length_score"),
            lexical_breakdown.get("long_word_ratio_score"),
            label,
        ),
    )
    conn.commit()


def log_appeal(content_id: str, reasoning: str) -> bool:
    """
    Update the audit log entry for content_id with appeal info.
    Returns True if the entry was found and updated, False otherwise.
    """
    conn = _get_conn()
    cursor = conn.execute(
        """
        UPDATE audit_log
        SET status = 'under_review',
            appeal_reasoning = ?,
            appeal_timestamp = ?
        WHERE content_id = ?
        """,
        (reasoning, datetime.now(timezone.utc).isoformat(), content_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_recent_entries(limit: int = 20) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT content_id, creator_id, timestamp, text_preview,
               attribution, confidence, llm_score, stylo_score, lexical_score,
               sentence_variance_score, ttr_score, punctuation_diversity_score,
               avg_word_length_score, long_word_ratio_score,
               label, status, appeal_reasoning, appeal_timestamp
        FROM audit_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_analytics() -> dict:
    conn = _get_conn()

    totals = conn.execute(
        """
        SELECT
            COUNT(*)                                            AS total,
            SUM(CASE WHEN attribution='likely_ai'    THEN 1 ELSE 0 END) AS likely_ai,
            SUM(CASE WHEN attribution='likely_human' THEN 1 ELSE 0 END) AS likely_human,
            SUM(CASE WHEN attribution='uncertain'    THEN 1 ELSE 0 END) AS uncertain,
            SUM(CASE WHEN status='under_review'      THEN 1 ELSE 0 END) AS appeals,
            AVG(confidence)                                     AS avg_confidence,
            AVG(llm_score)                                      AS avg_llm_score,
            AVG(stylo_score)                                    AS avg_stylo_score,
            AVG(lexical_score)                                  AS avg_lexical_score
        FROM audit_log
        """
    ).fetchone()

    total = totals["total"] or 0
    appeals = totals["appeals"] or 0

    return {
        "total_submissions": total,
        "attribution_counts": {
            "likely_ai": totals["likely_ai"] or 0,
            "likely_human": totals["likely_human"] or 0,
            "uncertain": totals["uncertain"] or 0,
        },
        "appeal_rate": round(appeals / total, 4) if total > 0 else 0.0,
        "total_appeals": appeals,
        "average_scores": {
            "confidence": round(totals["avg_confidence"] or 0, 4),
            "llm_score": round(totals["avg_llm_score"] or 0, 4),
            "stylo_score": round(totals["avg_stylo_score"] or 0, 4),
            "lexical_score": round(totals["avg_lexical_score"] or 0, 4),
        },
    }


def get_entry(content_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM audit_log WHERE content_id = ?", (content_id,)
    ).fetchone()
    return dict(row) if row else None
