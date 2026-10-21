# 2026-10-21 — `test_mediadup.py`: the sign test that failed on a legitimate zero

**Status:** fixed, verified. **Scope:** one assertion in `tests/test_mediadup.py`. No shipped code,
no server, no widget — the suite was wrong, not the money.

## The failure

```
FAIL  media today is a SUBSET of the account invoice (component, not an addend)  |  media=0.0 account=1.404756
19 checks, 18 passed, 1 failed
```

```python
media_today = (nora or {}).get('media_gen', {}).get('cost_today_usd')
check('media today is a SUBSET of the account invoice (component, not an addend)',
      media_today is not None and 0 < media_today <= acct_today, ...)
```

The check is about media *not exceeding* the invoice. The `0 <` turned it into "media must be
**positive**", which fails on any day with no image generation — the truth, not a fault: the provider
reported `media today $0.000000` in the same run.

It had been failing silently: the suites run on **Saturdays** (or with `--with-suites`), so the first
run to surface it was the manual one on 2026-10-21 that accompanied the watchdog fix.

## Fix — assert the property, not the sign

```python
def media_is_subset(media, account):
    if isinstance(media, bool) or not isinstance(media, (int, float)):
        return False          # a missing/broken field is NOT a zero
    return 0 <= media <= account
```

Zero passes. A missing field still fails. Media above the invoice still fails — the original
double-count shape from 2026-10-20. A bool is not money.

Four checks were added for the predicate itself, so "the live check passes" can never quietly mean
"the check stopped checking": zero passes · `None` fails · `account + 5` fails · `True` fails.
**19 → 23 checks.**

## Verified

```
test_mediadup.py: 23 checks, 23 passed, 0 failed
test_billed.py:   36 checks, 36 passed, 0 failed
test_watchdog.py: 12 checks, 12 passed, 0 failed
```

71 checks, 0 failures, against the live stack (`--always --with-suites`).

## Rollback

The pre-fix suite is `tests/test_mediadup.py` at commit `a525b36`; `git revert` of the commit below.

## The pattern worth keeping

Twice in one day, in two files, the same mistake: a check that asserts a **sign** or a **substring**

- `0 < media_today` — "media must be positive"
- `'$0.00' in text` — "no string may contain a zero"

...instead of the **property** it actually cares about:

- "media must not exceed the invoice it is a component of"
- "a served zero must agree with what the provider says the money is"

Sign tests and substring tests both fail on legitimate zeros, and a check that cries wolf on a healthy
system trains its reader to ignore it — which is worse than not having the check. Assert the property,
and pin the property's fault cases with their own tests.
