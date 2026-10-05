# HUM — Hermes Usage Meter: Product Requirements Document

|Field|Value|
|-|-|
|Product|HUM — Hermes Usage Meter|
|Version|1.0|
|Status|Draft, for sign-off|
|Owner|AB|
|Author|Nora|
|Date|2026-10-02|
|Developer|ChatGPT SOL per/count through Nora|

\---

## 1\. Background and context

AB runs two agents through Hermes on a remote Linux server, both routed to OpenRouter: Nora (`deepseek/deepseek-v4-pro-0813`) and ITGuy (`deepseek/deepseek-v4.1-flash`, with observed drift to `deepseek-v4-pro` and `deepseek-v4-flash`). Hermes persists per-session, per-model token counts and per-session USD cost into per-profile SQLite `state.db` files. The goal is to surface that data on AB's Windows desktop as a continuous, minimal, always-on widget.

## 2\. Product scope

**In scope:** the data emitter, the private transport, and the Windows desktop widget.

**Out of scope:** mobile, web dashboard, multi-user, billing/invoicing, any write access to the source databases, and any exposure of chat content or protected data.

## 3\. Definitions

* **Agent:** Nora or ITGuy, each with its own `state.db`.
* **Today:** the calendar day bounded by 06:00 Asia/Kolkata; "today" runs from the most recent 06:00 to now.
* **This session:** the live conversation session with the most recent activity (`last\_activity\_at`), falling back to the last session labeled "last session" when idle.
* **Last 24 hours:** a rolling trailing 24-hour window.
* **Live conversation only:** activity attributable to interactive conversation, excluding auxiliary tasks (`title\_generation`, `vision`, `approval`, `background\_review`) and non-conversational workload (cron jobs and the benchmark sandbox, identifiable by model `tencent/\*` and `glm-\*`).
* **Gross total:** all activity regardless of task or model, both agents summed.

## 4\. Functional requirements

|ID|Requirement|
|-|-|
|FR-1|The widget refreshes its data every 90 seconds (±5s).|
|FR-2|The widget always renders three sections in fixed order: Today, This session, Last 24 hours.|
|FR-3|"This session" and "Last 24 hours" each show two rows: Nora and ITGuy.|
|FR-4|The Today section shows the gross total (tokens in, tokens out, total cost) summed across all models and both agents, since 06:00 IST.|
|FR-5|"This session" resolves the live/open conversation session by most recent activity; an idle agent shows its last session labeled "last session."|
|FR-6|"Last 24 hours" uses a rolling trailing 24-hour window.|
|FR-7|Session and 24h rows apply the live-conversation-only filter (exclude the four aux tasks and `tencent/\*` and `glm-\*` models).|
|FR-8|Each agent row shows input tokens, output tokens, and cache-read tokens (cache-read as a secondary figure).|
|FR-9|Cost uses `actual\_cost\_usd`, falling back to `estimated\_cost\_usd`; displayed in USD to 4 decimal places.|
|FR-10|Token figures are abbreviated (k/M), e.g. `1.07M`, `180k`.|
|FR-11|Each row labels the model actually used, truncated to a short form (e.g. `deepseek-v4-pro-0813`).|
|FR-12|The widget uses the Abass palette (bottle green `#0E4D3A`, bottle deep `#0A3A2C`, gold `#C9A227`, off-white `#F7F5F0`, ink `#1A1A1A`) with Windows-provided Tahoma for labels and Consolas for figures, in a dark, flat, rounded, minimal card. No external font install.|
|FR-13|The widget sits on the Windows desktop layer, always present and never overlapping application windows.|
|FR-14|The widget occupies no more than 15% of screen area.|
|FR-15|On backend unreachability the widget shows last-known values with a dimmed staleness timestamp; it never blanks, throws, or requires restart.|
|FR-16|All data is read from `profiles/nora/state.db` and `profiles/itguy/state.db`, read-only, tables `session\_model\_usage` and `sessions`.|

## 5\. Non-functional requirements

|ID|Requirement|
|-|-|
|NFR-1|Reliability: the widget has no crash state and is resilient to backend outage; target 99% uptime while the host is on.|
|NFR-2|Performance: consumes under 2% CPU and under 50 MB RAM; operates in the background.|
|NFR-3|Security: read-only database access; the endpoint is bound to the private tailnet only, never public; no credentials live on the widget; only token counts and cost are transmitted, never message content or protected data.|
|NFR-4|Maintainability: the skin is text-only with no compiled dependencies; changes are edit-and-reload.|

