"""HUM read-only Hermes usage collector. Source profile databases are never modified."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def _cost(row):
    status = row['cost_status']
    if status == 'included':
        return 0.0
    actual = row['actual_cost_usd']
    if actual is not None and (actual > 0 or status == 'actual'):
        return max(0.0, actual)
    return max(0.0, row['estimated_cost_usd'] or 0.0)


def _is_conversation(row):
    model = row['model'] or ''
    return (row['task'] in ('', None) and not model.startswith('tencent/')
            and not model.split('/')[-1].startswith('glm-'))


def _read_agent(path):
    with closing(sqlite3.connect('file:' + path + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        sessions = db.execute('''SELECT id, source, chat_type, ended_at,
                            last_activity_at, started_at FROM sessions''').fetchall()
        rows = db.execute('''SELECT session_id, model, task, billing_provider,
                            billing_base_url, billing_mode, input_tokens,
                            output_tokens, cache_read_tokens, estimated_cost_usd,
                            actual_cost_usd, cost_status, first_seen, last_seen
                            FROM session_model_usage''').fetchall()
    return sessions, rows


def _session(sessions, rows, now):
    conversational = {r['session_id'] for r in rows if _is_conversation(r)}
    eligible = [s for s in sessions if s['id'] in conversational and
                s['source'] not in ('cron', 'kanban') and s['chat_type'] == 'dm']
    if not eligible:
        return {'status': 'none', 'model': None, 'input_tokens': 0,
                'output_tokens': 0, 'cache_read_tokens': 0, 'cost_usd': 0.0}
    latest = max(eligible, key=lambda s: s['last_activity_at'] or s['started_at'] or 0)
    in_session = [r for r in rows if r['session_id'] == latest['id'] and _is_conversation(r)]
    active = latest['ended_at'] is None and now - (latest['last_activity_at'] or 0) <= 600
    recent_model = max(in_session, key=lambda r: r['last_seen'] or 0)['model']
    return {'status': 'active' if active else 'last session', 'model': recent_model,
            'input_tokens': sum(r['input_tokens'] or 0 for r in in_session),
            'output_tokens': sum(r['output_tokens'] or 0 for r in in_session),
            'cache_read_tokens': sum(r['cache_read_tokens'] or 0 for r in in_session),
            'cost_usd': sum(_cost(r) for r in in_session)}


def _row_key(row):
    return json.dumps([row[k] for k in ('session_id', 'model', 'task',
                      'billing_provider', 'billing_base_url', 'billing_mode')], separators=(',', ':'))


def _baseline_and_deltas(db, agent, rows, now, initial=False):
    for row in rows:
        key = _row_key(row)
        values = (row['input_tokens'] or 0, row['output_tokens'] or 0,
                  row['cache_read_tokens'] or 0, _cost(row))
        old = db.execute('SELECT ins, outs, cache, cost FROM snapshot WHERE agent=? AND row_key=?',
                         (agent, key)).fetchone()
        if old is not None or not initial:
            delta = tuple(current - previous for current, previous in zip(values, old or (0, 0, 0, 0.0)))
            if all(v >= 0 for v in delta[:3]) and any(abs(v) > 0.00000001 for v in delta):
                db.execute('''INSERT INTO event(agent, at, ins, outs, cache, cost, model, conversation)
                              VALUES(?,?,?,?,?,?,?,?)''',
                           (agent, now, *delta, row['model'], int(_is_conversation(row))))
        db.execute('''INSERT INTO snapshot(agent,row_key,ins,outs,cache,cost) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(agent,row_key) DO UPDATE SET
                      ins=excluded.ins,outs=excluded.outs,cache=excluded.cache,cost=excluded.cost''',
                   (agent, key, *values))
    db.execute('INSERT INTO sample(agent,at) VALUES(?,?)', (agent, now))


def _window(db, agent, since, now, conversation_only=False):
    where = 'AND conversation=1' if conversation_only else ''
    totals = db.execute(f'''SELECT SUM(ins),SUM(outs),SUM(cache),SUM(cost) FROM event
                             WHERE agent=? AND at>=? AND at<=? {where}''',
                        (agent, since, now)).fetchone()
    samples = [row[0] for row in db.execute('''SELECT at FROM sample WHERE agent=? AND at<=?
                                                ORDER BY at''', (agent, now))]
    partial = not samples or samples[0] > since or any(
        b-a > 180 and b >= since for a, b in zip(samples, samples[1:]))
    # First sample is a baseline, not a historical reconstruction.
    has_data = bool(samples and samples[-1] > samples[0])
    return {'input_tokens': int(totals[0] or 0) if has_data else None,
            'output_tokens': int(totals[1] or 0) if has_data else None,
            'cache_read_tokens': int(totals[2] or 0) if has_data else None,
            'cost_usd': round(totals[3] or 0.0, 8) if has_data else None,
            'partial': partial}


def collect(sources: dict[str, str], ledger_path: str, now: float | None = None) -> dict:
    """Read profile DBs without modifying them; sample cumulative counters into a separate ledger."""
    if now is None:
        now = datetime.now(IST).timestamp()
    data = {name: _read_agent(path) for name, path in sources.items()}
    boundary = datetime.fromtimestamp(now, IST).replace(hour=6, minute=0, second=0, microsecond=0)
    if boundary.timestamp() > now:
        boundary -= timedelta(days=1)
    with closing(sqlite3.connect(ledger_path)) as ledger, ledger:
        ledger.executescript('''CREATE TABLE IF NOT EXISTS snapshot(
             agent TEXT NOT NULL,row_key TEXT NOT NULL,ins INTEGER,outs INTEGER,cache INTEGER,
             cost REAL,PRIMARY KEY(agent,row_key));
             CREATE TABLE IF NOT EXISTS event(
             agent TEXT,at REAL,ins INTEGER,outs INTEGER,cache INTEGER,cost REAL,
             model TEXT,conversation INTEGER);
             CREATE TABLE IF NOT EXISTS sample(agent TEXT,at REAL);''')
        for name, (_, rows) in data.items():
            last = ledger.execute('SELECT MAX(at) FROM sample WHERE agent=?', (name,)).fetchone()[0]
            if last is None or now > last:
                _baseline_and_deltas(ledger, name, rows, now, initial=last is None)
        today = {name: _window(ledger, name, boundary.timestamp(), now)
                 for name in data}
        hours = {name: _window(ledger, name, now-86400, now, conversation_only=True)
                 for name in data}
        for name, result in hours.items():
            latest = ledger.execute('''SELECT model FROM event WHERE agent=? AND at>=?
                                       AND at<=? AND conversation=1 ORDER BY at DESC,rowid DESC
                                       LIMIT 1''', (name, now-86400, now)).fetchone()
            result['model'] = latest[0] if latest else None
    # Only an agent that was REQUESTED but came back empty is missing. Nora is
    # never requested here by design (her figures come from her published
    # export, merged in hum_merge.py), so the old hardcoded ('Nora','ITGuy')
    # tuple marked every payload partial forever.
    missing = [name for name in ('Nora', 'ITGuy')
               if name in sources and name not in data]
    gross = {'input_tokens': sum(v['input_tokens'] or 0 for v in today.values())
                              if all(v['input_tokens'] is not None for v in today.values()) else None,
             'output_tokens': sum(v['output_tokens'] or 0 for v in today.values())
                              if all(v['output_tokens'] is not None for v in today.values()) else None,
             'cost_usd': round(sum(v['cost_usd'] or 0 for v in today.values()), 8)
                         if all(v['cost_usd'] is not None for v in today.values()) else None,
             'partial': bool(missing) or any(v['partial'] for v in today.values())}
    session = {name: _session(*tables, now) for name, tables in data.items()}
    for name in missing:
        session[name] = {'status': 'unavailable', 'model': None, 'input_tokens': None,
                         'output_tokens': None, 'cache_read_tokens': None, 'cost_usd': None}
        hours[name] = {'input_tokens': None, 'output_tokens': None,
                       'cache_read_tokens': None, 'cost_usd': None,
                       'partial': True, 'model': None}
    return {'updated_at': datetime.fromtimestamp(now, IST).isoformat(),
            'refresh_seconds': 90, 'agents_present': list(data), 'missing_agents': missing,
            'today': gross, 'session': session, 'last_24h': hours}


def _abbr(value):
    if value is None:
        return '...'
    if abs(value) >= 1_000_000:
        return f'{value / 1_000_000:.2f}M'
    if abs(value) >= 1_000:
        return f'{value / 1_000:.1f}k'
    return str(value)


def _money(value):
    """Compact 2dp money for provider-billed figures."""
    return f'${value:,.2f}'


def _billed_cost(billed, agent, window):
    """Provider-billed USD for a window; agent None means the account total.

    Returns None when the figure is not present, which the caller renders as the
    estimate path rather than as $0.00.
    """
    try:
        if agent is None:
            return float(billed['account'][window]['cost_usd'])
        return float(billed['agents'][agent][window]['cost_usd'])
    except (KeyError, TypeError, ValueError):
        return None


def _media_cost(media, window):
    """Nora's account-scoped media generation spend for a window, or None."""
    key = 'cost_today_usd' if window == 'today' else 'cost_24h_usd'
    try:
        value = media[key]
    except (KeyError, TypeError):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _agent_lines(name, row, billed=None):
    """Title + figures for one agent row.

    Without `billed` this is byte-for-byte the historical estimate render. With a
    billed amount the money line shows provider-billed USD beside the local
    estimate — the two are displayed together and never summed.
    """
    unavailable = row.get('status') == 'unavailable' or row['input_tokens'] is None
    label = row.get('status', '')
    if unavailable:
        title = f'{name} unavailable'
    else:
        model = re.sub(r'[^A-Za-z0-9._:-]', '?',
                       (row['model'] or 'unknown').split('/')[-1])[:22]
        title = f'{name}  {model}  {label.upper()}' if label else f'{name}  {model}'
    if billed is not None:
        figures = f'billed {_money(billed)}'
        if not unavailable and row['cost_usd'] is not None:
            figures += f'  ·  est {_money(row["cost_usd"])}'
        return title, figures
    if unavailable:
        return title, 'in ... / out ...  cache ...  $...'
    figures = (f'in {_abbr(row["input_tokens"])} / out {_abbr(row["output_tokens"])}'
               f'  cache {_abbr(row["cache_read_tokens"])}  ${row["cost_usd"]:.4f}')
    return title, figures


