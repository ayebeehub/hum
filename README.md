# HUM — Hermes Usage Meter

![HUM on the desktop](docs/screenshot-2026-10-05.jpg)

An always-on Windows desktop panel (Rainmeter skin) showing what this VPS's two Hermes
agents — **Nora** and **ITGuy** — are spending, plus the private server-side endpoint that
feeds it. A short visual walkthrough is in [`docs/widget-note.md`](docs/widget-note.md).

- **Today** — combined gross token spend for both agents since 06:00 IST
- **This session** — the current/last conversation per agent, with model, tokens, cache and cost
- **Last 24 hours** — the same two rows over a rolling 24 h window
- Polls a private endpoint every 90 s; on failure it keeps the last values and shows `STALE`

## Layout

| Path | Contents |
|---|---|
| `server/` | The endpoint: `hum_server.py` (HTTP), `hum_merge.py` (merge), `usage.py` (collector), `hum_sampler.sh` |
| `server/systemd/` | The three units: `hum.service`, `hum-sampler.service`, `hum-sampler.timer` |
| `windows/` | `HUM.ini` — the skin, with its endpoint already configured |
| `docs/` | The handover package: product description, PRD, mockup, handoff, deployment brief, Windows install guide, screenshot |
| `ops/` | The vetted install/control/observe scripts used on the host |
| `nora/` | Nora's publisher and the package's tests |
| `history/` | Deployment audit for the initial build |

## How it works

Three moving parts, one direction of data:

1. **Nora's publisher** (her own scheduled job) writes a pre-aggregated export of her
   figures to a known file, roughly every minute and at zero model cost.
2. **The ITGuy endpoint** reads *its own* usage database and that export, merges them under
   a single lock, and serves exactly one route — `GET /hum` — returning a fixed ten-string
   display array.
3. **The skin** reads it every 90 seconds and renders it.

Notable properties, all deliberate:

- The endpoint binds to the **private Tailscale address only** — never `0.0.0.0` — and the
  tailnet policy restricts the port to the owner's own device. There is no public hostname.
- It is the **single writer** to its ledger, so concurrent requests cannot double-count.
- The service runs as an **unprivileged numeric uid** with a read-only filesystem apart from
  its state directory, and no capabilities.
- **No credentials exist in the skin.** Nothing sensitive is stored on the Windows side.
- Response bodies and query results are never logged — these are financial figures.

## Data boundary

ITGuy reads only its **own** usage database (read-only) plus Nora's published export. Neither
agent reads the other's conversation database, and nothing in this repository contains
credential material, message content, session titles or chat identifiers.

## Server-side operations

```bash
sudo -n /opt/itguy/vetted/obs_hum.sh                      # status, payload, ledger, feed freshness
sudo -n /opt/itguy/vetted/exec_hum_control.sh stop|start  # also the STALE acceptance test
sudo bash /opt/itguy/temp/hum_stage/install_stage1.sh     # idempotent install/repair
```

Setup notes, rollback and the failures actually hit during deployment are in
[`docs/README-runbook.md`](docs/README-runbook.md).

## Windows setup

Rainmeter 4.5.26 or later, then place `windows/HUM.ini` in
`%USERPROFILE%\Documents\Rainmeter\Skins\HUM\` and load it (Manage → Load). Set
`Position` to **On Desktop** and leave **Click through** unchecked so the header controls
stay clickable. Full checklist: [`docs/06-windows-install.md`](docs/06-windows-install.md).

## Status

Live since 2026-10-05: endpoint serving, skin rendering on the owner's desktop. Outstanding:
the deliberate-stop `STALE` acceptance test and a sustained refresh soak. See
`history/2026-10-05-deployment-audit.md`.
