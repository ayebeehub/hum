# HUM build status and integration handoff

## Implemented locally

- `usage.py` reads Hermes `state.db` files with `mode=ro`, never writes them, and persists counter snapshots and 90-second deltas only in its separate `--ledger` SQLite file.
- `--display` emits a single JSON object with a fixed ten-string `display` array for the native Rainmeter WebParser. The regular CLI output includes structured fields for verification. Neither output contains messages, chat IDs, session titles, source paths, or credentials.
- Today sums gross changes across every model and task since 06:00 IST. Session selects the most recently active direct-message conversation and sums its conversation rows. Last 24 hours sums conversation-only changes per agent. Cron and benchmark models matching `tencent/*` or basename `glm-*` are excluded from session and 24h rows.
- The first sample is a baseline, not historical usage. The daily and 24-hour windows show `partial=true` until their complete coverage period has been sampled. Each polling interval places changes at collection time, so a boundary can be uncertain by up to one polling interval. Outages longer than 180 seconds mark affected windows partial.
- `HUM.ini` has 90-second WebParser polling, current-skin close via `!DeactivateConfig`, a collapsible header, desktop-level placement, ten field measures, and a stale footer on connection or parse errors. Its endpoint is an inert loopback placeholder until the private service is deployed.

## Live verification performed by Nora

- Ran the collector against Nora's real `state.db` using a separate temporary ledger. Her active-session row returned the actual `gpt-6-sol` model and populated token counts; included subscription cost returned $0.0000. ITGuy remains explicitly unavailable because Nora did not access its profile.
- Automated fixture tests cover conversation versus auxiliary cost, late cost adjustment, subscription inclusion, model filters, CLI privacy, WebParser payload shape, basic native control wiring, and idempotent sampling.

## ITGuy-owned deployment, not yet performed

ITGuy must independently review the script and run it with *read-only* access to both profile databases. It must choose a durable ledger location, expose only the `--display` JSON over an HTTP endpoint bound to an AB-approved Tailscale interface, never `0.0.0.0`, and retain no endpoint logs containing financial figures. Run a server-side sampler every 90 seconds so the ledger remains covered even when the Rainmeter skin is closed; also collect on GET for a fresh response. Serialize all collector calls through a single writer or lock. Closing the Rainmeter skin unloads the Windows widget but does not stop this separate server-side accounting process. ITGuy should verify that its own conversation rows reconcile to its `sessions` summary without adding the two sources together. Do not expose the raw CLI JSON publicly.

## AB-owned Windows step, not yet performed

Install Rainmeter and Tailscale. The skin uses Windows-provided Tahoma and Consolas; no external font download is required. Place `HUM.ini` in a Rainmeter skin folder, replace `[Variables] Endpoint` with the ITGuy-approved private URL, and load the skin. Check that the two control buttons are clickable, that closing unloads HUM rather than hiding it, that a killed endpoint leaves the last values visible with `STALE`, and that successive requests arrive 90 seconds apart. These are Windows acceptance tests and cannot be asserted from Linux static checks.

## Still open

The endpoint serving process, ITGuy-source reconciliation, Rainmeter pixel fidelity, Windows runtime clicks, and sustained refresh/outage tests are not complete. Current prices are estimates unless Hermes records actual costs. Subscription-included calls show zero incremental USD; that is not a statement about the price of AB's subscription. The first daily and 24-hour totals are intentionally partial rather than fabricated from cumulative historical rows.
