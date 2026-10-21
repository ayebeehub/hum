#!/usr/bin/env python3
"""Regression suite for the HUM watchdog's display check (added 2026-10-21).

WHY THIS SUITE EXISTS
On 2026-10-21 07:00 IST the watchdog alerted twice on a HEALTHY widget. Its check was
`if '$0.00' in text:` — a substring test over all ten display strings. Two independent
faults fell out of that one line:

  1. `BILLED $0.46 (incl. media $0.00)  ·  est $0.35` was flagged because the legitimate
     media COMPONENT happened to read $0.00. The invoice was never the problem.
  2. A genuinely zero window (06:00 IST had just rolled over; ITGuy had been idle ~24h, so
     provider account today really was $0.000000) was treated as a degraded render. The
     watchdog's own re-derivation printed the refutation next to the alert.

The rule is now parsed: a served $0.00 fails only when the provider's own figure for the
same window is non-zero. `check_display` was never unit-tested, which is why a substring
test shipped in the first place — this suite is the missing test, and the last check below
is the alert payload verbatim.

Run standalone (needs hum_integrity_watchdog.py importable — run_suites() copies it in):
    python3 test_watchdog.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hum_integrity_watchdog as wd  # noqa: E402

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))


HEADERS = {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store'}


def payload(invoice='0.00', media='0.00', nora='0.00', itguy='0.00',
            footer='UPDATED 07:00 IST  ·  90s  ·  BILLED', nora_model='deepseek-v4-pro-0813',
            itguy_model='unknown'):
    """The ten display strings exactly as the skin receives them."""
    return [
        'BILLED $%s (incl. media $%s)  ·  est $0.00' % (invoice, media),
        'Nora  %s  LAST SESSION' % nora_model,
        'in 21.2k / out 280  cache 37.9k  $0.0154',
        'ITGuy  %s  LAST SESSION' % itguy_model,
        'in 263.9k / out 80.1k  cache 13.04M  $0.0310',
        'Nora  %s' % nora_model,
        'billed $%s  ·  est $4.49' % nora,
        'ITGuy  %s' % itguy_model,
        'billed $%s  ·  est $0.00' % itguy,
        footer,
    ]


def provider(today=0.0, last_24h=0.0, nora_24h=0.0, itguy_24h=0.0, media_today=0.0):
    return {'account': {'today': today, 'last_24h': last_24h},
            'parts': {'nora': {'today': nora_24h, 'last_24h': nora_24h},
                      'itguy': {'today': itguy_24h, 'last_24h': itguy_24h}},
            'media_today': media_today}


# --- 1. the false positive that started this: a zero media COMPONENT is not a zero invoice
bad = wd.check_display(payload(invoice='0.46', media='0.00', nora='0.68', itguy='0.17'),
                       HEADERS, provider=provider(today=0.447, nora_24h=0.694, itguy_24h=0.176))
check('media $0.00 with a non-zero invoice passes (the 2026-10-21 false positive)',
      bad == [], '; '.join(bad))

# --- 2. a genuinely zero window is legitimate, not a degraded render
bad = wd.check_display(payload(invoice='0.00', media='0.00', nora='0.00', itguy='0.00'),
                       HEADERS, provider=provider(today=0.0, nora_24h=0.0, itguy_24h=0.0))
check('all-zero window with a zero provider passes (06:00 IST rollover)',
      bad == [], '; '.join(bad))

# --- 3. the real fault the old check was trying to catch: served zero, provider non-zero
bad = wd.check_display(payload(invoice='0.00', media='0.00', nora='0.68', itguy='0.00'),
                       HEADERS, provider=provider(today=0.047, nora_24h=0.694, itguy_24h=0.0))
check('invoice $0.00 while the provider reports $0.047 FAILS',
      len(bad) == 1 and 'invoice of $0.00' in bad[0], '; '.join(bad))

# --- 4. an agent row that loses its money while its partition has some
bad = wd.check_display(payload(invoice='0.82', media='0.00', nora='0.68', itguy='0.00'),
                       HEADERS, provider=provider(today=0.838, nora_24h=0.694, itguy_24h=0.176))
check('ITGuy row $0.00 while its partition is $0.176 FAILS',
      len(bad) == 1 and 'ITGuy 24h row serves $0.00' in bad[0], '; '.join(bad))

# --- 5. an agent row legitimately at zero while its partition is zero
bad = wd.check_display(payload(invoice='0.68', media='0.00', nora='0.68', itguy='0.00'),
                       HEADERS, provider=provider(today=0.694, nora_24h=0.694, itguy_24h=0.0))
check('ITGuy row $0.00 with a zero partition passes (idle agent)',
      bad == [], '; '.join(bad))

# --- 6. sub-half-cent residue cannot be shown, so it must not be judged
bad = wd.check_display(payload(invoice='0.00', media='0.00', nora='0.00', itguy='0.00'),
                       HEADERS, provider=provider(today=0.004, nora_24h=0.004, itguy_24h=0.004))
check('provider residue under half a cent does not fail a $0.00 render',
      bad == [], '; '.join(bad))

# --- 7. without the provider's figures the zero rule must abstain, not guess
bad = wd.check_display(payload(invoice='0.00', media='0.00', nora='0.00', itguy='0.00'),
                       HEADERS, provider=None)
check('provider unavailable: the zero rule abstains instead of judging',
      bad == [], '; '.join(bad))

# --- 8. the parsed checks still catch a real contract break
bad = wd.check_display(payload(invoice='0.46')[:8] + ['billed n/a  ·  est $0.00',
                                                      payload()[9]],
                       HEADERS, provider=provider(today=0.447))
check('a malformed ITGuy row is still caught',
      any('does not show a billed figure' in b for b in bad), '; '.join(bad))
bad = wd.check_display(['BILLED $14.03 + media $1.71  ·  est $7.26'] + payload()[1:],
                       HEADERS, provider=provider(today=14.03))
check('an ADDITIVE media term is still caught',
      any('ADDITIVE term' in b for b in bad), '; '.join(bad))
bad = wd.check_display(payload(invoice='0.00')[:9] + ['UPDATED 07:00 IST  ·  90s  ·  EST. USD'],
                       HEADERS, provider=provider(today=0.047))
check('the EST. USD footer is still caught',
      any('EST. USD' in b for b in bad), '; '.join(bad))

# --- 9. acceptance: the 2026-10-21 07:00 IST alert payload, verbatim, must now pass
alert = [
    'BILLED $0.00 (incl. media $0.00)  ·  est $0.00',
    'Nora  deepseek-v4-pro-0813  LAST SESSION',
    'in 21.2k / out 280  cache 37.9k  $0.0154',
    'ITGuy  deepseek-v4.1-flash  LAST SESSION',
    'in 263.9k / out 80.1k  cache 13.04M  $0.0310',
    'Nora  deepseek-v4-pro-0813',
    'billed $7.94  ·  est $4.49',
    'ITGuy  unknown',
    'billed $0.00  ·  est $0.00',
    'UPDATED 07:00 IST  ·  90s  ·  BILLED',
]
alert_provider = provider(today=0.0, last_24h=7.940791, nora_24h=7.940791, itguy_24h=0.0)
bad = wd.check_display(alert, HEADERS, provider=alert_provider)
check('the 07:00 IST alert payload now passes with 0 failures',
      bad == [], '; '.join(bad))
# ...and the same payload still fails if the money is really missing
bad = wd.check_display(alert, HEADERS, provider=provider(today=0.31, nora_24h=7.940791,
                                                         itguy_24h=0.31))
check('the same payload DOES fail when the provider reports money',
      len(bad) == 2, '; '.join(bad))

failed = [name for name, ok, _ in results if not ok]
print('\n%d checks, %d passed, %d failed' % (len(results), len(results) - len(failed), len(failed)))
if failed:
    for name, ok, detail in results:
        if not ok:
            print('  FAILED: %s%s' % (name, (' — ' + detail) if detail else ''))
raise SystemExit(1 if failed else 0)
