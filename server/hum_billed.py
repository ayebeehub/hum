#!/usr/bin/env python3
"""HUM billed-cost fetcher — read-only OpenRouter query, never a ledger writer.

Writes /var/lib/hum/billed.json: provider-BILLED cost per agent plus the account
total, for HUM's two windows. Run by hum-billed.timer (uid 10000). The endpoint
only READS this file — hum.service is ProtectSystem=strict and never talks to the
internet. Same shape as Nora's published export: one writer, passive reader.

WHY THIS EXISTS
Hermes' local `estimated_cost_usd` runs systematically BELOW the invoice — DeepSeek
is priced locally at off-peak rates while OpenRouter bills the peak windows
(documented 2026-10-07, audit 2026-10-07-orcost-24h-activity-window-fix.md; measured
again 2026-10-19 at 5.0x low for ITGuy, 1.67x for Nora). An estimate-only widget can
therefore never match what was charged. OpenRouter `/analytics/query` accepts an
arbitrary `time_range` plus an `api_key_id` filter, so an absolute per-agent billed
figure for an exact window is obtainable. Verified 2026-10-19: near-real-time
(trailing 3 h returned real money), stable once a window is settled, and retaining
roughly 60 days — a window older than that reads 0, which is "unavailable", so
retention is recorded rather than inferred.

CONTRACT
* Reads only /opt/data/profiles/itguy/.env (its own profile credential) and the
  pinned key-hash map. Never opens a profile database; never touches Nora's export.
* All-or-nothing: every query must succeed before the file is replaced, so a bad
  fetch leaves the last good figures in place rather than a partial mixture.
* Never logs or prints the credential, a response body, or a URL with a token.
  Failures print an exception CLASS NAME only.
* Atomic replace (tmp file in the same directory + os.replace), mode 0600.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
UTC = timezone.utc
API = 'https://openrouter.ai/api/v1'

ENV_PATH = os.environ.get('HUM_OR_ENV', '/opt/data/profiles/itguy/.env')
ENV_VAR = 'ITGUY_OR_MANAGEMENT'
CONFIG_PATH = os.environ.get(
    'HUM_ORCOST_CONFIG',
    '/opt/data/profiles/itguy/skills/infrastructure/openrouter-cost-report'
    '/scripts/orcost-agents.json')
OUT_PATH = os.environ.get('HUM_BILLED', '/var/lib/hum/billed.json')
AGENTS = ('itguy', 'nora')
SOURCE = 'openrouter:/analytics/query'
# Negative-residual tolerance for the account-vs-partitions invariant. Above this
# a fetch is rejected rather than published (see build()).
RESIDUAL_TOLERANCE_USD = 0.05


def read_secret(path: str, name: str) -> str | None:
    """Parse NAME=value without sourcing the file; never echo the value."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError:
        return None
    for line in text.splitlines():
        match = re.match(rf'^{re.escape(name)}=(.*)$', line)
        if match:
            return match.group(1).strip().strip('"\'')
    return None


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def query_cost(key: str, start: str, end: str, hashes=None) -> float:
    """One read-only Analytics query. Returns summed credits_usage in USD."""
    body = {'metrics': ['credits_usage'], 'dimensions': [],
            'time_range': {'start': start, 'end': end}, 'limit': 1000}
    if hashes is not None:
        body['filters'] = [{'field': 'api_key_id', 'operator': 'in',
                            'value': sorted(hashes)}]
    request = urllib.request.Request(
        API + '/analytics/query', data=json.dumps(body).encode('utf-8'),
        headers={'Authorization': f'Bearer {key}',
                 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.load(response)
    data = payload['data']
    meta, rows = data['metadata'], data['data']
    # A provider warning or a truncated response must never become a silent $0.
    if data.get('warnings'):
        raise RuntimeError('provider analytics warning')
    if meta.get('truncated') is not False or meta.get('row_count') != len(rows):
        raise RuntimeError('provider analytics truncated')
    return round(sum(float(row['credits_usage']) for row in rows), 6)


def today_boundary(now: datetime) -> datetime:
    """06:00 IST, rolled back a day when the clock has not reached it yet."""
    local = now.astimezone(IST)
    boundary = local.replace(hour=6, minute=0, second=0, microsecond=0)
    if boundary > local:
        boundary -= timedelta(days=1)
    return boundary.astimezone(UTC)


def build(now: datetime | None = None) -> dict:
    key = read_secret(ENV_PATH, ENV_VAR)
    if not key:
        raise RuntimeError('no management credential available')
    try:
        config = json.loads(Path(CONFIG_PATH).read_text(encoding='utf-8'))
        pinned = {agent: config['agents'][agent]['key_hashes'] for agent in AGENTS}
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError('pinned key map unusable: %s' % type(exc).__name__)

    now = now or datetime.now(UTC)
    boundary = today_boundary(now)
    windows = {
        'today': (iso(boundary), iso(now)),
        'last_24h': (iso(now - timedelta(hours=24)), iso(now)),
    }

    result = {
        'fetched_at': now.isoformat(),
        'source': SOURCE,
        'today_boundary_ist': boundary.astimezone(IST).isoformat(),
        'account': {}, 'agents': {}, 'agents_configured': list(AGENTS),
    }
    for name, (start, end) in windows.items():
        result['account'][name] = {
            'start': start, 'end': end, 'cost_usd': query_cost(key, start, end)}
        for agent in AGENTS:
            result['agents'].setdefault(agent, {})[name] = {
                'start': start, 'end': end,
                'cost_usd': query_cost(key, start, end, hashes=pinned[agent])}
    result['account']['last_24h']['note'] = (
        'account total; media generation IS included here — the image models bill '
        'to a pinned agent key, so media is a COMPONENT of this figure, never an addend')
    # --- structural invariant (added 2026-10-20 after the media double-count) ---
    # The account total is a SUPERSET of the pinned key partitions: an unassigned
    # or newly added key can only add to it. A negative residual therefore means
    # the provider returned less for the whole account than for the account's own
    # keys — impossible without a double-count or a misattribution bug. Refuse to
    # publish in that case (the previous good file stays in place) rather than
    # render an invoice that contradicts its own parts.
    for name in windows:
        partitions = sum(result['agents'][agent][name]['cost_usd'] for agent in AGENTS)
        residual = result['account'][name]['cost_usd'] - partitions
        if residual < -RESIDUAL_TOLERANCE_USD:
            raise RuntimeError('account total below key partitions (%s)' % name)
        result['account'][name]['key_partition_usd'] = round(partitions, 6)
        result['account'][name]['key_partition_residual_usd'] = round(residual, 6)
    return result


def write_atomic(path: str, payload: dict) -> None:
    directory = os.path.dirname(path) or '.'
    handle, tmp = tempfile.mkstemp(dir=directory, prefix='.billed-', suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, separators=(',', ':'), ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description='Refresh HUM billed cost')
    parser.add_argument('--if-stale', type=int, default=0, metavar='SECONDS',
                        help='skip the query when the output is younger than SECONDS')
    args = parser.parse_args()

    if args.if_stale > 0:
        try:
            age = datetime.now(UTC).timestamp() - os.path.getmtime(OUT_PATH)
            if age < args.if_stale:
                return 0
        except OSError:
            pass

    try:
        payload = build()
        write_atomic(OUT_PATH, payload)
    except Exception as exc:  # class name only: never values, URLs or bodies
        print('hum_billed: refresh failed: %s' % type(exc).__name__,
              file=sys.stderr, flush=True)
        return 1
    print('hum_billed: wrote %s (account today %s, 24h %s)'
          % (OUT_PATH, payload['account']['today']['cost_usd'],
             payload['account']['last_24h']['cost_usd']), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
