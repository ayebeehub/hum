"""Nora HUM publisher.

Reads Nora's OWN state.db read-only, maintains a Nora-only ledger of
cumulative-counter deltas, and atomically publishes a minimal, pre-aggregated
usage export for ITGuy's collector. The export deliberately contains no
messages, chat IDs, session titles, database paths, or credentials: only the
three aggregate windows, the model actually used, and a reconciliation verdict.

This is the trust boundary. ITGuy reads this file, never Nora's state.db.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from usage import (
    IST,
    _baseline_and_deltas,
    _cost,
    _is_conversation,
    _read_agent,
    _session,
    _window,
)

AGENT = "Nora"
REFRESH_SECONDS = 90
RECONCILIATION_TOLERANCE_USD = 0.01

LEDGER_SCHEMA = '''
CREATE TABLE IF NOT EXISTS snapshot(
    agent TEXT NOT NULL,row_key TEXT NOT NULL,ins INTEGER,outs INTEGER,cache INTEGER,
    cost REAL,PRIMARY KEY(agent,row_key));
CREATE TABLE IF NOT EXISTS event(
    agent TEXT,at REAL,ins INTEGER,outs INTEGER,cache INTEGER,cost REAL,
    model TEXT,conversation INTEGER);
CREATE TABLE IF NOT EXISTS sample(agent TEXT,at REAL);
'''


def _read_sessions_cost(path: str):
    """Sessions cost fields only, for reconciliation. No content columns."""
    with closing(sqlite3.connect("file:" + path + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            "SELECT id, actual_cost_usd, estimated_cost_usd, cost_status FROM sessions"
        ).fetchall()


def reconcile(usage_rows, session_cost_rows, checked_at):
    """Verify the collector's cost basis against the authoritative sessions summary.

    The fundamental invariant: total cost across all `session_model_usage` rows
    should equal the total cost in the `sessions` table, since both record the
    same spend at two granularities. Status is driven by that total-vs-total
    check, so a mismatch means real attribution drift (e.g. unbilled rows whose
    `cost_status` is still NULL), not a scoping difference. Conversation-only
    cost is reported alongside as context, since that is what the widget shows.
    """
    total_usage = sum(_cost(r) for r in usage_rows)
    total_summary = sum(_cost(s) for s in session_cost_rows)
    conv_usage = sum(_cost(r) for r in usage_rows if _is_conversation(r))
    if not usage_rows:
        status = "unavailable"
    elif abs(total_usage - total_summary) <= RECONCILIATION_TOLERANCE_USD:
        status = "ok"
    else:
        status = "mismatch"
    return {
        "status": status,
        "model_usage_total_cost_usd": round(total_usage, 8),
        "model_usage_conversation_cost_usd": round(conv_usage, 8),
        "session_summary_cost_usd": round(total_summary, 8),
        "difference_usd": round(total_usage - total_summary, 8),
        "checked_at": checked_at,
    }


def build_export(nora_db: str, ledger_path: str, now: float | None = None) -> dict:
    """Sample Nora's state.db into the Nora ledger and return the minimal export."""
    if now is None:
        now = datetime.now(IST).timestamp()
    sessions, rows = _read_agent(nora_db)
    boundary = datetime.fromtimestamp(now, IST).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    if boundary.timestamp() > now:
        boundary -= timedelta(days=1)

    with closing(sqlite3.connect(ledger_path)) as ledger, ledger:
        ledger.executescript(LEDGER_SCHEMA)
        last = ledger.execute(
            "SELECT MAX(at) FROM sample WHERE agent=?", (AGENT,)
        ).fetchone()[0]
        if last is None or now > last:
            _baseline_and_deltas(ledger, AGENT, rows, now, initial=last is None)
        today = _window(ledger, AGENT, boundary.timestamp(), now)
        hours = _window(ledger, AGENT, now - 86400, now, conversation_only=True)
        latest = ledger.execute(
            "SELECT model FROM event WHERE agent=? AND at>=? AND at<=? "
            "AND conversation=1 ORDER BY at DESC,rowid DESC LIMIT 1",
            (AGENT, now - 86400, now),
        ).fetchone()
        hours["model"] = latest[0] if latest else None

    session = _session(sessions, rows, now)
    reconciliation = reconcile(
        rows, _read_sessions_cost(nora_db), datetime.fromtimestamp(now, IST).isoformat()
    )

    return {
        "agent": AGENT,
        "published_at": datetime.fromtimestamp(now, IST).isoformat(),
        "refresh_seconds": REFRESH_SECONDS,
        "partial": bool(today["partial"] or hours["partial"]),
        "today": today,
        "session": session,
        "last_24h": hours,
        "reconciliation": reconciliation,
    }


def publish(export: dict, out_path: str) -> str:
    """Write the export atomically (temp file + rename). Returns the path."""
    payload = json.dumps(export, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    directory = os.path.dirname(out_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".nora-usage-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Publish Nora's minimal HUM usage export for ITGuy's collector"
    )
    parser.add_argument("--nora-db", required=True, help="read-only Nora state.db (own profile)")
    parser.add_argument("--ledger", required=True, help="Nora-only durable ledger SQLite file")
    parser.add_argument("--out", help="export file path; omit to print JSON to stdout")
    parser.add_argument("--now", type=float, help="override timestamp (testing)")
    args = parser.parse_args()

    export = build_export(args.nora_db, args.ledger, now=args.now)
    if args.out:
        publish(export, args.out)
        print("published", args.out)
    else:
        print(json.dumps(export, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()