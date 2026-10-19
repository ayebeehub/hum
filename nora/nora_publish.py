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
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from usage import (
    IST,
    _baseline_and_deltas,
    _cost,
    _read_agent,
    _session,
    _window,
)

AGENT = "Nora"
REFRESH_SECONDS = 90
RECONCILIATION_TOLERANCE_USD = 0.01
MEDIA_CACHE_TTL_SECONDS = 3600.0
MEDIA_CACHE_FILENAME = "or-media-cache.json"
ANALYTICS_URL = "https://openrouter.ai/api/v1/analytics/query"
# Nora's OpenRouter API key hash, pinned so media generation is attributed to HER
# key rather than read account-wide. Source of truth: ITGuy's orcost-agents.json
# (agents.nora.key_hashes). Not a secret (it is a hash, already in that readable
# config), but it MUST be kept in sync on key rotation or media silently mis-scopes.
NORA_KEY_HASH = "c65fb7ac655dad776f0001df67e4020a7ec9662139007dbaf9039229c1b9d5e8"

# Model-name markers for OpenRouter generation tools that bill through OpenRouter
# but are NOT written to state.db's session_model_usage (the DB only logs chat /
# vision / aux completion calls). These are generation families, kept specific so a
# text or vision model can't false-positive. Extend when a new generation model lands.
_MEDIA_MODEL_MARKERS = (
    # image generation
    "image", "flux", "sdxl", "dalle", "ideogram", "recraft", "imagen", "midjourney",
    "gpt-image", "nano-banana", "wanx",
    # video generation
    "veo", "sora", "kling", "runway", "wan", "pika", "luma", "hailuo",
    "minimax-video", "seedance", "cogvideo", "genvideo",
    # audio / music / speech generation
    "suno", "udio", "music", "tts-1", "speech", "elevenlabs", "audiocraft",
)


def _read_management_key() -> str | None:
    """Return the OpenRouter management key from the environment, never logged.

    Read from env only (the cron shell script exports it). Unit tests stay
    hermetic: no env var means no key, so fetch_media_gen short-circuits to an
    error stub and never touches the network.
    """
    key = os.environ.get("OR_MANAGEMENT")
    return key.strip() or None if key else None


def _is_media_model(model: str) -> bool:
    m = (model or "").lower()
    return any(marker in m for marker in _MEDIA_MODEL_MARKERS)


