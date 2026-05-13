"""
Query / incident history backed by SQLite.

Every /query that produces notable signals (errors, error traces, correlations,
or an RCA result) is recorded. A coarse `signature` (target + correlation kinds
+ a normalised first line of the RCA root cause) lets us answer "has this
happened before?" — distinguishing a new failure mode from a recurring one.

The DB lives at settings.history_db_path (persisted via a Docker volume).
Uses the stdlib sqlite3 module; blocking calls run in a worker thread.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query

from aggregator.config import settings
from aggregator.models.result import HistoryInfo, HistoryOccurrence, RecurrenceInfo, UnifiedResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])

_MAX_PER_TARGET = 200            # keep at most this many rows per target
_OCCURRENCES_IN_RESPONSE = 5     # recent prior occurrences embedded in a /query response

_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  target TEXT NOT NULL,
  namespace TEXT,
  window_start TEXT,
  window_end TEXT,
  error_count INTEGER,
  error_trace_count INTEGER,
  correlation_kinds TEXT,
  rca_performed INTEGER,
  rca_summary TEXT,
  rca_root_cause TEXT,
  rca_confidence REAL,
  signature TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_target_created ON query_history(target, created_at);
CREATE INDEX IF NOT EXISTS idx_history_target_sig ON query_history(target, signature);
"""


# ── DB plumbing ────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    p = Path(settings.history_db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def _init_sync() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


async def init_db() -> None:
    try:
        await asyncio.to_thread(_init_sync)
        logger.info("Query history DB ready at %s", settings.history_db_path)
    except Exception as exc:  # never block startup on history
        logger.warning("Could not initialise query history DB: %s", exc)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ── Signature ──────────────────────────────────────────────────────────────

_HEX_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def compute_signature(target: str, correlation_kinds: list[str], rca_root_cause: str | None) -> str:
    """Coarse incident fingerprint — same target + same correlation kinds + same
    normalised RCA root-cause first line → same signature."""
    parts = [
        (target or "").strip().lower(),
        ",".join(sorted({(k or "").strip().lower() for k in correlation_kinds if k})),
    ]
    if rca_root_cause:
        first = rca_root_cause.strip().split(". ")[0][:200].lower()
        first = _HEX_RE.sub("ID", first)       # mask hex ids first
        first = _NUM_RE.sub("N", first)        # then remaining numbers (e.g. "30s" → "Ns")
        first = re.sub(r"\s+", " ", first).strip()
        parts.append(first)
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ── Record + recurrence lookup ─────────────────────────────────────────────

def _record_sync(row: dict) -> RecurrenceInfo:
    conn = _connect()
    try:
        # Recurrence is computed over PRIOR rows (before inserting this one).
        prior = conn.execute(
            "SELECT created_at, rca_summary, rca_confidence FROM query_history "
            "WHERE target=? AND signature=? ORDER BY created_at DESC",
            (row["target"], row["signature"]),
        ).fetchall()

        occurrences = [
            HistoryOccurrence(
                created_at=_parse_dt(r["created_at"]) or datetime.now(tz=timezone.utc),
                rca_summary=r["rca_summary"],
                rca_confidence=r["rca_confidence"],
            )
            for r in prior[:_OCCURRENCES_IN_RESPONSE]
        ]
        recurrence = RecurrenceInfo(
            count=len(prior),
            first_seen=_parse_dt(prior[-1]["created_at"]) if prior else None,
            last_seen=_parse_dt(prior[0]["created_at"]) if prior else None,
            occurrences=occurrences,
        )

        conn.execute(
            "INSERT INTO query_history "
            "(created_at, target, namespace, window_start, window_end, error_count, "
            " error_trace_count, correlation_kinds, rca_performed, rca_summary, "
            " rca_root_cause, rca_confidence, signature) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["created_at"], row["target"], row["namespace"], row["window_start"],
                row["window_end"], row["error_count"], row["error_trace_count"],
                row["correlation_kinds"], row["rca_performed"], row["rca_summary"],
                row["rca_root_cause"], row["rca_confidence"], row["signature"],
            ),
        )
        conn.execute(
            "DELETE FROM query_history WHERE target=? AND id NOT IN "
            "(SELECT id FROM query_history WHERE target=? ORDER BY created_at DESC LIMIT ?)",
            (row["target"], row["target"], _MAX_PER_TARGET),
        )
        conn.commit()
        return recurrence
    finally:
        conn.close()


async def record(result: UnifiedResult) -> HistoryInfo | None:
    """Record a notable query and return its HistoryInfo (signature + prior recurrence)."""
    correlation_kinds = [c.kind for c in result.correlations]
    rca = result.rca
    sig = compute_signature(
        result.meta.target,
        correlation_kinds,
        rca.root_cause if rca.performed else None,
    )
    row = {
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "target": result.meta.target,
        "namespace": result.meta.namespace,
        "window_start": result.meta.window_start.isoformat() if result.meta.window_start else None,
        "window_end": result.meta.window_end.isoformat() if result.meta.window_end else None,
        "error_count": result.logs.error_count,
        "error_trace_count": result.traces.error_trace_count,
        "correlation_kinds": ",".join(correlation_kinds),
        "rca_performed": 1 if rca.performed else 0,
        "rca_summary": rca.summary if rca.performed else None,
        "rca_root_cause": rca.root_cause if rca.performed else None,
        "rca_confidence": rca.confidence if rca.performed else None,
        "signature": sig,
    }
    try:
        recurrence = await asyncio.to_thread(_record_sync, row)
    except Exception as exc:
        logger.warning("Could not record query history: %s", exc)
        return None
    return HistoryInfo(signature=sig, recurrence=recurrence)


# ── /history endpoints ─────────────────────────────────────────────────────

def _recent_sync(target: str | None, limit: int) -> list[dict]:
    conn = _connect()
    try:
        if target:
            cur = conn.execute(
                "SELECT * FROM query_history WHERE target=? ORDER BY created_at DESC LIMIT ?",
                (target, limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM query_history ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@router.get("")
async def list_history(
    target: str | None = Query(default=None, description="Filter to one target service"),
    limit: int = Query(default=25, ge=1, le=200),
) -> list[dict]:
    """Recent query-history records (most recent first)."""
    try:
        return await asyncio.to_thread(_recent_sync, target, limit)
    except Exception as exc:
        logger.warning("Could not read query history: %s", exc)
        return []
