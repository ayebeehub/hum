# HUM — Hermes Usage Meter (server-side runbook)

> Last verified: 2026-10-17. Agent skill: `hum-widget-ops` (load before touching HUM).
> Product docs in this folder are Nora's handover package, kept here because the
> Hermes container's cache directory is pruned after 24h idle.

## What it is

An always-on Windows Rainmeter widget showing AB's per-agent token/cost burn for
Nora and ITGuy. Nora authors the product docs and publishes her own figures;
ITGuy owns the server-side endpoint; AB owns the Windows side.

## Live topology (verified 2026-10-17)

| Piece | Value |
|---|---|
| Endpoint | `http://100.88.230.67:8765/hum` — GET only, exactly 10 display strings |
| Transport | Tailscale tailnet `tail00e078.ts.net` |
| Tailnet node | `ayebee-vps1` = 100.88.230.67 (VPS); `ayebeecentral` = 100.83.232.53 (AB's Windows) |
| App code | `/opt/hum/` — root:root 0755 (**never** under `/opt/itguy`) |
| Durable payload | `/opt/itguy/hum-payload/` — root:root, read only by root at install time |
| Ledger | `/var/lib/hum/hum-ledger.sqlite` — dir 0700, uid 10000 |
| Service account | `hum` (uid/gid 10000, nologin, no home) — created by `exec_hum_install.sh` |
| Units | `hum.service`, `hum-sampler.service`, `hum-sampler.timer` (90 s) |
| Staging | `/opt/itguy/temp/hum_stage/` |
| Vetted scripts | `exec_hum_install.sh`; `exec_hum_control.sh` — start / stop / restart / status / **park [minutes]** / **resume**; `obs_hum.sh` (+ `LEDGER COVERAGE` section) |

## Data boundary — hard contract (doc 05)

- ITGuy reads **its own** `/opt/data/profiles/itguy/state.db` (read-only) and Nora's
  pre-aggregated export `/opt/data/hum-export/nora-usage.json` (her cron job
  "HUM Nora Publisher", every 1 minute, `no_agent`).
- **Never open `/opt/data/profiles/nora/state.db`** (or `-wal`/`-shm`), not even to verify.
- This is **data minimisation, not OS isolation** — every profile shares uid 10000, so
  filesystem modes are not the guardrail; code review is.
- Nora's export carries a `reconciliation` verdict: **carry it through, never recompute it.**

## Operations

```bash
# status + payload + ledger + coverage + export freshness (read-only)
sudo -n /opt/itguy/vetted/obs_hum.sh

# start | stop | restart | status  — control hum.service alone
sudo -n /opt/itguy/vetted/exec_hum_control.sh stop
sudo -n /opt/itguy/vetted/exec_hum_control.sh start

# park | resume  — the ONLY way to hold the endpoint down (see pitfall 6)
sudo -n /opt/itguy/vetted/exec_hum_control.sh park 2   # self-limiting: auto-resume in 2 min
sudo -n /opt/itguy/vetted/exec_hum_control.sh park     # INDEFINITE: stays down until resume
sudo -n /opt/itguy/vetted/exec_hum_control.sh resume

# reinstall / repair (idempotent — recreates the uid-10000 account if absent)
sudo bash /opt/itguy/temp/hum_stage/install_stage1.sh
```

Rollback: `exec_hum_control.sh park` disables **and** stops both units (`hum-sampler.service`
carries `Requires=hum.service`, so stopping the service alone is undone within 90 s), then remove
the three unit files, `daemon-reload`, and
`rm -rf /opt/hum /opt/itguy/hum-payload /var/lib/hum` and `userdel hum && groupdel hum`.

## Pitfalls hit in real deployment

1. **`User=10000` fails on a host with no passwd entry** — `status=217/USER`,
   `Failed to determine user credentials: No such process`. The account must be created
   **on uid/gid 10000** (`groupadd -g 10000 hum; useradd -u 10000 -g 10000 -M -d /nonexistent
   -s /usr/sbin/nologin hum`). Never run as root: only uid 10000 can read the 0600 inputs.
2. **App code must not live under `/opt/itguy`** — that dir is `drwxr-x--- itguy:itguy`
   (0750), so uid 10000 cannot traverse it: `status=200/CHDIR`, `Changing to the requested
   working directory failed: Permission denied`. Code lives at `/opt/hum`. Do **not**
   "fix" this by loosening `/opt/itguy` — it holds knowledge/audit/deployment files with
   live credentials.
3. **`217/USER` masks later failures.** The unit never reaches chdir while the account is
   missing, so expect a second, different error after fixing user resolution.
4. **Today/24h legitimately read `warming up`** on a fresh ledger — the first sample is a
   baseline, never a historical reconstruction. Missing Nora export ⇒ null windows (never a
   partial sum); merely stale (>300 s) ⇒ may be summed but must force `PARTIAL`.
5. **A redeploy from the original package reintroduces the placeholder endpoint.** Nora's
   `HUM.ini` ships `Endpoint=http://127.0.0.1:8765/hum`, which on AB's laptop means the laptop
   itself — every fetch fails and the card sits at its baked-in defaults
   ("Waiting for usage data" in every field, including the static agent-name headers).
   **Always hand AB the configured `HUM.ini` from this folder**, never the package copy.
   Diagnosis: all fields at defaults (not just the numbers) = fetch never succeeded; check the
   `[Variables] Endpoint` line before touching the server. Changing the variable requires a
   skin **reload** (Manage → Load), not just Refresh all.
6. **`stop` cannot hold the endpoint down — use `park`.** `hum-sampler.timer` requires
   `hum-sampler.service`, which requires `hum.service`, so the timer's 90 s elapse pulls the
   endpoint back up: a measured `stop` was undone after **53 s** with `NRestarts=0` (a fresh
   start job, not `Restart=always`). `park` disables both units and **asserts** they went
   inactive and that nothing still listens on 8765; `park <minutes>` (1..1440) additionally arms
   a transient auto-resume.
7. **The STALE footer embeds the whole previous footer line — by design.** `OnConnectErrorAction`
   sets the footer to `STALE · #LastUpdated#`, and `#LastUpdated#` holds `MeasureField10` — the
   *entire* tenth display string (`UPDATED 20:59 IST · 90s · PARTIAL`), not a bare clock time. Do
   not read the embedded `UPDATED` as evidence the fetch succeeded; the tell is that its time is
   stale. Note the `UPDATED` time advances every **~60 s** (Nora's 1-minute publisher drives
   `updated_at = max(...)`), while `90s` is the skin's *poll interval* — different quantities.
8. **`PARTIAL` is a coverage OR data-completeness flag — and it used to be stuck on.** Before
   2026-10-18 the footer read `PARTIAL` permanently, and `LEDGER COVERAGE` could not explain it.
   Cause: `usage.collect()` was called with ITGuy only (by contract), but computed
   `missing = [n for n in ('Nora','ITGuy') if n not in data]`, so Nora was *always* "missing" and
   `today.partial` was structurally `True`; `hum_merge` OR'd it into the footer. Fixed in
   `usage.py` (`missing` now counts only agents that were requested and did not come back) —
   verified 2026-10-18: footer `EST. USD` with clean data, `PARTIAL` still set for a stale export,
   a missing export, or a real gap > 180 s.
   The genuinely valid triggers are: the ledger is younger than the window, a **gap > 180 s** falls
   inside it (any outage longer than that — a host reboot above all — re-arms it for up to 24 h),
   Nora's export is stale (> 300 s) or unusable, or her `today.partial` is set. `LEDGER COVERAGE`
   reports the ITGuy half directly and lists ITGuy only (by contract); Nora's half comes from her
   export, so never go looking for her ledger to explain it.

9. **`exec_hum_install.sh` does NOT restart an already-running `hum.service`.** It installs the
   code and then runs `systemctl enable --now hum.service`, which is a no-op for a running unit —
   the old module stays loaded in memory and the deployed change has no visible effect (caught
   2026-10-18: file hash matched while the footer still showed the old behaviour and the PID was
   unchanged). After any code install, follow with
   `sudo -n /opt/itguy/vetted/exec_hum_control.sh restart` and confirm the **PID changed** before
   believing the new code is live.

## Acceptance status — ALL CLOSED (2026-10-17)

Server side: **live and verified** (10-string payload, tailnet-only listener, state
`application/json; charset=utf-8` + `Cache-Control: no-store`).
AB's Windows side: Rainmeter + skin loaded and rendering correctly.

- **STALE acceptance test: PASSED 2026-10-17.** `park 3` held the endpoint down **185 s**
  (journal 15:30:27Z Stopped → 15:33:32Z Started, against 53 s for the failed `stop` attempt).
  AB's screenshot showed the panel keeping its last figures with the footer reading
  `STALE · UPDATED 20:59 IST · 90s · PARTIAL` — 20:59 being the last successful fetch before the
  21:00:26 IST park. The self-limiting auto-resume fired **unattended** and recovery was verified
  independently.
- **Sustained refresh soak: PASSED 2026-10-17.** 44 polls at 30 s intervals over 22.6 min:
  44/44 HTTP 200, 44/44 exactly 10 display strings, `UPDATED` monotonic with no stall, no new
  ledger gap across the window, no service restarts.
- **Open by choice, not by defect:** the `park <minutes>` auto-resume is a *transient* unit, so a
  reboot during a self-limiting park clears the guard and leaves the endpoint parked.

## 2026-10-18 — stuck `PARTIAL` footer fixed; Nora's reconciliation fixed

- **Footer `PARTIAL` was a collector bug, now fixed.** `usage.collect()` was fed ITGuy only (by
  contract) but computed `missing` against a hardcoded `('Nora','ITGuy')` tuple, so Nora always
  counted as missing and `today.partial` was structurally `True`; `hum_merge` OR'd it into the
  footer. The footer therefore read `PARTIAL` permanently, independent of coverage — which is why the
  186 s park gap "clearing" on 2026-10-18 changed nothing. Fixed line:
  `missing = [n for n in ('Nora','ITGuy') if n in sources and n not in data]`
  (`/opt/hum/usage.py`, sha256 `da45f312…`; pre-fix copy
  `/opt/itguy/hum-payload/usage.py.pre-partialfix-20261018`, sha256 `cee08df7…`).
  Verified: clean data ⇒ `EST. USD`; stale export, absent export and a real 400 s gap each still
  ⇒ `PARTIAL`. Live footer confirmed `EST. USD` across successive 90 s refreshes.
- **Nora's `reconciliation` now reports `ok`, `difference_usd: 0.0`** (fixed on her side; carried
  through untouched here, never recomputed). New schema: `status`, `total_cost_usd`,
  `session_summary_cost_usd`, `auxiliary_cost_usd`, `difference_usd`, `checked_at`. The old
  `model_usage_total_cost_usd` / `model_usage_conversation_cost_usd` fields are removed and nothing
  on this side referenced them (`hum_merge` passes the dict through; `obs_hum.sh` reads `.status`).
  Her root cause: the old check compared total `session_model_usage` cost (which includes
  auxiliary-task rows — background_review, title_generation, approval, vision, image_generation,
  compression — that never roll into a session summary) against the sessions-summary cost. Her
  numbers now reconcile exactly: 71.94359504 + 13.68493914 = 85.62853418.
- Current service state: `hum.service` + `hum-sampler.timer` active/enabled, tailnet-only listener,
  10-string payload, ledger and export fresh, no open items.

## Files here

- `01-product-description.md`, `02-prd.md` — Nora's product definition
- `03-widget-mockup.html`, `03-widget-mockup.png` — visual design reference
- `04-handoff.md` — build status and ownership split
- `05-itguy-deployment.md` — the server-side brief (data boundary contract)
- `06-windows-install.md` — AB's Windows installation + acceptance checklist
- `HUM.ini` — the skin, with `[Variables] Endpoint` set to the live tailnet URL
