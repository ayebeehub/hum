# HUM — Hermes Usage Meter (server-side runbook)

> Last verified: 2026-10-05. Agent skill: `hum-widget-ops` (load before touching HUM).
> Product docs in this folder are Nora's handover package, kept here because the
> Hermes container's cache directory is pruned after 24h idle.

## What it is

An always-on Windows Rainmeter widget showing AB's per-agent token/cost burn for
Nora and ITGuy. Nora authors the product docs and publishes her own figures;
ITGuy owns the server-side endpoint; AB owns the Windows side.

## Live topology (verified 2026-10-05)

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
| Vetted scripts | `exec_hum_install.sh`, `exec_hum_control.sh`, `obs_hum.sh` |

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
# status + payload + ledger + export freshness (read-only)
sudo -n /opt/itguy/vetted/obs_hum.sh

# start | stop | restart | status
sudo -n /opt/itguy/vetted/exec_hum_control.sh stop     # also the STALE acceptance test
sudo -n /opt/itguy/vetted/exec_hum_control.sh start

# reinstall / repair (idempotent — recreates the uid-10000 account if absent)
sudo bash /opt/itguy/temp/hum_stage/install_stage1.sh
```

Rollback: disable `hum-sampler.timer` **and** `hum.service` (the timer carries
`Requires=hum.service`, so stopping the service alone is undone within 90 s), remove the
three unit files, `daemon-reload`, then `rm -rf /opt/hum /opt/itguy/hum-payload /var/lib/hum`
and `userdel hum && groupdel hum`.

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

## Acceptance status

Server side: **live and verified** (10-string payload, tailnet-only listener, state
`application/json; charset=utf-8` + `Cache-Control: no-store`).
AB's Windows side: Rainmeter + skin loaded and rendering correctly as of 2026-10-05;
the deliberate-stop **STALE test** is still outstanding.

## Files here

- `01-product-description.md`, `02-prd.md` — Nora's product definition
- `03-widget-mockup.html`, `03-widget-mockup.png` — visual design reference
- `04-handoff.md` — build status and ownership split
- `05-itguy-deployment.md` — the server-side brief (data boundary contract)
- `06-windows-install.md` — AB's Windows installation + acceptance checklist
- `HUM.ini` — the skin, with `[Variables] Endpoint` set to the live tailnet URL
