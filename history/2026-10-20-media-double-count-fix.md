# HUM — provider-invoice media double-count: fixed and deployed (2026-10-20)

**STATUS: DEPLOYED AND VERIFIED.** Live string 1 now reads
`BILLED $14.93 (incl. media $1.71)  ·  est $6.86`.

**Authorized by:** AB, Telegram — *"Make a plan Fix this once and for all"* … then
*"all allowed. dont trust ur judgment, verify everything against facts and exact status.
fix this. double check for no errors. report back"* … install run by AB: *"done"*.
**Scope:** 3 files in `/opt/hum` (+ payload copies), one `hum.service` restart, docs, audit.
**No** credential, DNS, sudoers, firewall, ledger or profile-data change.

## The fault (verified, not inferred)

Display string 1 rendered `BILLED $14.03 + media $1.71`. Media spend is **already inside** the
account invoice, so the additive form overstated the day by the entire media amount ($1.71 at
diagnosis; media billed in 24 h was $4.30, so the error scales with image/video use).

Measured 2026-10-20 against OpenRouter `/analytics/query`, same 06:00-IST window the widget uses:

| Fact | Measured value |
|---|---|
| Nora's published media today (`media_gen.cost_today_usd`) | `1.706008` |
| Image-model rows billed to Nora's key | `gemini-3-pro-image 1.501922` + `gemini-3.1-flash-image 0.204086` = **`1.706008`** |
| Account total vs pinned key partitions | `14.034520` vs `itguy 2.93… + nora 11.09…` → **residual `0.000000`** |
| Provider tokens vs Hermes tokens | OpenRouter prompt `142,628,065` ≈ Hermes `4,251,288` fresh + `138,147,328` cache |

Identical to the cent ⇒ media is a **component** of both the account total and Nora's per-agent row.
The false belief was embedded in the code: `hum_billed.py`'s note string said *"media generation is
billed separately and is NOT included"*. That sentence made the renderer add it, and it is why the
2026-10-19 audit's open item ("media = the one bucket billed outside the key ledger") was wrong —
media was never outside it.

## Why est and billed diverge so far (answered for AB; deliberately no code change)

The ~$6.5 gap on the day decomposes exactly: **$2.17** ITGuy model mispricing (Hermes charges cache
reads at $0.0016/M; the endpoints actually serving him — Wafer, DigitalOcean, Together, Morph,
InferenceNet — bill prompt tokens at ~$0.018/M whether cached or not; token volumes agree to 0.2%,
so this is purely the cache discount the serving endpoints do not grant), **$1.82** benchmark
sandbox (`tencent/hy4-preview`, excluded from estimates by design), **$1.71** image generation (same),
**$0.81** Nora peak-hour uplift (1.13x).

Not "fixed" on purpose: the local estimate cannot track a provider-mix invoice, and a calibrated
constant would be a fudge that drifts. The invoice is now the primary figure.

## The change

- `usage.py` — string 1 → `BILLED $A (incl. media $M)`, and the component is printed only when it is
  genuinely `<=` the invoice. Blast radius: **exactly one display string**.
- `hum_merge.py` — comment only, recording that media bills to Nora's own key.
- `hum_billed.py` — corrected the false note, and **new invariant**: refuse to publish (previous good
  `billed.json` stays; class-name-only warning) when the account total is below the sum of the pinned
  key partitions. Tolerance `RESIDUAL_TOLERANCE_USD = 0.05`, because the account snapshot is requested
  first and can be seconds older than the partitions.

## Evidence — pre-deploy (against byte-identical code)

- Staged copies sha256-matched the live files (`usage.py 34150cb3…`, `hum_merge.py ddeaa304…`,
  `hum_billed.py 2639af50…`).
- `test_mediadup.py` — **19/19 PASS**, live-reconciled: superset invariant, media-is-a-subset, exact
  invoice rendering, blast radius = 1 string, media-oversize guard, byte-identity of the estimate-only
  path, every degradation branch at 10 strings, and `build()` refusing a tampered
  account-below-partitions payload.
- `test_billed.py` (pre-existing) — **36/36 PASS** with the patched code.
- Installer validator proven to discriminate: **exit 0** on the new payload, **exit 1** on the old
  additive payload.
- Installer/rollback: `bash -n` clean; `sha256sum -c MANIFEST.sha256` passes on the host; root guard
  refuses a non-root run.
- Self-review of the installer found and fixed two failure modes before hand-over: a re-run could
  overwrite the rollback baseline (now written once, oldest stamp wins), and the new `hum_billed.py`
  was never exercised (now kicked and read back).

