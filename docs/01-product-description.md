# HUM — Hermes Usage Meter

**Product Description**

| Field | Value |
|---|---|
| Product | HUM — Hermes Usage Meter |
| Version | 1.0 |
| Status | Draft, for sign-off |
| Owner | AB |
| Author | Nora |
| Date | 2026-10-02 |

---

## 1. Overview

HUM (Hermes Usage Meter) is a small, always-on Windows desktop widget that shows, at a glance, how many tokens AB's AI agents are consuming and what it is costing, on two timescales: the current session and the trailing 24 hours, plus a consolidated gross total for the calendar day. It is designed to answer one question instantly: "How much am I burning right now, and how much today?"

## 2. Problem

AB runs multiple AI agents — Nora (personal assistant) and ITGuy (server and infrastructure) among them — each routed through OpenRouter to its own model. Today, knowing the real spend means opening OpenRouter's dashboard or running ad-hoc queries. There is no always-visible, zero-effort surface that shows token volume and cost for the agents continuously on the desktop.

Three specifics motivate the product:

1. No at-a-glance burn figure. The number that matters most — tokens in, tokens out, and real dollar cost, right now and today — lives behind a dashboard click, not on the desktop.
2. Mixed activity, hidden in one place. Each agent's data store also contains background work (cron, vision, title generation, benchmark runs), which pollutes a naive total. The widget must distinguish live conversation from that noise, and also show the true gross figure when asked.
3. Cost attribution drift. Models change over time; the widget must report the model actually used, not a hard-coded label.

## 3. Goals

- Show token volume (input, output, cache-read) and cost in USD for Nora and ITGuy, refreshed every 90 seconds.
- Show three views in one compact card: the gross day total, the current session, and the trailing 24 hours.
- Sit on the Windows desktop layer, perpetually visible behind application windows, never covering them.
- Occupy no more than 15% of the screen.
- Match the Abass brand palette and typography.
- Degrade gracefully (last-known values with a staleness stamp) rather than blanking or crashing when the backend is unreachable.

## 4. Non-goals

- Not a full analytics dashboard: no charts, no history browser, no export.
- Not a billing or invoicing tool: cost figures are informational, from OpenRouter/Hermes accounting, not an invoice.
- Not multi-user: a single-user surface for AB.
- Not a public service: data stays private to AB's tailnet.

## 5. User and usage

Single user, AB. Usage is ambient: he glances at the widget during and between sessions. There is no interaction surface; it is a read-only display.

## 6. Experience

The widget is a single card with three sections, top to bottom:

**Today** — one line, the consolidated gross total for the current calendar day (day boundary at 06:00 IST), across all models and both agents. Shows tokens in, tokens out, and total cost. This is the "how much today" number.

**This session** — two rows, Nora and ITGuy, each showing the live conversation session with the most recent activity. If an agent is idle, the row shows the last session labeled "last session" instead of "this session."

**Last 24 hours** — two rows, Nora and ITGuy, each showing the rolling trailing-24-hour totals.

Each agent row shows, for its labeled model: input tokens, output tokens, cache-read tokens (secondary), and cost in USD.

Example layout:

```
▌ NORA · ITGuy                HUM
  TODAY
    in 1.95M · out 344k · $3.21
  THIS SESSION
    Nora   deepseek-v4-pro-0813   in 199k / out 23.5k  cache 6.1M   $0.1975
    ITGuy  deepseek-v4.1-flash    in 372k / out 63.2k  cache 5.1M   $0.5637
  LAST 24H
    Nora   deepseek-v4-pro-0813   in 1.07M / out 180k   cache …      $1.1842
    ITGuy  deepseek-v4.1-flash    in 879k / out 165k    cache …      $2.0316
```

## 7. Visual design

The card follows the Abass brand system exactly.

- Palette: bottle green `#0E4D3A` (primary and header), bottle deep `#0A3A2C`, gold `#C9A227` (accents and section labels), off-white `#F7F5F0` (text on dark), ink `#1A1A1A` (card background), and muted grays for secondary figures.
- Typography: Tahoma for labels and headings; Consolas for numeric figures. Both use Windows-provided fonts; no font download is required.
- Style: dark card, flat solid colors, thin gold dividers between sections, rounded corners. Minimal and quiet; no gradients, no shadows, no animation.
- Size: comfortably under 15% of screen area at common resolutions (roughly 300 × 380 px at 1920 px wide).

## 8. Behavior

- Refresh every 90 seconds.
- Renders on the Windows desktop layer: always present, behind normal windows, never on top of applications.
- On backend failure, holds the last-known values and appends a dimmed last-updated timestamp. It never blanks, throws, or requires a restart.

## 9. Architecture at a glance

1. **Data source.** Hermes already records per-session, per-model token usage and per-session cost in two local SQLite files: `profiles/nora/state.db` and `profiles/itguy/state.db`. No external API or key is required; reads are read-only.
2. **Emitter.** A small Python script runs the three queries (today, session, last-24h) and emits a single JSON object.
3. **Transport.** The JSON is served over a private Tailscale network. The widget endpoint is bound to the tailnet only, never the public internet.
4. **Widget.** A native Rainmeter skin fetches the JSON every 90 seconds via the first-party WebParser measure and renders the card with Shape and String meters. No browser runtime, no third-party plugin, no compiled binary.

## 10. Success criteria

- 99% uptime: no crash states, resilient to backend outage.
- 90-second refresh holds reliably.
- Always-on desktop presence, never over other apps, under 15% of screen.
- Numbers match a manual OpenRouter/Hermes reconciliation to within the accounting precision.
- Deployable in minutes (install Tailscale and Rainmeter, drop one skin folder) and maintainable by editing text.