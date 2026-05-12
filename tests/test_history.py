from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aggregator import history


@pytest.fixture()
def history_db(tmp_path, monkeypatch):
    """Point the history module at a throwaway SQLite file and init the schema."""
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(history.settings, "history_db_path", str(db_path))
    history._init_sync()
    return db_path


_BASE = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _row(offset_sec: float, *, signature: str = "sig-a", target: str = "service-b", **over) -> dict:
    base = {
        "created_at": (_BASE + timedelta(seconds=offset_sec)).isoformat(),
        "target": target,
        "namespace": "demo",
        "window_start": None,
        "window_end": None,
        "error_count": 3,
        "error_trace_count": 1,
        "correlation_kinds": "log_spike",
        "rca_performed": 0,
        "rca_summary": None,
        "rca_root_cause": None,
        "rca_confidence": None,
        "signature": signature,
    }
    base.update(over)
    return base


def _count_rows(db_path) -> int:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM query_history").fetchone()[0]
    finally:
        conn.close()


def test_repeated_queries_in_same_window_do_not_inflate_recurrence(history_db, monkeypatch):
    monkeypatch.setattr(history, "_SAME_INCIDENT_WINDOW_SEC", 10 * 60)

    # First notable query — a brand-new failure mode.
    r1 = history._record_sync(_row(0))
    assert r1.count == 0
    assert _count_rows(history_db) == 1

    # Re-querying the same incident a minute later, then five minutes later:
    # still the same occurrence, still count 0, still a single row.
    r2 = history._record_sync(_row(60))
    r3 = history._record_sync(_row(5 * 60))
    assert r2.count == 0
    assert r3.count == 0
    assert _count_rows(history_db) == 1


def test_quiet_gap_starts_a_new_occurrence(history_db, monkeypatch):
    monkeypatch.setattr(history, "_SAME_INCIDENT_WINDOW_SEC", 10 * 60)

    history._record_sync(_row(0))
    history._record_sync(_row(60))            # folds into occurrence #1

    # 20 minutes after the last activity — the incident has recurred.
    r = history._record_sync(_row(60 + 20 * 60))
    assert r.count == 1
    assert r.first_seen == _BASE
    assert _count_rows(history_db) == 2

    # And re-querying that fresh occurrence still reports a single prior recurrence.
    r2 = history._record_sync(_row(60 + 20 * 60 + 30))
    assert r2.count == 1
    assert _count_rows(history_db) == 2


def test_distinct_signatures_are_tracked_separately(history_db):
    history._record_sync(_row(0, signature="sig-a"))
    r = history._record_sync(_row(30, signature="sig-b"))
    assert r.count == 0
    assert _count_rows(history_db) == 2


def test_signature_buckets_same_incident_together():
    base = history.compute_signature("service-b", ["log_error_burst", "error_spike"])

    # Order of correlation kinds doesn't matter.
    assert history.compute_signature("service-b", ["error_spike", "log_error_burst"]) == base

    # Different error *detectors* (threshold vs z-score vs cross-signal) all fold
    # into the "errors" family — so the bucket doesn't jitter as those toggle.
    assert history.compute_signature("service-b", ["error_rate_anomaly"]) == base
    assert history.compute_signature(
        "service-b", ["error_spike", "error_rate_anomaly", "log_error_burst", "error_metric_log_correlation"]
    ) == base

    # Suspicious-absence events (telemetry gaps) don't shift the bucket — so a
    # query where Jaeger happened to have no traces still folds in. (And the
    # signature is, by construction, the same whether or not AI RCA ran.)
    assert history.compute_signature(
        "service-b", ["log_error_burst", "error_spike", "traffic_without_traces", "logs_unavailable"]
    ) == base

    # A genuinely different failure dimension, or a different service, is different.
    assert history.compute_signature("service-b", ["latency_spike"]) != base
    assert history.compute_signature("service-b", ["error_spike", "latency_spike"]) != base
    assert history.compute_signature("service-a", ["log_error_burst", "error_spike"]) != base

    # All-absence correlations collapse to the "just <something> on this target"
    # bucket (target + empty family set) — fine, they're not a failure mode.
    assert history.compute_signature("service-b", ["traffic_without_traces"]) == history.compute_signature("service-b", [])


def test_merge_refreshes_latest_signal_snapshot(history_db, monkeypatch):
    monkeypatch.setattr(history, "_SAME_INCIDENT_WINDOW_SEC", 10 * 60)

    history._record_sync(_row(0, error_count=3))
    history._record_sync(_row(120, error_count=9, rca_performed=1, rca_summary="db pool exhausted"))

    rows = history._recent_sync("service-b", 10)
    assert len(rows) == 1
    assert rows[0]["error_count"] == 9
    assert rows[0]["rca_summary"] == "db pool exhausted"
    assert rows[0]["created_at"] == _BASE.isoformat()                       # first seen, unchanged
    assert rows[0]["last_seen"] == (_BASE + timedelta(seconds=120)).isoformat()


def test_plain_requery_does_not_erase_a_recorded_rca(history_db, monkeypatch):
    monkeypatch.setattr(history, "_SAME_INCIDENT_WINDOW_SEC", 10 * 60)

    # Query + "Analyze with AI": the occurrence gets an RCA attached.
    history._record_sync(_row(0, rca_performed=1, rca_summary="db pool exhausted",
                              rca_root_cause="connection pool maxed out", rca_confidence=0.8))
    # A plain re-query a minute later (no RCA) folds into the same occurrence —
    # and must NOT wipe the analysis.
    history._record_sync(_row(60, error_count=11))

    rows = history._recent_sync("service-b", 10)
    assert len(rows) == 1
    assert rows[0]["error_count"] == 11                  # signal snapshot refreshed
    assert rows[0]["rca_performed"] == 1                 # RCA preserved
    assert rows[0]["rca_summary"] == "db pool exhausted"
    assert rows[0]["rca_root_cause"] == "connection pool maxed out"
    assert rows[0]["rca_confidence"] == 0.8


def test_newer_rca_replaces_an_older_one_on_merge(history_db, monkeypatch):
    monkeypatch.setattr(history, "_SAME_INCIDENT_WINDOW_SEC", 10 * 60)

    history._record_sync(_row(0, rca_performed=1, rca_summary="first guess", rca_confidence=0.4))
    history._record_sync(_row(60, rca_performed=1, rca_summary="second, better guess", rca_confidence=0.9))

    rows = history._recent_sync("service-b", 10)
    assert len(rows) == 1
    assert rows[0]["rca_summary"] == "second, better guess"
    assert rows[0]["rca_confidence"] == 0.9
