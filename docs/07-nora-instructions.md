# HUM — instructions for Nora

> **Standing document, 2026-10-20.** These are the rules your side of HUM must keep true. The dated
> change notes in this folder (`NORA-CHANGENOTE-*.md`) are history; where they disagree with this
> file, this file wins. Nothing here asks you to change code — it states what your publisher must
> publish, and what is now watching it.

## 1. What you own

Your publisher (`nora_publish.py` + its ledger) reads **your own** `state.db` read-only and writes one
small, pre-aggregated export. ITGuy's collector reads **that file only** — never your database. That
export is the trust boundary, so it must never carry messages, chat IDs, session titles, database
paths, or credentials.

## 2. The export contract (what ITGuy consumes)

One JSON object, written atomically:

- `agent` — **must be exactly `"Nora"`**; anything else and the export is discarded as unusable.
- `published_at` — ISO 8601 **with** offset. The merge parses it, and fails closed without it.
- `today`, `session`, `last_24h` — each a dict; a non-dict makes the export unusable.
- `media_gen` — a dict carrying at least `cost_today_usd` and `cost_24h_usd`. This is what puts
  `(incl. media $X)` on the widget's first line. Since 2026-10-20 it carries `key_scoped: true` and
  the figure is filtered to **your own key** (`filters=[{api_key_id in [NORA_KEY_HASH]}]`); before
  that it read the whole account and overstated you by ~$9.94 lifetime. The hash is pinned in your
  publisher *and* in ITGuy's `orcost-agents.json` — **a key rotation must update both**, or media
  silently mis-scopes with no error anywhere.
- `reconciliation` — carried through to AB **untouched**. Only `status` is read here; the USD fields
  are context for him.
- `partial` — OR'd into the widget footer. Keep it honest.

**Freshness.** An export older than **300 s** (`STALE_AFTER_SECONDS` in `hum_merge.py`) is still
summed but forces the `PARTIAL` footer. A missing or unparsable export renders your whole column as
*unavailable* rather than a guessed number — we fail closed on purpose.

**Never soften `reconciliation`.** Report what you measure; a `mismatch` is surfaced to AB, not
hidden, and definitely not recomputed on this side.

## 3. The money rules (learned the hard way, 2026-10-20)

1. **The provider invoice is authoritative.** The widget's `BILLED $X` is what OpenRouter actually
   charged for the day since 06:00 IST. Your figures are Hermes' local list-price estimates — they
   will **never** equal `BILLED`, and that is by design, not a bug to chase.
2. **Media generation is a component, never an addend.** Image-model spend bills to **your own key**,
   so it is *already inside* the account total and *already inside* your per-agent row. The widget
   used to print `BILLED $A + media $M`, which overstated the day by the whole media amount. It now
   prints `BILLED $A (incl. media $M)`, and only when `M <= A`. If you ever add media to a total
   yourself, you reintroduce exactly that bug.
3. **Your 24 h row on the widget carries the provider's per-key invoice**, with your estimate beside
   it (`billed $15.80 · est $11.90`). Two different quantities, printed side by side, never summed.

## 4. What is watching it now

Since 2026-10-20 there is an **independent daily check** on the ITGuy side (07:00 IST, script-only):

- it fetches the served payload and asserts the display contract — 10 strings; `BILLED` carrying **no**
  additive term; media `<=` invoice; both agent rows carrying a billed figure; no `$0.00` leak; the
  footer not stuck in its estimate form;
- it re-derives the provider's own account total, per-key partitions and **image-model rows** for the
  same 06:00-IST window, and compares them against what is served — including the media figure, which
  is **your** `media_gen.cost_today_usd` carried through;
- **it says nothing when healthy.** An alert prints which check failed and what it measured;
- it never reads your `state.db`, and it does not import the widget's renderer — so it can't inherit a
  bug from either side.

**Triaging an alert:** if the failing check names the media component or the account-vs-partitions
residual, that is the provider/billed side — escalate to ITGuy. If your column is empty, `$0.00`, or
the footer reads `PARTIAL` while your export is fresh, that is your side: start with
`reconciliation.status` and the timestamp of your last publish.

## 5. What to run before saying "done"

```bash
python3 nora/test_publish.py     # export shape, atomicity, no-content guarantee
python3 nora/test_usage.py       # collector / window / ledger behaviour
```

The server-side suites also live in the package (`tests/test_billed.py`, `tests/test_mediadup.py`,
19 + 36 checks) and are run against the **live** code every Saturday by the watchdog. They assert the
display contract and the media accounting rule — i.e. the contract your export feeds into. If your
change trips one of them, ITGuy hears about it on the next Saturday run.

## 6. Change protocol

- Anything that alters a **total** needs a note to AB with before/after figures, the evidence (raw
  provider numbers if you have them) and the date. That is how the media rule above got established.
- Don't rename or drop export fields silently. The merge tolerates *extra* fields, but if a field the
  renderer reads goes missing (`media_gen.cost_today_usd`, `media_gen.cost_24h_usd`) a line silently
  disappears from AB's widget.
- If the reconciliation schema changes, say so in the same note — this side reads only `status`.

## 7. Mirror accuracy — closed 2026-10-20

**This item is resolved.** The package copy of `nora_publish.py` now matches your live publisher
byte-for-byte: `7f1e0590…`, 13,172 B, at `nora/nora_publish.py` on `main` of
`github.com/ayebeehub/hum` (commit `8cd5761`). `nora/test_publish.py` is `8107c2df…` (9,946 B);
12/12 tests pass. Verified from a fresh clone against your live files, not from a working tree.

It took two passes, which is worth recording: the first file you sent was already superseded six
minutes after it was mirrored, so the mirror was briefly correct and then wrong again. The lesson on
this side is that **a "done" card is not the end of the thread** — re-read the card before declaring
a mirror current. If you change your publisher again, send it with a short note saying which earlier
one it supersedes, and say it in the card body, not only in a comment.

Two related items remain deliberately open, both structural and both AB's call:

1. `nora/` ships no `usage.py`. Your live publisher imports `_session(..., gross=True)`, and
   `server/usage.py` has no such parameter, so `nora/` is a **reference copy, not a runnable unit**.
   Shipping your `usage.py` into the package would make it runnable — but it also copies more of your
   own code into a shared repo, so it is AB's decision, not a tidiness fix.
2. `NORA_KEY_HASH` now exists in two places (your publisher and ITGuy's `orcost-agents.json`). A
   rotation that touches only one produces a quietly wrong media figure. Add it to any rotation
   checklist.

## 8. Where everything lives

- Widget runbook + live status: `/opt/itguy/deployments/hum/README.md`
- The 2026-10-20 fix with the measurement: `audit/2026-10-20-hum-media-double-count.md` (host),
  `history/2026-10-20-media-double-count-fix.md` (package)
- Same-day change note: `NORA-CHANGENOTE-2026-10-20-media-fix.md` (this folder)
- The code the widget actually runs: `/opt/hum/*.py` — root-owned, so every code change needs exactly
  one root install step on the host, staged and self-verifying, never automatic
- Package mirror: `github.com/ayebeehub/hum` (private)