## 6\. Data requirements

Source tables and fields (both profiles):

`session\_model\_usage` — `session\_id`, `model`, `task`, `input\_tokens`, `output\_tokens`, `cache\_read\_tokens`, `cache\_write\_tokens`, `reasoning\_tokens`, `api\_call\_count`, `first\_seen`, `last\_seen`.

`sessions` — `id`, `chat\_id`, `model`, `started\_at`, `ended\_at`, `input\_tokens`, `output\_tokens`, `cache\_read\_tokens`, `cache\_write\_tokens`, `reasoning\_tokens`, `actual\_cost\_usd`, `estimated\_cost\_usd`, `cost\_status`, `cost\_source`, `last\_activity\_at`, `title`.

Rules:

* Token counts are sourced from `session\_model\_usage`, filtered by task for live-conversation rows and summed.
* Cost is sourced from `sessions` (`actual\_cost\_usd`, then `estimated\_cost\_usd`).
* The Today gross row drops all filters and sums every task and model for both agents since the 06:00 IST boundary.
* Known data nuance: `session\_model\_usage.actual\_cost\_usd` is currently unpopulated (0.0); cost lives in `sessions`. Cost attribution is reconciled during build.

## 7\. Security and privacy

* Read-only SQLite access; no writes to `state.db`.
* The JSON endpoint carries only aggregated token counts and cost figures. No message text, no session titles beyond the model label, no protected data.
* Transport is confined to a private Tailscale network; the endpoint is not exposed to the public internet.
* No API keys, passwords, or credentials are required on, or shipped to, the desktop widget.

## 8\. Deliverables and ownership

|Artifact|Owner|
|-|-|
|`usage.py` (SQLite → JSON emitter) and its queries|Nora|
|Rainmeter skin (`.ini`, shapes, Windows-provided fonts)|Nora|
|Tailscale install on the server, and placement/running of the endpoint|ITGuy|
|Tailscale and Rainmeter install on Windows|AB|
|Handoff note (exact install and deploy steps)|Nora|

## 9\. Milestones

1. **Interface design** — pixel-accurate widget mockup (HTML/CSS → PNG) in the Abass palette, reviewed and signed off by AB before any skin wiring.
2. **Data pipeline** — `usage.py` implemented and verified against live `state.db` rows for all three sections.
3. **Rainmeter skin** — the approved mockup becomes a native skin with WebParser on a 90-second timer and stale-detection fallback.
4. **Handoff and verify** — Tailscale and endpoint deploy note for ITGuy, AB's Windows install steps, and a server-side smoke check that the endpoint returns correct JSON.

## 10\. Acceptance criteria

* The three sections render with correct, reconciled numbers for Nora and ITGuy.
* Refresh holds at 90 seconds consistently over a sustained run.
* The widget remains on the desktop layer under application windows and stays under 15% of screen.
* An intentionally killed backend produces last-known values plus a staleness stamp, not a blank or crash.
* The Abass palette and Windows-provided fonts are visually correct at native resolution.
* Deployment and maintenance can be completed with the provided handoff alone.

## 11\. Risks and mitigations

|Risk|Mitigation|
|-|-|
|Backend or tailnet outage blanks the widget|FR-15 last-known + timestamp fallback|
|Cost attribution gap (session-level vs model-level cost)|Reconcile during Stage 2 against real rows; document the source|
|Model drift (an agent's model changes over time)|Report the model actually used (FR-11), never a hard-coded label|
|Cron/benchmark pollutes the count|FR-7 live-conversation filter, verified against real rows|
|Rainmeter render fidelity vs. the mockup|Design stage gates the look before skin wiring|
|Font availability|Use Windows-provided Tahoma and Consolas; no external font install or network font fetch|

## 12\. Assumptions and open questions

* Product name is HUM (Hermes Usage Meter), per AB's decision of 2026-10-02.
* "Today" day boundary at 06:00 IST is fixed; IST has no daylight saving, so no DST handling is needed.
* The Today row is gross (all tasks, all models); the session and 24h rows are live-conversation only, per decision recorded 2026-10-02.
* The endpoint choice between a live HTTP handler and a periodically refreshed static JSON file is deferred to the Stage 4 handoff with ITGuy.

