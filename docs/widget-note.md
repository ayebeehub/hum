# HUM — the widget at a glance

![HUM on the desktop](screenshot-2026-10-05.jpg)

HUM is a small, always-on desktop panel for Windows (a Rainmeter skin) that shows what
the VPS's two Hermes agents are burning. It sits at the right edge of the desktop, behind
normal windows, and refreshes itself every 90 seconds.

## What it shows

| Block | Meaning |
|---|---|
| **TODAY · SINCE 06:00 IST · GROSS** | One combined figure — Nora's and ITGuy's gross token spend since 06:00 IST |
| **THIS SESSION** | Two rows: Nora's most recent conversation, and ITGuy's currently active one |
| **LAST 24 HOURS** | The same two rows, rolled up over a moving 24-hour window |
| Footer | `UPDATED <hh:mm> IST · 90s · PARTIAL` — when it last refreshed |

Each agent row carries, left to right: the agent name, the model in use, an `in`/`out`
token pair (plus `cache` where present), and a dollar figure. The session rows also show
`ACTIVE` or `LAST SESSION` so you can tell which agent is currently working.

## Controls

- `−` collapses the panel to its header; `+` restores it
- `×` **unloads** the skin — it does not quit Rainmeter

## Behaviour worth knowing

- **It polls, it does not push.** One private request every 90 seconds.
- **Failure is visible, not silent.** If a fetch fails the panel keeps its last figures
  and swaps the footer for `STALE · <last successful update>`.
- **Some windows start partial by design.** `TODAY` and `LAST 24 HOURS` read `PARTIAL`
  until their collection windows are fully covered — the first sample is a baseline, never
  a reconstruction of history. HUM only ever reports what it has actually observed.
- **Nothing is fabricated.** A missing or unreadable Nora feed renders her rows as empty
  rather than guessing a total.

## How it fits together

Nora publishes her own pre-aggregated figures; ITGuy's server merges those with its own
usage and serves a fixed ten-line payload over a private Tailscale endpoint, which the
skin reads. No public hostname, no credential in the skin, and neither agent ever reads
the other's conversation database. See `../docs/README-runbook.md` for the full topology.