def fetch_media_gen(management_key: str | None, cache_path: str, now: float | None = None) -> dict:
    """Return media-generation spend (image/video/audio) for today / 24h / lifetime.

    Covers OpenRouter generation tools that bypass state.db (image_gen, video_gen, etc.).
    Scoped to Nora's OWN key via the api_key_id filter, so the figure is her media
    spend, not the whole account's. (Measured 2026-10-20: account-wide media overstates
    Nora by ~$0.20 today and ~$9.94 lifetime, because other keys also generate media.)
    Cached for MEDIA_CACHE_TTL_SECONDS; analytics data is day-granularity, so a 90s poll
    would return the same numbers every time.
    """
    now = now or time.time()
    end = datetime.fromtimestamp(now, ZoneInfo("UTC")).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    start = end - timedelta(days=367)
    stub = {
        "cost_today": 0.0,
        "cost_24h": 0.0,
        "cost_lifetime": 0.0,
        "as_of": None,
        "cached": False,
        "key_scoped": True,
        "error": None,
        "fetched_at": now,
    }
    if cache_path:
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if (now - cached.get("fetched_at", 0) < MEDIA_CACHE_TTL_SECONDS
                    and cached.get("key_hint") == (management_key or "")[:8]):
                cached["cached"] = True
                return cached
        except (OSError, ValueError):
            pass
    if not management_key:
        stub["error"] = "missing management key"
        return stub

    payload = json.dumps({
        "metrics": ["total_usage"],
        "dimensions": ["model"],
        "granularity": "day",
        "time_range": {
            "start": start.strftime("%Y-%m-%dT00:00:00Z"),
            "end": end.strftime("%Y-%m-%dT00:00:00Z"),
        },
        "filters": [{"field": "api_key_id", "operator": "in", "value": [NORA_KEY_HASH]}],
        "limit": 1000,
    }).encode("utf-8")
    request = urllib.request.Request(
        ANALYTICS_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + management_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            data = json.load(resp)
    except Exception as exc:  # network/HTTP — fall back to stale/zero, never fail publish
        stub["error"] = "fetch: %s" % exc
        return stub

    rows = data.get("data", {}).get("data", [])
    today_key = (end - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_key = (end - timedelta(days=2)).strftime("%Y-%m-%d")
    cost_today = cost_24h = cost_lifetime = 0.0
    for row in rows:
        if not _is_media_model(row.get("model") or ""):
            continue
        usage = float(row.get("total_usage") or 0.0)
        day = row.get("date__day")
        cost_lifetime += usage
        if day == today_key:
            cost_today += usage
        if day in (today_key, yesterday_key):
            cost_24h += usage
    stub.update({
        "cost_today": round(cost_today, 8),
        "cost_24h": round(cost_24h, 8),
        "cost_lifetime": round(cost_lifetime, 8),
        "as_of": datetime.fromtimestamp(now, ZoneInfo("UTC")).isoformat(),
        "fetched_at": now,
        "key_hint": management_key[:8],
        "error": None,
    })
    if cache_path:
        try:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(stub, fh, separators=(",", ":"))
        except OSError:
            pass
    return stub

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

    The `sessions` summary is authoritative for settled cost: it records each
    session's billed conversation/chat cost, and its total must equal the sum of
    `session_model_usage` rows whose `cost_status` is set (estimated / actual /
    included) — those are the settled rows. Rows whose `cost_status` is NULL are
    auxiliary task charges (background_review, title_generation, approval,
    vision, image_generation, compression) that carry an estimate but are never
    rolled into any session's summary. They are tracked and folded into a single
    consolidated total instead of feeding the ok/mismatch check, which previously
    forced a permanent `mismatch` (the NULL auxiliary delta reported a growing
    nonzero difference every publish).

    Status is driven by the settled-rows-vs-summary check; `difference_usd` is
    that drift. `total_cost_usd` is the consolidated figure AB tracks
    (summary + auxiliary).
    """
    total_summary = sum(_cost(s) for s in session_cost_rows)
    settled_usage = sum(_cost(r) for r in usage_rows if r["cost_status"] is not None)
    auxiliary = sum(_cost(r) for r in usage_rows if r["cost_status"] is None)
    if not usage_rows:
        status = "unavailable"
    elif abs(settled_usage - total_summary) <= RECONCILIATION_TOLERANCE_USD:
        status = "ok"
    else:
        status = "mismatch"
    return {
        "status": status,
        "total_cost_usd": round(total_summary + auxiliary, 8),
        "session_summary_cost_usd": round(total_summary, 8),
        "auxiliary_cost_usd": round(auxiliary, 8),
        "difference_usd": round(settled_usage - total_summary, 8),
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
        hours = _window(ledger, AGENT, now - 86400, now)
        latest = ledger.execute(
            "SELECT model FROM event WHERE agent=? AND at>=? AND at<=? "
            "AND conversation=1 ORDER BY at DESC,rowid DESC LIMIT 1",
            (AGENT, now - 86400, now),
        ).fetchone()
        hours["model"] = latest[0] if latest else None

    session = _session(sessions, rows, now, gross=True)
    reconciliation = reconcile(
        rows, _read_sessions_cost(nora_db), datetime.fromtimestamp(now, IST).isoformat()
    )

    cache_dir = os.path.dirname(os.path.abspath(ledger_path) or ".")
    media = fetch_media_gen(_read_management_key(), os.path.join(cache_dir, MEDIA_CACHE_FILENAME), now=now)
    if media.get("error") is None and media.get("cost_today") is not None:
        if today.get("cost_usd") is not None:
            today["cost_usd"] = round(today["cost_usd"] + media["cost_today"], 8)
        if hours.get("cost_usd") is not None:
            hours["cost_usd"] = round(hours["cost_usd"] + media["cost_24h"], 8)

    return {
        "agent": AGENT,
        "published_at": datetime.fromtimestamp(now, IST).isoformat(),
        "refresh_seconds": REFRESH_SECONDS,
        "partial": bool(today["partial"] or hours["partial"]),
        "today": today,
        "session": session,
        "last_24h": hours,
        "reconciliation": reconciliation,
        "media_gen": {
            "cost_today_usd": media["cost_today"],
            "cost_24h_usd": media["cost_24h"],
            "cost_lifetime_usd": media["cost_lifetime"],
            "as_of": media["as_of"],
            "cached": media["cached"],
            "key_scoped": media["key_scoped"],
            "error": media["error"],
        },
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