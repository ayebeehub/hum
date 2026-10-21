#!/usr/bin/env python3
"""HUM integrity watchdog — an INDEPENDENT check that the widget's money is right.

WHY THIS EXISTS
On 2026-10-20 the widget served `BILLED $14.03 + media $1.71`: media had been added on
top of a provider invoice that already contained it. The display was fixed structurally
(media is a component; the fetcher refuses to publish an account total below its own key
partitions) — but nothing INDEPENDENT watched the served figure. Those numbers had only
ever been compared by a human looking at the dashboard, which is how a wrong figure
survived a day. This watchdog closes that gap: it asks the provider what the money
actually is, parses what the widget actually serves, and compares the two.

INDEPENDENCE IS THE POINT
It deliberately does NOT import `usage.py` or `hum_merge.py`. It re-queries
`/analytics/query` for the same 06:00-IST window the widget uses and re-parses the served
payload itself, so an arithmetic bug in the widget cannot hide from it. It also runs the
regression suites weekly (see SCHEDULE) so a code change that regresses is reported
without anyone remembering to test.

READ-ONLY
Fetches the endpoint over the tailnet through `ssh-host.sh`; queries Analytics; reads its
own profile `.env`, the pinned key-hash map, `state.db` and Nora's published export.
Writes only temp files under `cache/scratch` (the suites do the same). It NEVER touches
`/var/lib/hum`, `/opt/hum`, or anything on the host outside a read.

NO_AGENT CRON CONTRACT
Healthy  -> silent stdout (nothing is delivered).
Unhealthy-> an alert block on stdout, and still exit 0: the run must not die before the
            alert is emitted. `--always` prints the report even when healthy (manual use).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
UTC = timezone.utc
API = 'https://openrouter.ai/api/v1'

PROFILE = '/opt/data/profiles/itguy'
SSH = PROFILE + '/scripts/ssh-host.sh'
ENV_PATH = PROFILE + '/.env'
ENV_VAR = 'ITGUY_OR_MANAGEMENT'
CONFIG_PATH = PROFILE + '/skills/infrastructure/openrouter-cost-report/scripts/orcost-agents.json'
STATE_DB = PROFILE + '/state.db'
NORA_EXPORT = '/opt/data/hum-export/nora-usage.json'
SCRATCH = PROFILE + '/cache/scratch'
# Durable home of the regression suites + the predecessor copies they compare against.
SUITES_DIR = '/opt/itguy/deployments/hum/tests'
PAYLOAD_DIR = '/opt/itguy/hum-payload'
LIVE_FILES = ('usage.py', 'hum_merge.py', 'hum_billed.py')

AGENTS = ('itguy', 'nora')
IMAGE_HINT = 'image'
RESIDUAL_TOLERANCE_USD = 0.05      # same invariant the fetcher enforces
SERVED_AHEAD_TOLERANCE_USD = 0.01  # the served invoice must never exceed the provider total
SERVED_STALE_MAX_USD = 5.00        # a served invoice this far behind is a stale/broken fetch
MEDIA_TOLERANCE_USD = 0.25         # served media vs media re-derived from provider rows
AGENT_ROW_TOLERANCE_USD = 2.00     # served per-agent 24h row vs the re-derived partition
ZERO_EPSILON_USD = 0.005           # below half a cent the display cannot show it; see
                                   # the zero rule in check_display

# `BILLED $16.30 (incl. media $1.71)  ·  est $7.26`
INVOICE_RE = re.compile(
    r'^BILLED \$(?P<invoice>[0-9]+\.[0-9]{2})'
    r'(?: \(incl\. media \$(?P<media>[0-9]+\.[0-9]{2})\))?'
    r'\s+·\s+est \$(?P<est>[0-9]+\.[0-9]{2})$')
AGENT_ROW_RE = re.compile(r'^billed \$(?P<billed>[0-9]+\.[0-9]{2})\s+·\s+est \$(?P<est>[0-9]+\.[0-9]{2})$')


# --------------------------------------------------------------------------- helpers
def read_secret(path: str, name: str) -> str | None:
    """Parse NAME=value without sourcing the file; never echo the value."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError:
        return None
    for line in text.splitlines():
        match = re.match(rf'^{re.escape(name)}=(.*)$', line)
        if match:
            return match.group(1).strip().strip('"\'' )
    return None


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def today_boundary(now: datetime) -> datetime:
    """06:00 IST, rolled back a day when the clock has not reached it yet."""
    local = now.astimezone(IST)
    boundary = local.replace(hour=6, minute=0, second=0, microsecond=0)
    if boundary > local:
        boundary -= timedelta(days=1)
    return boundary.astimezone(UTC)


