# 2026-10-21 — the watchdog's `$0.00` check: a substring test replaced by a parsed comparison

**Status:** fixed, deployed, tested. **Scope:** the watchdog only (`ops/hum_integrity_watchdog.py`).
No server, no widget, no `hum_merge`/`usage.py`/`hum_billed.py` change; the live payload and `MainPID`
were never touched.

## What happened

The daily **HUM Integrity Watchdog** (job `0d3d78e3ec5d`, 07:00 IST) alerted with two failures on a
widget that was serving correct figures:

```
1. string 1 is $0.00 (a degraded render leaked to the skin): 'BILLED $0.00 (incl. media $0.00)  ·  est $0.00'
2. string 9 is $0.00 (a degraded render leaked to the skin): 'billed $0.00  ·  est $0.00'
```

The alert's own independent re-derivation, printed in the same message, refuted both:

```
provider account today   $0.000000
key partitions           $0.000000 (residual 0.000000)
ITGuy 24h row            $0.00 vs partition $0.000000
```

07:00 IST is one hour past the 06:00 IST window boundary, and ITGuy had been idle ~24 h. `$0.00` was
the truth.

## Root cause — one line, two independent faults

```python
for index, text in enumerate(display, 1):
    if '$0.00' in text:
        bad.append('string %d is $0.00 ...' % index)
```

1. **Substring, not a field.** It matched any string *containing* `$0.00`, including the legitimate
   media **component** of a day with no image generation. It was aiming at a lost invoice; it hit
   `(incl. media $0.00)`.
2. **Zero treated as evidence of breakage.** A true zero — a window that just rolled over, an idle
   agent — was indistinguishable from a degraded render, because the check never consulted the
   provider before judging.

## Fix

- Every money field is **parsed** and compared against the provider's own figure for the same window:
  the invoice against the account total, each agent row against its own key partition. A served
  `$0.00` fails only while the provider reports otherwise (`ZERO_EPSILON_USD = 0.005` — the display
  cannot show less than half a cent, so residue below it is not a render fault).
- The **media component is exempt** from the zero rule: it is judged by the existing proportional
  drift check, and a zero there is normal.
- `check_display(display, headers, provider=None)` — with the provider unavailable the rule
  **abstains**; a check that cannot see the money must not judge it.
- `main()` now re-derives the provider figures **before** the display check instead of after.
- A legitimate zero is reported explicitly so the next reader does not re-open this:
  `zero invoice: served $0.00 and provider $0.000000 — nothing billed in this window yet`.

## Why it shipped — and the test that now stops it

`check_display` had no unit test. `tests/test_watchdog.py` (12 checks) is that missing test; it pins
the false positive, the genuine-zero case, both real-fault directions, the sub-half-cent residue case,
the provider-unavailable abstention, the surviving contract checks, and the **07:00 alert payload
verbatim** as an acceptance case.

| code under test | result |
|---|---|
| pre-fix (substring) | 4 passed / 8 failed — reproduces the alert text exactly |
| fixed (parsed) | 12 passed / 0 failed |

`run_suites()` now copies the watchdog module next to the suites, so the file exercised is the file
under test.

## Verified

- `hum_integrity_watchdog.py --always --with-suites` against the live stack:
  `test_watchdog.py: 12 checks, 12 passed, 0 failed`; no zero-rule failure on a healthy payload
  (`BILLED $1.10 (incl. media $0.00)`, provider `$1.182987`, residual `0.000000`).
- `test_billed.py` 36/36 unchanged.

## Rollback

Pre-fix file: `ops/hum_integrity_watchdog.py` at commit `02053e4`. The fix is a single commit; `git
revert` restores the substring behaviour (and its false positives).

## Left open, deliberately

`tests/test_mediadup.py` fails 1 of 19 with the **same class** of bug, in a different file:

```python
check('media today is a SUBSET of the account invoice (component, not an addend)',
      media_today is not None and 0 < media_today <= acct_today, ...)
```

`0 < media_today` requires media to be **positive**, so any day with no image generation fails a check
about media *not exceeding* the invoice. Not fixed in this commit: it was not the approved change. It
surfaces on Saturdays (when the suites run) until it is.

## Lesson

A check that judges money must first *parse* the money, and must know what zero means before it calls
zero a fault. Substring matching over a formatted display is a category error: it cannot tell a field
from a coincidence.
