#!/usr/bin/env python3
"""HUM media double-count fix: regression + LIVE reconciliation tests.

Proves, against the REAL sources and a REAL provider query:
  1. the account total is a superset of the pinned key partitions (residual >= 0);
  2. media is a COMPONENT of the account invoice, never an addend;
  3. the rendered today invoice equals the account figure exactly;
  4. the blast radius is exactly one display string;
  5. the estimate-only render is byte-identical to the pre-change code;
  6. build() itself refuses to publish an account total below its partitions;
  7. every branch still yields exactly 10 strings.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

BUILD = os.path.dirname(os.path.abspath(__file__))
SCRATCH = '/opt/data/profiles/itguy/cache/scratch'
ITGUY_DB = '/opt/data/profiles/itguy/state.db'
NORA_EXPORT = '/opt/data/hum-export/nora-usage.json'
UTC = timezone.utc

results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(('PASS  ' if ok else 'FAIL  ') + name + (('  |  ' + detail) if detail else ''))


def load(mod, path):
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[mod] = m
    spec.loader.exec_module(m)
    return m


def media_is_subset(media, account):
    """`media` is a COMPONENT of the invoice: it must exist and must never exceed its parent.

    Zero is legitimate — a day with no image generation reports $0.00, and requiring positivity
    made this check fail every such day. That was the same "zero must mean broken" mistake the
    watchdog's `'$0.00' in text` substring test made (history/2026-10-21-watchdog-zero-rule.md);
    it is fixed here the same way: name what must actually hold rather than assert a sign.
    A missing field is still a failure (a broken export is not a zero), and so is a bool.
    """
    if isinstance(media, bool) or not isinstance(media, (int, float)):
        return False
    return 0 <= media <= account


# hum_merge does `from usage import IST`, so the NEW usage must own the name
# `usage` in sys.modules before hum_merge is imported.
usage_pre = load('usage_pre', os.path.join(BUILD, 'usage_pre.py'))
usage = load('usage', os.path.join(BUILD, 'usage.py'))
hum_merge = load('hum_merge', os.path.join(BUILD, 'hum_merge.py'))
hum_billed = load('hum_billed', os.path.join(BUILD, 'hum_billed.py'))

# --- real sources -----------------------------------------------------------
ledger = tempfile.mkstemp(dir=SCRATCH, suffix='.sqlite')[1]
data = usage.collect({'ITGuy': ITGUY_DB}, ledger)
nora = hum_merge.load_nora(NORA_EXPORT)
check('real sources loaded (ITGuy ledger + Nora export)',
      bool(data.get('today')) and nora is not None)

# --- real provider query ----------------------------------------------------
billed = hum_billed.build()
acct_today = billed['account']['today']['cost_usd']
check('live billed set built from the provider', isinstance(billed, dict) and acct_today > 0)
for w in ('today', 'last_24h'):
    info = billed['account'][w]
    check('[%s] account total is a SUPERSET of the key partitions (residual >= 0)' % w,
          info['key_partition_residual_usd'] >= -hum_billed.RESIDUAL_TOLERANCE_USD,
          'account=%.6f partitions=%.6f residual=%.6f' % (
              info['cost_usd'], info['key_partition_usd'], info['key_partition_residual_usd']))

media_today = (nora or {}).get('media_gen', {}).get('cost_today_usd')
check('media today is a SUBSET of the account invoice (component, not an addend)',
      media_is_subset(media_today, acct_today),
      'media=%s account=%.6f' % (media_today, acct_today))
# The predicate's own fault cases, so "the live check passes" can never quietly mean
# "the check stopped checking". These are the shapes that motivated it in the first place.
check('predicate: a zero media day PASSES (no image generation today)',
      media_is_subset(0.0, acct_today), 'media=0.0')
check('predicate: a missing media field FAILS (a broken export is not a zero)',
      not media_is_subset(None, acct_today), 'media=None')
check('predicate: media above the invoice FAILS (the double-count shape)',
      not media_is_subset(acct_today + 5.0, acct_today), 'media=account+5')
check('predicate: a bool is not money', not media_is_subset(True, acct_today), 'media=True')

# --- the actual render ------------------------------------------------------
merged, _ = hum_merge.merge(data, nora, billed=billed)
lines = usage.render_display(merged)
print('\n--- LIVE PAYLOAD (real sources, real invoice) ---')
for i, text in enumerate(lines, 1):
    print('%2d| %s' % (i, text))
print()

check('live render -> exactly 10 strings',
      len(lines) == 10 and all(isinstance(x, str) for x in lines))
money = re.escape('${:,.2f}'.format(acct_today))
check('line 1 carries the account invoice EXACTLY (never invoice + media)',
      re.match(r'^BILLED ' + money + r'( \(incl\. media \$[\d,\.]+\))?(  \u00b7  est \$[\d,\.]+)?$',
               lines[0]) is not None, lines[0])
check('line 1 is not additive (no " + media")', ' + media' not in lines[0], lines[0])
check('line 1 shows media as a component', 'incl. media' in lines[0], lines[0])

# --- exact form on a COMPLETE payload (fresh sandbox ledgers legitimately lack
# an est figure, so the tail clause is absent above; assert the full shape here)
def row(model, cost, tokens, status='', partial=False):
    return {'status': status, 'model': model, 'input_tokens': tokens[0],
            'output_tokens': tokens[1], 'cache_read_tokens': tokens[2],
            'cost_usd': cost, 'partial': partial}


data_ok = {
    'updated_at': data['updated_at'],
    'refresh_seconds': 90,
    'today': {'input_tokens': 9_600_000, 'output_tokens': 2_400_000,
              'cost_usd': 6.49, 'partial': False},
    'session': {'Nora': row('deepseek/deepseek-v4-pro-0813', 0.0838, (92_200, 7_600, 360_400), 'active'),
                'ITGuy': row('deepseek/deepseek-v4.1-flash', 0.0209, (185_700, 91_600, 4_970_000), 'active')},
    'last_24h': {'Nora': row('deepseek/deepseek-v4-pro-0813', 8.87, (2_500_000, 650_000, 42_000_000)),
                 'ITGuy': row('deepseek/deepseek-v4.1-flash', 0.45, (4_200_000, 1_280_000, 138_000_000))},
}
l5 = usage.render_display(hum_merge.merge(data_ok, nora, billed=billed)[0])
check('complete payload -> exactly 10 strings', len(l5) == 10)
check('complete payload -> form "BILLED $X (incl. media $Y)  \u00b7  est $Z"',
      re.fullmatch(r'BILLED \$[\d,\.]+ \(incl\. media \$[\d,\.]+\)  \u00b7  est \$[\d,\.]+', l5[0]) is not None,
      l5[0])

# --- blast radius: exactly one string changed -------------------------------
pre_lines = usage_pre.render_display(merged)
diffs = [(i, a, b) for i, (a, b) in enumerate(zip(pre_lines, lines), 1) if a != b]
check('only display string 1 differs from the live renderer',
      len(diffs) == 1 and diffs[0][0] == 1, repr(diffs))

# --- guard: a media figure larger than the parent is never printed ----------
big_media = dict(nora['media_gen'])
big_media['cost_today_usd'] = acct_today + 5.0
m2, _ = hum_merge.merge(data, {**nora, 'media_gen': big_media}, billed=billed)
l2 = usage.render_display(m2)
check('guard: media larger than the invoice is not printed as a component',
      'media' not in l2[0].lower(), l2[0])
check('guard: still 10 strings', len(l2) == 10)

# --- byte-identity of the estimate-only path --------------------------------
plain, _ = hum_merge.merge(data, nora, billed=None)
check('no billed set -> render byte-identical to the pre-change code',
      usage_pre.render_display(plain) == usage.render_display(plain))
check('no billed set -> 10 strings', len(usage.render_display(plain)) == 10)

# --- degradation branches ---------------------------------------------------
empty = {'fetched_at': datetime.now(UTC).isoformat(), 'account': {}, 'agents': {}}
l3 = usage.render_display(hum_merge.merge(data, nora, billed=empty)[0])
check('empty billed set -> full fallback, 10 strings',
      len(l3) == 10 and l3[9].endswith(('EST. USD', 'PARTIAL')), l3[0])

missing = json.loads(json.dumps(billed))
del missing['agents']['itguy']
l4 = usage.render_display(hum_merge.merge(data, nora, billed=missing)[0])
check('missing agent partition -> 10 strings, no fabricated $0.00',
      len(l4) == 10 and '$0.00' not in l4[8], l4[8])

# --- the invariant fires inside build() itself ------------------------------
fake_env = os.path.join(SCRATCH, 'mediadup-fake-env')
with open(fake_env, 'w', encoding='utf-8') as fh:
    fh.write('ITGUY_OR_MANAGEMENT=fake-for-test\n')
orig_env, orig_qc = hum_billed.ENV_PATH, hum_billed.query_cost


def evil(key, start, end, hashes=None):
    return 1.00  # account 1.00 while each of the two partitions claims 1.00


hum_billed.ENV_PATH, hum_billed.query_cost = fake_env, evil
raised = False
try:
    hum_billed.build()
except RuntimeError:
    raised = True
finally:
    hum_billed.ENV_PATH, hum_billed.query_cost = orig_env, orig_qc
check('build() refuses to publish an account total below its partitions', raised)

# --- host consistency: the deploy must be idempotent on the live file -------
failed = [n for n, ok, _ in results if not ok]
print('\n%d checks, %d passed, %d failed' % (len(results), len(results) - len(failed), len(failed)))
for n in failed:
    print('  FAILED: ' + n)
raise SystemExit(1 if failed else 0)