def query_rows(key: str, start: str, end: str, hashes=None, dimensions=None) -> list:
    """One read-only Analytics query. Returns raw rows."""
    body = {'metrics': ['credits_usage'], 'dimensions': list(dimensions or []),
            'time_range': {'start': start, 'end': end}, 'limit': 1000}
    if hashes is not None:
        body['filters'] = [{'field': 'api_key_id', 'operator': 'in', 'value': sorted(hashes)}]
    request = urllib.request.Request(
        API + '/analytics/query', data=json.dumps(body).encode('utf-8'),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        method='POST')
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.load(response)
    data = payload['data']
    meta, rows = data['metadata'], data['data']
    # A provider warning or a truncated response must never become a silent $0.
    if data.get('warnings'):
        raise RuntimeError('provider analytics warning')
    if meta.get('truncated') is not False or meta.get('row_count') != len(rows):
        raise RuntimeError('provider analytics truncated')
    return rows


def sum_cost(rows) -> float:
    return round(sum(float(row['credits_usage']) for row in rows), 6)


def sh(remote_command: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(['bash', SSH, remote_command], capture_output=True,
                          text=True, timeout=timeout)


# --------------------------------------------------------------- the check functions
def live_check():
    """Fetch the payload exactly as the skin receives it.

    Returns (status, http, box) where status is 'ok' or a failure sentence, http is the
    HTTP code, and box carries {'payload': <json body>, 'headers': {...}} when ok.
    Kept as a module-level function so the failure path can be rehearsed by substitution
    (see scripts/rehearse_no_agent_failure.py in itguy-infrastructure-ops).
    """
    remote = ('IP=$(tailscale ip -4 | head -1); '
              'curl -fsS -i --max-time 20 "http://$IP:8765/hum"')
    try:
        proc = sh(remote, timeout=60)
    except Exception as exc:
        return 'endpoint fetch could not run (%s)' % type(exc).__name__, 0, {}
    if proc.returncode != 0:
        return ('endpoint unreachable or curl failed (wrapper exit %d)'
                % proc.returncode), 0, {}
    raw = proc.stdout
    if not raw.strip():
        return 'endpoint returned no bytes', 0, {}

    head, sep, body = raw.partition('\r\n\r\n')
    if not sep:
        head, sep, body = raw.partition('\n\n')
    lines = head.splitlines()
    status_line = lines[0] if lines else ''
    headers = {}
    for line in lines[1:]:
        if ':' in line:
            name, value = line.split(':', 1)
            headers[name.strip().lower()] = value.strip()
    match = re.search(r'\s(\d{3})', status_line)
    http = int(match.group(1)) if match else 0
    if http != 200:
        return 'endpoint returned HTTP %s' % http, http, {}
    try:
        payload = json.loads(body)
    except ValueError:
        return 'endpoint body is not JSON', http, {}
    return 'ok', http, {'payload': payload, 'headers': headers}


def check_display(display, headers, provider=None) -> list:
    """Pure display-contract check. Returns a list of failure sentences.

    ZERO RULE (2026-10-21). A served `$0.00` is only a fault when the money it claims to
    summarise is NOT zero. The predecessor of this function asked `'$0.00' in text`, which
    fired on any row containing a zero — including the legitimate `(incl. media $0.00)` of a
    day with no image generation — and it had no way to tell a genuine zero (an agent idle
    for 24h, or a window that just rolled over at 06:00 IST) from a degraded render. It
    false-alarmed twice on a healthy widget. So each money field is now PARSED and compared
    against the provider's own figure for the same window: zero is a failure only while the
    provider says otherwise. `provider=None` (unavailable, or a unit test) skips the rule
    entirely rather than guessing — a check that cannot see the money must not judge it.
    """
    bad = []
    if not isinstance(display, list):
        return ['payload has no display list']
    if len(display) != 10:
        bad.append('payload has %d display strings, expected 10' % len(display))
    if not all(isinstance(x, str) for x in display):
        bad.append('payload contains a non-string display entry')
    if bad:
        return bad

    first = display[0]
    if ' + ' in first:
        bad.append('string 1 carries an ADDITIVE term (media or another bucket added on '
                   'top of the invoice): %r' % first)
    invoice = media = None
    match = INVOICE_RE.match(first)
    if not match:
        bad.append('string 1 does not match the BILLED contract: %r' % first)
    else:
        invoice = float(match.group('invoice'))
        media = float(match.group('media')) if match.group('media') else None
        if media is not None and media > invoice + 0.005:
            bad.append('media component $%.2f exceeds the invoice $%.2f' % (media, invoice))
    for who, index in (('Nora', 6), ('ITGuy', 8)):
        if not AGENT_ROW_RE.match(display[index]):
            bad.append('%s 24h row does not show a billed figure: %r' % (who, display[index]))
    # The zero rule. Only the fields the provider can corroborate are judged: the invoice
    # against the account total, each agent row against its own partition. The media
    # component is deliberately excluded — it is judged by the proportional drift check in
    # main(), and a zero there is the normal state of a day with no image generation.
    if provider:
        account_today = provider['account']['today']
        if invoice is not None and invoice == 0 and account_today > ZERO_EPSILON_USD:
            bad.append('string 1 serves an invoice of $0.00 while the provider reports '
                       '$%.6f for the same window (a degraded render reached the skin)'
                       % account_today)
        for who, index, agent in (('Nora', 6, 'nora'), ('ITGuy', 8, 'itguy')):
            row = AGENT_ROW_RE.match(display[index])
            if row and float(row.group('billed')) == 0:
                partition = provider['parts'][agent]['last_24h']
                if partition > ZERO_EPSILON_USD:
                    bad.append('%s 24h row serves $0.00 while its partition for the same '
                               'window is $%.6f (a degraded render reached the skin)'
                               % (who, partition))
    footer = display[9]
    if 'EST. USD' in footer:
        bad.append('footer says EST. USD: no fresh provider-billed data is being served '
                   '(the fetcher has been failing for >900s)')
    elif 'BILLED' not in footer:
        bad.append('footer carries neither BILLED nor EST. USD: %r' % footer)
    content_type = headers.get('content-type', '')
    if 'application/json' not in content_type:
        bad.append('unexpected Content-Type: %r' % content_type)
    if 'no-store' not in headers.get('cache-control', ''):
        bad.append('unexpected Cache-Control: %r' % headers.get('cache-control'))
    return bad


def provider_totals(now: datetime | None = None) -> dict:
    """Re-derive the provider's own figures for the widget's windows. Read-only."""
    key = read_secret(ENV_PATH, ENV_VAR)
    if not key:
        raise RuntimeError('no management credential available')
    config = json.loads(Path(CONFIG_PATH).read_text(encoding='utf-8'))
    pinned = {agent: config['agents'][agent]['key_hashes'] for agent in AGENTS}

    now = now or datetime.now(UTC)
    boundary = today_boundary(now)
    windows = {'today': (iso(boundary), iso(now)),
               'last_24h': (iso(now - timedelta(hours=24)), iso(now))}
    out = {'parts': {}, 'account': {}, 'media_today': None}
    for name, (start, end) in windows.items():
        out['account'][name] = sum_cost(query_rows(key, start, end))
        for agent in AGENTS:
            out['parts'].setdefault(agent, {})[name] = sum_cost(
                query_rows(key, start, end, hashes=pinned[agent]))
    # The media component, re-derived from provider rows rather than from the widget.
    start, end = windows['today']
    rows = query_rows(key, start, end, hashes=pinned['nora'], dimensions=['model'])
    out['media_today'] = round(sum(
        float(row['credits_usage']) for row in rows
        if IMAGE_HINT in str(row.get('model_name') or row.get('model') or '').lower()), 6)
    return out


def run_suites() -> tuple:
    """Run the regression suites against the LIVE code. Returns (failures, summary)."""
    build = tempfile.mkdtemp(prefix='hum-watchdog-', dir=SCRATCH)
    failures, summary = [], []
    try:
        # Live modules, straight off the host (read-only).
        for name in LIVE_FILES:
            proc = sh('cat /opt/hum/%s' % name)
            if proc.returncode != 0 or not proc.stdout:
                return (['regression suites could not run: /opt/hum/%s unreadable' % name],
                        'unavailable')
            Path(build, name).write_text(proc.stdout, encoding='utf-8')
        # The predecessor copies the suites diff against.
        proc = sh('ls -1 %s/usage.py.pre-billed-* 2>/dev/null | head -1' % PAYLOAD_DIR)
        old, newer = proc.stdout.strip(), ''
        proc2 = sh('ls -1 %s/usage.py.pre-mediadup-* 2>/dev/null | head -1' % PAYLOAD_DIR)
        newer = proc2.stdout.strip()
        if not old or not newer:
            return (['regression suites could not run: predecessor copies missing in %s'
                     % PAYLOAD_DIR], 'unavailable')
        for src, dest in ((old, 'usage_orig.py'), (newer, 'usage_pre.py')):
            proc = sh('cat %s' % src)
            if proc.returncode != 0 or not proc.stdout:
                return (['regression suites could not run: %s unreadable' % src], 'unavailable')
            Path(build, dest).write_text(proc.stdout, encoding='utf-8')
        # test_watchdog.py imports the watchdog module itself, so the module under test has
        # to travel with it: the suite must exercise THIS file, not whatever happens to be
        # importable in the build dir.
        Path(build, 'hum_integrity_watchdog.py').write_text(
            Path(__file__).read_text(encoding='utf-8'), encoding='utf-8')
        # The suites themselves come from the durable deployments dir.
        for name in ('test_billed.py', 'test_mediadup.py', 'test_watchdog.py'):
            proc = sh('cat %s/%s' % (SUITES_DIR, name))
            if proc.returncode != 0 or not proc.stdout:
                return (['regression suites could not run: %s/%s missing' % (SUITES_DIR, name)],
                        'unavailable')
            Path(build, name).write_text(proc.stdout, encoding='utf-8')

        for name in ('test_mediadup.py', 'test_billed.py', 'test_watchdog.py'):
            try:
                proc = subprocess.run([sys.executable, name], cwd=build, capture_output=True,
                                      text=True, timeout=420)
            except subprocess.TimeoutExpired:
                failures.append('%s timed out after 420s' % name)
                summary.append('%s TIMEOUT' % name)
                continue
            tail = [line for line in proc.stdout.splitlines() if 'checks,' in line]
            summary.append('%s: %s' % (name, tail[-1].strip() if tail else 'no summary line'))
            if proc.returncode != 0:
                failed = [line.strip() for line in proc.stdout.splitlines()
                          if line.startswith('  FAILED: ')]
                failures.append('%s exited %d%s' % (
                    name, proc.returncode,
                    (' — ' + '; '.join(failed)) if failed else ''))
    finally:
        shutil.rmtree(build, ignore_errors=True)
    return failures, '; '.join(summary)


# ------------------------------------------------------------------------------- main
def main() -> int:
    # parse_known_args, not parse_args: the failure-path rehearsal
    # (itguy-infrastructure-ops/scripts/rehearse_no_agent_failure.py) calls main() with its
    # OWN argv, and an unknown-argument SystemExit would kill the alert it exists to prove.
    parser = argparse.ArgumentParser(description='HUM integrity watchdog (read-only)')
    parser.add_argument('--always', action='store_true',
                        help='print the report even when everything is healthy')
    parser.add_argument('--with-suites', action='store_true',
                        help='run the regression suites regardless of the weekday')
    parser.add_argument('--no-suites', action='store_true',
                        help='skip the regression suites entirely')
    args, _unknown = parser.parse_known_args()

    failures = []
    detail = []

    status, http, box = live_check()
    if status != 'ok':
        failures.append('payload fetch: %s' % status)

    totals = None
    try:
        totals = provider_totals()
    except Exception as exc:
        failures.append('provider re-derivation failed: %s' % type(exc).__name__)

    # The display check runs AFTER the re-derivation because its zero rule compares each
    # served $0.00 against the provider's figure for the same window (see check_display).
    if status == 'ok':
        payload = box.get('payload') or {}
        display = payload.get('display')
        headers = box.get('headers') or {}
        failures.extend(check_display(display, headers, provider=totals))
        detail.append(('served', (display or ['<none>'])[0]))

    # The provider's own figures are reported even when the payload fetch failed — on a
    # broken widget, "what the money actually is" is the useful half of the alert.
    if totals:
        detail.append(('provider account today', '$%.6f' % totals['account']['today']))
        detail.append(('provider account 24h', '$%.6f' % totals['account']['last_24h']))
        parts_today = sum(totals['parts'][a]['today'] for a in AGENTS)
        residual = round(totals['account']['today'] - parts_today, 6)
        detail.append(('key partitions', '$%.6f (residual %.6f)' % (parts_today, residual)))
        if totals['media_today'] is not None:
            detail.append(('provider media today', '$%.6f' % totals['media_today']))
        if residual < -RESIDUAL_TOLERANCE_USD:
            failures.append('account total is BELOW its own key partitions '
                            '(residual %.6f): a double-count or misattribution is live'
                            % residual)

    if totals and box.get('payload') and isinstance(box['payload'].get('display'), list) \
            and len(box['payload']['display']) == 10:
        display = box['payload']['display']
        invoice = media = None
        match = INVOICE_RE.match(display[0])
        if match:
            invoice = float(match.group('invoice'))
            media = float(match.group('media')) if match.group('media') else None
        account_today = totals['account']['today']
        if invoice is not None:
            if invoice > account_today + SERVED_AHEAD_TOLERANCE_USD:
                failures.append('served invoice $%.2f is AHEAD of the provider total $%.6f '
                                'by $%.2f — the widget is OVERSTATING the day'
                                % (invoice, account_today, invoice - account_today))
            elif account_today - invoice > SERVED_STALE_MAX_USD:
                failures.append('served invoice $%.2f is $%.2f behind the provider total '
                                '— stale or failing fetch' % (invoice, account_today - invoice))
            else:
                detail.append(('served vs provider',
                               '$%.2f vs $%.6f (lag $%.2f)'
                               % (invoice, account_today, account_today - invoice)))
                if invoice == 0 and account_today <= ZERO_EPSILON_USD:
                    detail.append(('zero invoice',
                                   'served $0.00 and provider $%.6f — nothing billed in '
                                   'this window yet (legitimate, not a degraded render)'
                                   % account_today))
        if media is not None and totals['media_today'] is not None:
            drift = abs(media - totals['media_today'])
            # The served media figure is a Hermes list-price estimate while the provider
            # rows are what was actually charged, so allow a proportional drift: an
            # absolute-only tolerance false-alarms as soon as image use grows.
            allowed = max(MEDIA_TOLERANCE_USD, 0.10 * totals['media_today'])
            detail.append(('media component',
                           'served $%.2f / provider $%.6f (allowed $%.2f)'
                           % (media, totals['media_today'], allowed)))
            if drift > allowed:
                failures.append('media component $%.2f disagrees with the provider rows '
                                '$%.6f by $%.2f' % (media, totals['media_today'], drift))
        # per-agent row vs its own partition (24h window). 0-based: display[6] is Nora's
        # `billed …` row, display[8] is ITGuy's (display[7]/[9] are the model line and the
        # footer — an off-by-one here makes the check silently skip, which is worse than
        # failing).
        for who, index, agent in (('Nora', 6, 'nora'), ('ITGuy', 8, 'itguy')):
            row = AGENT_ROW_RE.match(display[index])
            if row:
                served = float(row.group('billed'))
                partition = totals['parts'][agent]['last_24h']
                if abs(served - partition) > AGENT_ROW_TOLERANCE_USD:
                    failures.append('%s 24h row $%.2f disagrees with its partition $%.6f'
                                    % (who, served, partition))
                else:
                    detail.append(('%s 24h row' % who,
                                   '$%.2f vs partition $%.6f' % (served, partition)))

    want_suites = not args.no_suites and (
        args.with_suites or datetime.now(IST).weekday() == 5)  # Saturday
    if want_suites:
        suite_failures, suite_summary = run_suites()
        failures.extend(suite_failures)
        detail.append(('regression suites', suite_summary))

    if failures:
        print('🔴 HUM INTEGRITY FAILURE — %d check(s) failed' % len(failures))
        for index, item in enumerate(failures, 1):
            print('%d. %s' % (index, item))
        print()
        print('served payload:')
        display = (box.get('payload') or {}).get('display')
        if isinstance(display, list):
            for index, text in enumerate(display, 1):
                print('%2d| %s' % (index, text))
        else:
            print('   (no payload retrieved — status: %s, HTTP %s)' % (status, http))
        if detail:
            print()
            print('independently re-derived (same 06:00-IST window):')
            for label, value in detail:
                print('  %-24s %s' % (label, value))
        print()
        print('Read-only checks; nothing was changed. Runbook: '
              '/opt/itguy/deployments/hum/README.md')
        return 0

    if args.always:
        print('HUM integrity: OK (%d checks, 0 failures)' % (len(detail) + 1))
        for label, value in detail:
            print('  %-24s %s' % (label, value))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
