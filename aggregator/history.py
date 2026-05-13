"""
Query / incident history backed by SQLite.

Every /query that produces notable signals (errors, error traces, correlations,
or an RCA result) is recorded. A coarse `signature` (target + the set of
*failure* correlation kinds) lets us answer "has this happened before?" —
distinguishing a new failure mode from a recurring one. The signature is
deliberately independent of whether AI RCA ran, so a plain query and an
"Analyze with AI" query for the same incident land in the same bucket.

Repeatedly querying the *same ongoing* incident does NOT inflate the recurrence
count: if the most recent record for this (target, signature) was touched within
`_SAME_INCIDENT_WINDOW_SEC`, we update that row in place (refreshing `last_seen`
and the latest signal snapshot) instead of inserting a new occurrence. A genuinely
new occurrence is only logged once activity has gone quiet for that long. A plain
re-query never erases an RCA already attached to the occurrence — RCA columns are
merged (COALESCE), so the analysis survives until a newer one replaces it.

The DB lives at settings.history_db_path (persisted via a Docker volume).
Uses the stdlib sqlite3 module; blocking calls run in a worker thread.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Query

from aggregator.config import settings
from aggregator.core.suspicious_absence import SUSPICIOUS_ABSENCE_KINDS
from aggregator.models.result import HistoryInfo, HistoryOccurrence, RecurrenceInfo, UnifiedResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])

_MAX_PER_TARGET = 200            # keep at most this many rows per target
_OCCURRENCES_IN_RESPONSE = 5     # recent prior occurrences embedded in a /query response
_SAME_INCIDENT_WINDOW_SEC = 10 * 60   # re-queries within this gap fold into the same occurrence

# Caps on the evidence we freeze into signals_snapshot. Bounded so the table
# stays small even after hundreds of occurrences — Loki/Prom retention is the
# source of truth within their windows; this is the after-retention fallback.
_SNAPSHOT_LOG_LINES   = 30
_SNAPSHOT_TRACES      = 15
_SNAPSHOT_LOG_MSG_LEN = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  last_seen TEXT,
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
  signals_snapshot TEXT,
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
        # Upgrade DBs created before the last_seen column existed.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(query_history)")}
        if "last_seen" not in cols:
            conn.execute("ALTER TABLE query_history ADD COLUMN last_seen TEXT")
        if "signals_snapshot" not in cols:
            conn.execute("ALTER TABLE query_history ADD COLUMN signals_snapshot TEXT")
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

# Many correlation kinds are different *detectors* of the same underlying
# failure dimension (e.g. `error_spike` is a threshold rule, `error_rate_anomaly`
# a z-score rule on the same series). Whether each individual rule fires depends
# on thresholds and window variance, so the raw kind set jitters between queries
# of one incident. For fingerprinting we fold them down to coarse families;
# anything unrecognised maps to itself so new kinds aren't silently merged.
_CORRELATION_FAMILY: dict[str, str] = {
    "error_spike":                  "errors",
    "error_rate_anomaly":           "errors",
    "log_error_burst":              "errors",
    "error_metric_log_correlation": "errors",
    "latency_spike":                "latency",
    "latency_trace_correlation":    "latency",
    "container_restart":            "restarts",
}


def compute_signature(target: str, correlation_kinds: list[str]) -> str:
    """Coarse incident fingerprint: same target + same set of failure *families*
    → same signature.

    Deliberately NOT a function of whether AI RCA ran (the free-form root-cause
    text used to be folded in, which split otherwise-identical incidents into
    separate "with-RCA" / "without-RCA" buckets), nor of suspicious-absence
    events (`logs_unavailable`, `traffic_without_traces`, …) — those describe
    telemetry gaps rather than the failure itself and flicker with backend state.
    """
    families = sorted({
        _CORRELATION_FAMILY.get(kk, kk)
        for kk in (k.strip().lower() for k in correlation_kinds if k)
        if kk not in SUSPICIOUS_ABSENCE_KINDS
    })
    parts = [(target or "").strip().lower(), ",".join(families)]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ── Evidence snapshot ──────────────────────────────────────────────────────


def _build_signals_snapshot(result: UnifiedResult) -> str | None:
    """Freeze a bounded slice of the raw evidence into JSON.

    Stored alongside the summary so that *after* Loki/Prom/Jaeger have aged the
    original data out of their retention windows, we can still show what
    actually happened at the time of the incident — and re-run RCA against it.

    Deliberately not the full result: we keep top error logs, top error traces
    (with span counts but not every span), and metric latest/peak only (no full
    time-series — that would dominate the row size). Sized for ~50–200 KB.
    """
    from aggregator.models.signals import Severity

    log_lines = [
        ll for ll in (result.logs.lines or [])
        if ll.severity in (Severity.ERROR, Severity.CRITICAL)
    ] or list(result.logs.lines or [])
    log_lines = log_lines[:_SNAPSHOT_LOG_LINES]

    snap_logs = [
        {
            "ts": ll.timestamp.isoformat() if ll.timestamp else None,
            "severity": str(ll.severity),
            "message": (ll.message or "")[:_SNAPSHOT_LOG_MSG_LEN],
        }
        for ll in log_lines
    ]

    traces = list(result.traces.traces or [])
    # Surface error traces first, then by duration so the slowest are kept.
    traces.sort(key=lambda t: (not t.has_errors, -(t.duration_ms or 0)))
    snap_traces = []
    for t in traces[:_SNAPSHOT_TRACES]:
        root = t.root_span
        err_span = next((s for s in t.spans if s.is_error), None)
        snap_traces.append({
            "trace_id": t.trace_id,
            "root_service": t.root_service,
            "operation": root.operation_name if root else None,
            "duration_ms": round(t.duration_ms or 0, 2),
            "span_count": len(t.spans),
            "is_error": t.has_errors,
            "error_message": (err_span.tags.get("error") or err_span.tags.get("otel.status_description")) if err_span else None,
        })

    snap_metrics = [
        {
            "name": s.name,
            "labels": s.labels,
            "latest": s.latest_value,
            "peak": s.peak_value,
            "sample_count": len(s.samples or []),
        }
        for s in (result.metrics.series or [])
    ]

    snap_corrs = [
        {
            "kind": c.kind,
            "severity": c.severity,
            "description": c.description,
            "timestamp": c.timestamp.isoformat() if c.timestamp else None,
            "confidence": c.confidence,
            "related_metric": c.related_metric,
            "related_log_sample": c.related_log_sample,
            "related_trace_id": c.related_trace_id,
        }
        for c in (result.correlations or [])
    ]

    payload = {
        "window": {
            "start": result.meta.window_start.isoformat() if result.meta.window_start else None,
            "end":   result.meta.window_end.isoformat()   if result.meta.window_end   else None,
        },
        "totals": {
            "log_lines": result.logs.total_lines,
            "error_count": result.logs.error_count,
            "warn_count":  result.logs.warn_count,
            "trace_count": len(result.traces.traces or []),
            "error_trace_count": result.traces.error_trace_count,
        },
        "logs":         snap_logs,
        "traces":       snap_traces,
        "metrics":      snap_metrics,
        "correlations": snap_corrs,
    }
    try:
        return json.dumps(payload, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


# ── Record + recurrence lookup ─────────────────────────────────────────────

def _record_sync(row: dict) -> RecurrenceInfo:
    conn = _connect()
    try:
        now = _parse_dt(row["created_at"]) or datetime.now(tz=timezone.utc)

        rows = conn.execute(
            "SELECT id, created_at, last_seen, rca_summary, rca_root_cause, rca_confidence "
            "FROM query_history WHERE target=? AND signature=? ORDER BY created_at DESC",
            (row["target"], row["signature"]),
        ).fetchall()

        # If the most recent record for this signature was touched very recently,
        # this query is still about the *same* occurrence — fold into that row
        # rather than logging a fresh one.
        merge_id: int | None = None
        if rows:
            latest = rows[0]
            last_activity = _parse_dt(latest["last_seen"]) or _parse_dt(latest["created_at"])
            if last_activity and (now - last_activity).total_seconds() <= _SAME_INCIDENT_WINDOW_SEC:
                merge_id = latest["id"]

        # Recurrence is computed over PRIOR occurrences — every existing row
        # except the one (if any) we're about to merge this query into.
        prior = [r for r in rows if r["id"] != merge_id]
        occurrences = [
            HistoryOccurrence(
                created_at=_parse_dt(r["created_at"]) or now,
                rca_summary=r["rca_summary"],
                rca_root_cause=r["rca_root_cause"],
                rca_confidence=r["rca_confidence"],
            )
            for r in prior[:_OCCURRENCES_IN_RESPONSE]
        ]
        recurrence = RecurrenceInfo(
            count=len(prior),
            first_seen=_parse_dt(prior[-1]["created_at"]) if prior else None,
            last_seen=(_parse_dt(prior[0]["last_seen"]) or _parse_dt(prior[0]["created_at"])) if prior else None,
            occurrences=occurrences,
        )

        if merge_id is not None:
            # Refresh the latest-signal snapshot in place. RCA columns are merged,
            # not blindly overwritten: a plain re-query (no "Analyze") carries
            # NULL RCA fields, and clobbering a previously-recorded RCA with those
            # NULLs would silently lose the analysis. So keep the existing RCA
            # unless this query produced a fresh one (COALESCE picks the new value
            # when present, else the stored one); rca_performed never goes 1 → 0.
            conn.execute(
                "UPDATE query_history SET last_seen=?, window_start=?, window_end=?, "
                " error_count=?, error_trace_count=?, correlation_kinds=?, "
                " rca_performed=MAX(rca_performed, ?), "
                " rca_summary=COALESCE(?, rca_summary), "
                " rca_root_cause=COALESCE(?, rca_root_cause), "
                " rca_confidence=COALESCE(?, rca_confidence), "
                " signals_snapshot=COALESCE(?, signals_snapshot) "
                "WHERE id=?",
                (
                    row["created_at"], row["window_start"], row["window_end"],
                    row["error_count"], row["error_trace_count"], row["correlation_kinds"],
                    row["rca_performed"], row["rca_summary"], row["rca_root_cause"],
                    row["rca_confidence"], row["signals_snapshot"], merge_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO query_history "
                "(created_at, last_seen, target, namespace, window_start, window_end, error_count, "
                " error_trace_count, correlation_kinds, rca_performed, rca_summary, "
                " rca_root_cause, rca_confidence, signals_snapshot, signature) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["created_at"], row["created_at"], row["target"], row["namespace"],
                    row["window_start"], row["window_end"], row["error_count"],
                    row["error_trace_count"], row["correlation_kinds"], row["rca_performed"],
                    row["rca_summary"], row["rca_root_cause"], row["rca_confidence"],
                    row["signals_snapshot"], row["signature"],
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
    sig = compute_signature(result.meta.target, correlation_kinds)
    snapshot = _build_signals_snapshot(result)
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
        "signals_snapshot": snapshot,
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
