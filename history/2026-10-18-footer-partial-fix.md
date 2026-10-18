# 2026-10-18 — stuck `PARTIAL` footer fixed; reconciliation schema change

Two defects were closed on 2026-10-18. Neither changed the widget's design, the data boundary,
the port, or the units.

## 1. The footer read `PARTIAL` permanently (server-side bug, fixed)

**Symptom.** The tenth display string — the footer — showed
`UPDATED <hh:mm> IST · 90s · PARTIAL` continuously, even when the ledger reported
`today OK / 24h OK` and Nora's export was fresh with `partial: false`.

**Root cause.** `usage.collect()` is called with ITGuy only, by the data-boundary contract
(Nora's figures come from her published export, never her database). But it computed its
missing-agent list against a hardcoded tuple:

```python
missing = [name for name in ('Nora', 'ITGuy') if name not in data]
```

`data` can only ever contain ITGuy, so that list was always `['Nora']`, and
`gross['partial'] = bool(missing) or ...` was therefore structurally `True`. `hum_merge.merge()`
OR'd it into `today['partial']` and `render_display()` printed `PARTIAL`.

The flag was hard-wired: no amount of clean data could clear it. A 186-second park test on
2026-10-17 had added a second, temporary cause of the same flag — which is why the flag appeared
"not to clear" when that gap aged out of the window.

**Fix** (`server/usage.py`, one statement plus comment):

```python
missing = [name for name in ('Nora', 'ITGuy')
           if name in sources and name not in data]
```

Only an agent that was *requested* and did not come back now counts as missing. Nora's absence is
already handled explicitly in `hum_merge.py`: an unusable export yields null windows, and an export
older than 300 s forces `PARTIAL`.

**Verification.** Old and new code were run side by side against a synthetic ledger covering the
full trailing 24 h, with ITGuy's real `state.db` opened read-only:

| case | before | after |
|---|---|---|
| clean export | `PARTIAL` | `EST. USD` |
| export stale (>300 s) | `PARTIAL` | `PARTIAL` |
| export absent | `PARTIAL` | `PARTIAL` |
| real 400 s ledger gap | `PARTIAL` | `PARTIAL` |

Ten display strings in every case. Live: footer `EST. USD` across successive 90 s refreshes, with
the ledger advancing and coverage `OK` on both windows.

**Operational trap found during deployment.** `ops/exec_hum_install.sh` ends with
`systemctl enable --now hum.service`, which is a no-op on an already-running unit — the byte-level
install succeeds while the old module stays loaded, so behaviour does not change. After any code
install, run `exec_hum_control.sh restart` and confirm the **PID changed** before believing the new
code is live.

## 2. Nora's `reconciliation` reported `mismatch` permanently (fixed on Nora's side)

Her check asserted that total `session_model_usage` cost equals the sessions-summary cost. That
invariant is false: the sessions table records only settled conversation cost, while
`session_model_usage` also carries auxiliary-task rows (background review, title generation,
approval, vision, image generation, compression) whose estimates never roll into any session
summary. The comparison therefore ran a total-with-aux against a total-without-aux, producing a
permanent, growing mismatch.

She now reconciles settled rows only and consolidates auxiliary spend into one figure. Her published
numbers reconcile exactly: `71.94359504 + 13.68493914 = 85.62853418`.

**Schema change.** `model_usage_total_cost_usd` and `model_usage_conversation_cost_usd` were
removed; the fields are now `status`, `total_cost_usd`, `session_summary_cost_usd`,
`auxiliary_cost_usd`, `difference_usd`, `checked_at`. Nothing on the ITGuy side referenced the old
names — `hum_merge` passes the reconciliation dict through opaquely and `obs_hum.sh` reads only
`.status` — so no server change was needed for the rename.

Media generation is **not** part of `auxiliary_cost_usd`; it remains account-scoped in the separate
`media_gen` block. An early hypothesis that the residual was media-related was refuted with
arithmetic and is recorded here only so it is not re-derived.

## Current state

- `hum.service` and `hum-sampler.timer`: active / enabled
- Listener: `100.88.230.67:8765`, tailnet only; 10-string payload
- Footer under healthy conditions: `EST. USD`
- `reconcile`: `ok`, `difference_usd: 0.0`
- No open items on either side