def render_display(data):
    """Ten strings in a fixed order for Rainmeter WebParser; never include session metadata.

    String 1 is the today gross line. When a fresh provider-billed set is
    available it leads with BILLED and keeps the local estimate visible as `est`;
    without one, this is exactly the historical estimate-only render, so a
    provider or timer failure degrades to the old display rather than to $0.00.
    """
    today = data['today']
    billed = data.get('billed') if isinstance(data.get('billed'), dict) else None
    media = data.get('media') if isinstance(data.get('media'), dict) else None

    billed_today = _billed_cost(billed, None, 'today') if billed else None
    media_today = _media_cost(media, 'today')
    if billed_today is not None:
        line = f'BILLED {_money(billed_today)}'
        # Nora's media generation spend is ALREADY INSIDE the account total that
        # _billed_cost() just returned — it is not a separate bucket. Measured
        # 2026-10-20 against the provider: the two image-model rows billed to her
        # key ($1.501922 gemini-3-pro-image + $0.204086 gemini-3.1-flash-image)
        # equal her published media figure ($1.706008) to the cent, and the
        # account total equals the sum of the pinned key partitions exactly. So
        # media is a COMPONENT of this invoice, never an addend: the old
        # '+ media $X' form overstated the day by the whole media amount. Only
        # shown when it is genuinely a subset of the parent figure.
        if media_today is not None and media_today <= billed_today:
            line += f' (incl. media {_money(media_today)})'
        if today['cost_usd'] is not None:
            line += f'  ·  est {_money(today["cost_usd"])}'
    elif today['cost_usd'] is None:
        line = 'in ... / out ...  $...  warming up'
    else:
        line = (f'in {_abbr(today["input_tokens"])} / out {_abbr(today["output_tokens"])}'
                f'  ${today["cost_usd"]:.4f}')
    lines = [line]
    for section in ('session', 'last_24h'):
        for name in ('Nora', 'ITGuy'):
            row = data[section][name]
            # Provider-billed money wins the 24h rows when it is available: the
            # per-agent invoice is the figure AB actually needs there.
            agent_billed = (_billed_cost(billed, name.lower(), 'last_24h')
                            if (section == 'last_24h' and billed) else None)
            if agent_billed is not None:
                lines.extend(_agent_lines(name, row, billed=agent_billed))
            elif section == 'last_24h' and row['input_tokens'] is None:
                lines.extend((f'{name}  warming up', 'in ... / out ...  cache ...  $...'))
            else:
                lines.extend(_agent_lines(name, row))
    stamp = datetime.fromisoformat(data['updated_at']).strftime('%H:%M IST')
    partial = any(x['partial'] for x in data['last_24h'].values()) or today['partial']
    if partial:
        verdict = 'PARTIAL'
    elif billed_today is not None:
        # The leading figure is a provider invoice, not a local estimate.
        verdict = 'BILLED'
    else:
        verdict = 'EST. USD'
    lines.append(f'UPDATED {stamp}  ·  90s  ·  ' + verdict)
    return lines


def main():
    parser = argparse.ArgumentParser(description='Emit HUM aggregate usage JSON')
    parser.add_argument('--nora-db', required=True, help='read-only Nora state.db')
    parser.add_argument('--itguy-db', help='read-only ITGuy state.db, provided by its owner')
    parser.add_argument('--ledger', required=True, help='separate collector SQLite file')
    parser.add_argument('--display', action='store_true', help='emit only the ten widget strings')
    args = parser.parse_args()
    sources = {'Nora': args.nora_db}
    if args.itguy_db:
        sources['ITGuy'] = args.itguy_db
    result = collect(sources, args.ledger)
    print(json.dumps({'display': render_display(result)} if args.display else result,
                     separators=(',', ':'), ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