## Evidence — post-deploy

- Installed hashes == staged bytes: `da110d00…` (usage.py) / `e540858e…` (hum_merge.py) /
  `dc083fbe…` (hum_billed.py); `/opt/hum` and `/opt/itguy/hum-payload` identical.
- Rollback baseline `*.pre-mediadup-20261019T203403Z` verified to hold the exact pre-change bytes
  (`34150cb3…` / `ddeaa304…` / `2639af50…`).
- `hum.service` MainPID **3555397 → 3591552**, `NRestarts=0` (fresh start, not a restart loop);
  journal `Stopped 20:34:03 → Started 20:34:03`; listening on `100.88.230.67:8765` only.
- Payload: 10 strings, `Content-Type: application/json; charset=utf-8`, `Cache-Control: no-store`;
  string 1 `BILLED $14.03 (incl. media $1.71)  ·  est $6.81`, later `$14.93` — never additive.
- New fetcher proven live: first real run `20:39:10Z`, `ExecMainStatus=0`, `billed.json`
  **851 → 1056 bytes** (the `key_partition_usd` / `key_partition_residual_usd` fields). The earlier
  20:34:05 run exited 0 via `--if-stale 240` without writing — a static mtime is not a failure.
- Ledger coverage `today OK` / `24h OK`, Nora export fresh, `reconcile: ok`.
- Independent re-derivation for the fetch instant: account `$14.955148` = itguy `$3.059720` + nora
  `$11.895428`, residual `0.000000`; the displayed `$14.93` differs by 2.5 cents — provider snapshot
  lag, not a display fault (documented Analytics behaviour).

## Operations note — one root step per payload change

`/opt/hum` is root-owned and `exec_hum_install.sh` only installs from `/opt/itguy/hum-payload`
(root-owned, not writable by ITGuy), with no cron/watcher path. So every HUM payload change needs:

    sudo bash /opt/itguy/temp/hum_stage/mediadup/install_hum_mediadup.sh

A self-service vetted deploy script was considered and **rejected**: it would require a sudoers entry
executing a payload from an ITGuy-writable directory, which is root-equivalent for the itguy account.
The manual step is the security-correct choice.

## Rollback

    sudo bash /opt/itguy/temp/hum_stage/mediadup/rollback_hum_mediadup.sh

Restores every `*.pre-mediadup-<stamp>` backup in `/opt/hum` and `/opt/itguy/hum-payload`, restarts
`hum.service`, and re-verifies the endpoint.

## Docs updated

`hum-widget-ops` skill (string-1 contract, new pitfalls 14–15, status), its
`references/billed-display.md` (component rule, account-superset invariant, acceptance tests 6–7,
rollback), and `/opt/itguy/deployments/hum/README.md` (dated section).

## Follow-up (same day): an independent watchdog

Fixing the display did not fix the *class* of failure. The figures on that widget had only ever
been compared by a human looking at a dashboard — nothing automated would have caught the bug —
so the same silent-drift risk remained for an accounting change, a key rotation, or a new bucket
added on top of the invoice.

Added `ops/hum_integrity_watchdog.py` (agent-side, read-only), running daily at 07:00 IST as a
`no_agent` cron job — empty stdout when healthy, so it delivers nothing unless something is wrong.
It does three things:

1. **Display contract.** Fetches the served payload over the tailnet and asserts 10 strings, a
   `BILLED` string 1 with **no additive term**, a media component `<=` the invoice, per-agent rows
   that carry a billed figure, no `$0.00` leak, the expected headers, and a footer that is not
   `EST. USD` (which would mean the billed fetcher had been dead for more than 900 s).
2. **Independent re-derivation.** Queries the provider for the same 06:00-IST window and compares:
   the served invoice must never be **ahead** of the provider's own total (the direction that
   overstates), the account-vs-partitions residual must stay inside the same tolerance the fetcher
   enforces, the media component must match the provider's image-model rows, and each served
   per-agent 24 h row is checked against its own partition.
3. **Regression suites** (`tests/`) against the live code, every Saturday.

It deliberately does **not** import `usage.py` or `hum_merge.py`, so a bug in the widget's own
arithmetic cannot hide from it. Verified the same day: the exact pre-fix payload
(`BILLED $14.03 + media $1.71`) is caught as an additive term, a media-over-invoice case and a
`$0.00`/dead-fetcher case are caught, the failure path renders its alert and exits 0 (rehearsed
with `rehearse_no_agent_failure.py` and separately against a dead endpoint), and a healthy run
emits **zero bytes**.
