#!/usr/bin/env python3
"""HUM billed-cost change: isolated regression tests.

Runs entirely against scratch copies + the real (read-only) sources. Proves:
  1. with NO billed set, the render is byte-identical to the pre-change renderer,
     even though the merged dict now also carries `media`;
  2. with a billed set, string 1 leads with BILLED, the 24h rows show the
     per-agent invoice beside the estimate, and the footer says BILLED;
  3. every degradation path falls back to the estimate render, never to $0.00;
  4. the staleness boundary is enforced (fresh ok, stale -> unavailable);
  5. the payload stays exactly 10 strings in every branch.
"""
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

BUILD = os.path.dirname(os.path.abspath(__file__))
SCRATCH = '/opt/data/profiles/itguy/cache/scratch'
ITGUY_DB = '/opt/data/profiles/itguy/state.db'
NORA_EXPORT = '/opt/data/hum-export/nora-usage.json'
UTC = timezone.utc

results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(('PASS  ' if ok else 'FAIL  ') + name + (('  | ' + detail) if detail else ''))


def load(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# hum_merge does `from usage import IST`, so the NEW usage must own the name
# `usage` in sys.modules before hum_merge is imported.
usage_old = load('usage_old', os.path.join(BUILD, 'usage_orig.py'))
usage_new = load('usage', os.path.join(BUILD, 'usage.py'))
hum_merge = load('hum_merge', os.path.join(BUILD, 'hum_merge.py'))
hum_billed = load('hum_billed', os.path.join(BUILD, 'hum_billed.py'))

# --- real inputs, read-only -------------------------------------------------
ledger = tempfile.mkstemp(dir=SCRATCH, suffix='.sqlite')[1]
data = usage_new.collect({'ITGuy': ITGUY_DB}, ledger)
nora = hum_merge.load_nora(NORA_EXPORT)
check('sources loaded (ITGuy ledger + Nora export)',
      bool(data.get('today')) and nora is not None)

now = datetime.now(UTC)
fake_billed = {
    'fetched_at': now.isoformat(),
    'source': 'openrouter:/analytics/query',
    'account': {'today': {'cost_usd': 11.294076}, 'last_24h': {'cost_usd': 11.251673}},
    'agents': {'itguy': {'today': {'cost_usd': 2.251893},
                         'last_24h': {'cost_usd': 2.243112}},
               'nora': {'today': {'cost_usd': 9.042183},
                        'last_24h': {'cost_usd': 9.011676}}},
}

# --- controlled renderer input ---------------------------------------------
# A brand-new ledger is legitimately `partial`/"warming up", which would mask the
# billed branches. The renderer is a pure function, so drive it with a complete,
# non-partial synthetic payload shaped exactly like collect()'s output. The real
# sources above still cover the byte-identity checks.
def row(model, cost, tokens=(4_800_000, 1_200_000, 180_000_000), status='', partial=False):
    return {'status': status, 'model': model, 'input_tokens': tokens[0],
            'output_tokens': tokens[1], 'cache_read_tokens': tokens[2],
            'cost_usd': cost, 'partial': partial}


data_ok = {
    'updated_at': now.isoformat(),
    'refresh_seconds': 90,
    'today': {'input_tokens': 9_600_000, 'output_tokens': 2_400_000,
              'cost_usd': 5.8558, 'partial': False},
    'session': {'Nora': row('deepseek/deepseek-v4-pro-0813', 0.1774,
                            (101_933, 39_364, 1_462_528), 'active'),
                'ITGuy': row('deepseek/deepseek-v4.1-flash', 0.0009,
                             (26_200, 338, 18_900), 'active')},
    'last_24h': {'Nora': row('deepseek/deepseek-v4-pro-0813', 8.0011),
                 'ITGuy': row('deepseek/deepseek-v4.1-flash', 0.3326)},
}
real_plain, _ = hum_merge.merge(data, nora, billed=None)
check('no billed set -> byte-identical render on the real (fresh) ledger',
      usage_old.render_display(real_plain) == usage_new.render_display(real_plain))
check('synthetic payload is complete (not partial)',
      not data_ok['today']['partial']
      and not any(x['partial'] for x in data_ok['last_24h'].values()))

# --- 1. no billed set: byte-identical to the pre-change renderer ------------
plain, _ = hum_merge.merge(data_ok, nora, billed=None)
old_lines = usage_old.render_display(plain)
new_lines = usage_new.render_display(plain)
check('no billed set -> render byte-identical to pre-change code', old_lines == new_lines,
      '' if old_lines == new_lines else repr([(a, b) for a, b in zip(old_lines, new_lines) if a != b]))
check('no billed set -> 10 strings', len(new_lines) == 10)
check('no billed set -> footer is the old EST. USD / PARTIAL verdict',
      new_lines[9].endswith('EST. USD') or new_lines[9].endswith('PARTIAL'), new_lines[9])
check('media present but excluded from the estimate render',
      'media' not in new_lines[0].lower(), new_lines[0])

# --- 2. fresh billed set ----------------------------------------------------
billed_set, _ = hum_merge.merge(data_ok, nora, billed=fake_billed)
lines = usage_new.render_display(billed_set)
check('billed -> 10 strings', len(lines) == 10)
check('billed -> string 1 leads with BILLED', lines[0].startswith('BILLED'), lines[0])
check('billed -> string 1 carries the account invoice $11.29', '$11.29' in lines[0], lines[0])
check('billed -> string 1 keeps the estimate visible as est', 'est' in lines[0], lines[0])
check('billed -> media spend surfaced', 'media' in lines[0].lower(), lines[0])
check('billed -> Nora 24h row shows her invoice $9.01', '$9.01' in lines[6], lines[6])
check('billed -> ITGuy 24h row shows his invoice $2.24', '$2.24' in lines[8], lines[8])
check('billed -> 24h rows keep the estimate beside it', 'est' in lines[6] and 'est' in lines[8])
check('billed -> session rows unchanged (still estimates)',
      lines[1] == new_lines[1] and lines[3] == new_lines[3])
check('billed -> footer says BILLED', lines[9].endswith('BILLED'), lines[9])

# --- 3. degradation paths ---------------------------------------------------
partial = json.loads(json.dumps(plain))
partial['last_24h']['Nora']['partial'] = True
check('billed + PARTIAL wins the footer over BILLED',
      usage_new.render_display({**hum_merge.merge(data_ok, nora, billed=fake_billed)[0],
                                'last_24h': partial['last_24h']})[9].endswith('PARTIAL'))

missing_agent = json.loads(json.dumps(fake_billed))
del missing_agent['agents']['itguy']
merged_missing, _ = hum_merge.merge(data_ok, nora, billed=missing_agent)
lines_missing = usage_new.render_display(merged_missing)
check('billed with a missing agent -> that row falls back to the estimate, not $0.00',
      '$0.00' not in lines_missing[8] and lines_missing[8] == new_lines[8], lines_missing[8])
check('billed with a missing agent -> other rows still billed', '$9.01' in lines_missing[6])
check('billed with a missing agent -> string 1 still billed', lines_missing[0].startswith('BILLED'))

empty_billed = {'fetched_at': now.isoformat(), 'account': {}, 'agents': {}}
merged_empty, _ = hum_merge.merge(data_ok, nora, billed=empty_billed)
lines_empty = usage_new.render_display(merged_empty)
check('billed set present but empty -> full fallback to the estimate render',
      lines_empty == new_lines)
check('empty billed set -> footer back to EST. USD', lines_empty[9].endswith('EST. USD'), lines_empty[9])

# --- 4. staleness boundary --------------------------------------------------
fresh_path = os.path.join(SCRATCH, 'billed-fresh.json')
stale_path = os.path.join(SCRATCH, 'billed-stale.json')
with open(fresh_path, 'w') as fh:
    json.dump(fake_billed, fh)
stale = json.loads(json.dumps(fake_billed))
stale['fetched_at'] = (now - timedelta(seconds=hum_merge.BILLED_MAX_AGE_SECONDS + 60)).isoformat()
with open(stale_path, 'w') as fh:
    json.dump(stale, fh)
check('load_billed accepts a fresh file', hum_merge.load_billed(fresh_path) is not None)
check('load_billed rejects a file past BILLED_MAX_AGE_SECONDS',
      hum_merge.load_billed(stale_path) is None)
check('load_billed rejects a missing file', hum_merge.load_billed(os.path.join(SCRATCH, 'nope.json')) is None)
bad_path = os.path.join(SCRATCH, 'billed-bad.json')
with open(bad_path, 'w') as fh:
    fh.write('{not json')
check('load_billed rejects malformed JSON', hum_merge.load_billed(bad_path) is None)

# --- 5. every string is a string, no None leaked ----------------------------
for label, candidate in (('no billed', new_lines), ('billed', lines),
                         ('missing agent', lines_missing), ('empty billed', lines_empty)):
    check(f'payload [{label}] is 10 strings', len(candidate) == 10
          and all(isinstance(x, str) for x in candidate))

# --- 6. the real billed fetcher --------------------------------------------
out = os.path.join(SCRATCH, 'billed-live.json')
hum_billed.OUT_PATH = out   # module constant captured at import time
if os.path.exists(out):
    os.unlink(out)
sys.argv = ['hum_billed.py']
try:
    rc = hum_billed.main()
except SystemExit as exc:
    rc = exc.code
check('hum_billed.py live run exited 0', rc == 0, 'rc=%s' % rc)
if rc == 0 and os.path.exists(out):
    live = json.load(open(out))
    acct_today = live['account']['today']['cost_usd']
    acct_24h = live['account']['last_24h']['cost_usd']
    check('live billed: account today is a plausible positive number',
          acct_today > 0, 'today=$%s 24h=$%s' % (acct_today, acct_24h))
    check('live billed: per-agent partitions are present',
          all(a in live['agents'] for a in ('itguy', 'nora')))
    parts = sum(live['agents'][a]['today']['cost_usd'] for a in ('itguy', 'nora'))
    check('live billed: partitions reconcile with the account total (today)',
          abs(parts - acct_today) < 0.02, 'partitions=%.6f account=%.6f' % (parts, acct_today))
    check('live billed: file mode is 0600', oct(os.stat(out).st_mode & 0o777) == '0o600',
          oct(os.stat(out).st_mode & 0o777))
    # and the full pipeline on the live figure
    merged_live, _ = hum_merge.merge(data_ok, nora, billed=live)
    lines_live = usage_new.render_display(merged_live)
    print('\n--- live payload as the skin would receive it ---')
    for i, text in enumerate(lines_live, 1):
        print('%2d| %s' % (i, text))

failed = [name for name, ok, _ in results if not ok]
print('\n%d checks, %d passed, %d failed' % (len(results), len(results) - len(failed), len(failed)))
if failed:
    for name in failed:
        print('  FAILED: ' + name)
raise SystemExit(1 if failed else 0)
