# Audit Log: HUM usage endpoint — activation (uid-10000 service account + app relocation)

- **Date:** 2026-10-05 10:29 IST (2026-10-05T04:59Z)
- **Requested by:** AB (Telegram) — "lets resume the HUM setup"
- **Mode:** PLAN → EXECUTE (approved: `YES CONFIRM HUM SYSUSER`, then `YES CONFIRM HUM RELOCATE`)
- **Task:** Complete Stage 1 of the HUM desktop widget: get `hum.service` actually serving
  `GET /hum` on the tailnet, after the previously-reported blocker.

## Pre-state (verified live before any change)

- `hum.service`: enabled + fail-looping (`activating`), restart counter 9602.
- `getent passwd 10000` / `getent group 10000`: **no entry** on the host.
- Nothing listening on 8765. `hum-sampler.timer` active+enabled, retrying the broken service
  every 90 s via its `Requires=hum.service`.
- Tailscale side (Stage 0) already complete: node `ayebee-vps1`, IP `100.88.230.67`.

## Step A — service account (approved `YES CONFIRM HUM SYSUSER`)

Added an idempotent service-account block to `exec_hum_install.sh`:

    groupadd -g 10000 hum
    useradd -u 10000 -g 10000 -M -d /nonexistent -s /usr/sbin/nologin hum

Result: `hum:x:10000:10000::/nonexistent:/usr/sbin/nologin`. The `217/USER` error cleared,
exposing the second (masked) problem below — the unit never got as far as chdir before.

## Step B — the real second failure: `status=200/CHDIR`

    hum.service: Changing to the requested working directory failed: Permission denied
    hum.service: Main process exited, code=exited, status=200/CHDIR

**Root cause:** `/opt/itguy` is `drwxr-x--- itguy:itguy` (0750). uid 10000 has no traverse
permission on it, so the service could not chdir into `/opt/itguy/hum`, and
`hum-sampler.service` could not have executed its script from there either. This was masked by
the earlier user-resolution failure.

**Rejected fix:** loosening `/opt/itguy` to world-traversable. That directory holds `knowledge/`,
`audit/`, `policies/` and `deployments/` files containing live credentials — a real security
regression, not a cosmetic one.

## Step B fix (approved `YES CONFIRM HUM RELOCATE`)

Relocate the app code outside `/opt/itguy`, to a root-owned, world-traversable path. Three files:

1. `payload/hum.service` — `WorkingDirectory=/opt/hum`,
   `ExecStart=/usr/bin/python3 /opt/hum/hum_server.py`
2. `payload/hum-sampler.service` — `ExecStart=/opt/hum/hum_sampler.sh`
3. `exec_hum_install.sh` — `APP=/opt/hum`, plus a new **access preflight**:
   `runuser -u hum -- test -r <code file>` and `<state.db>`, aborting with a plain-English
   message instead of letting systemd emit the cryptic `200/CHDIR`.

`/opt/itguy/hum-payload/` (root-only durable payload, read by root at install time) and the
ledger `/var/lib/hum` (0700, uid 10000) unchanged. Tailscale, containers, DNS, sites and
databases untouched.

### Staged SHA-256 (both ends verified identical)

- `exec_hum_install.sh` — `1dd113cc0d865f7818d046c15284419dc17485682f0edf72885444c77f0bd29c`
- `payload/hum.service` — `dc806476c02a6cb281c8ac19686aebf6b8159256dd96a465465e99f63cc18240`
- `payload/hum-sampler.service` — `9f620084402e17d5fb12145a2bd1c49edcacd6a90b6f67c0a724db2178741dd9`

Installer run by AB as root: `sudo bash /opt/itguy/temp/hum_stage/install_stage1.sh` ("done").

## Verification performed (independent, post-install)

`sudo -n /opt/itguy/vetted/obs_hum.sh`:

- `hum.service` **active / enabled**; `hum-sampler.timer` **active / enabled**.
- Occupied at `100.88.230.67:8765` (`python3`, pid 3328803); **no listener on any public
  interface** (PASS).
- Ledger `/var/lib/hum/hum-ledger.sqlite` owned `hum:hum`, age 11.8 s.
- Nora export fresh (54.9 s), `partial: true`, `reconciliation: mismatch` — carried through
  unmodified, as required by doc 05.
- `GET /hum` → headers `Content-Type: application/json; charset=utf-8` and
  `Cache-Control: no-store`; **display array = 10 strings**, all populated (Today, both session
  rows, both 24 h rows, footer).
- Service log shows the final transition at 04:57:52 — `hum: listening on
  http://100.88.230.67:8765/hum`. Restart counter frozen at **9646** (no restarts since), so the
  90 s sampler/timer loop is now healthy rather than a fail-loop.
- Negative reachability check from the Hermes container (a non-tailnet path): `100.88.230.67:8765`
  is **unreachable** (000) — the bind really is tailnet-only, reachable only by AB's Windows node.

Not re-verified live this round: 404 on unknown paths / 405 on POST (tested in the container
preflight before deployment; the route table is unchanged since).

## Result: SUCCESS

The endpoint serves the widget payload privately on the tailnet. Operationally NOT yet "done":
AB's Windows-side acceptance checks (Rainmeter install, `HUM.ini`, pixel fidelity, clickable
controls, unload-vs-hide, and the deliberate-stop STALE test) are his to run.

## Handover

- URL for the skin: `http://100.88.230.67:8765/hum`
- PowerShell check: `(Invoke-RestMethod 'http://100.88.230.67:8765/hum').display.Count` → `10`
- First ~90 s of Today/24 h may legitimately read `warming up`: the first sample is a baseline.

## Left deliberately

- `/opt/itguy/hum/` — dead copies from the first (failed) install. Harmless; **not deleted** —
  removal requires a separate explicit instruction (see §4a discipline).
- `/opt/itguy/hum-payload/` — still the durable payload source for reinstalls.

## Rollback

- Stop: `sudo -n /opt/itguy/vetted/exec_hum_control.sh stop` (plus
  `sudo systemctl disable --now hum-sampler.timer hum.service` to stop the 90 s retry loop).
- Full removal: disable both units, remove the three unit files, `daemon-reload`, then
  `rm -rf /opt/hum /opt/itguy/hum-payload /var/lib/hum`, and `userdel hum && groupdel hum`.

## Duration

Approx. 25 min across two approval rounds (04:35–04:59 UTC), most of it diagnosis.

---

## Addendum (2026-10-05, later same day) — Windows side accepted + bookkeeping

- AB reported Rainmeter was not yet installed; walked through the official 4.5.26 install,
  skin placement (`%USERPROFILE%\Documents\Rainmeter\Skins\HUM\HUM.ini`) and load. A
  pre-configured `HUM.ini` (only `[Variables] Endpoint` changed to the live tailnet URL) was
  delivered to skip the manual edit step.
- AB then reported the widget **loaded and rendering correctly** ("looks good"). Card content
  cross-checked against a live payload fetch at the time.
- Still outstanding: the deliberate-stop **STALE** acceptance test, and sustained 90 s refresh
  soak. The endpoint remains deliberately stopped-only-on-request.

### Bookkeeping performed

- Durable runbook + handover docs: `/opt/itguy/deployments/hum/` (README.md plus Nora's docs
  01–06, mockup, and the configured HUM.ini) — the Hermes container cache is pruned after 24 h.
- Knowledge base updated: `network.md` (tailscale0, tailnet ingress is not a UFW rule, 2026-10-05
  section), `security.md` (tailnet-exposed services, uid-10000 service account, /opt/itguy stays
  0750), `server.md` (host daemons table, dynamic gateway, /opt/data permissions),
  `containers.md` (host-daemons section — obs_docker.sh will never show these).
- `config/domains.txt` deliberately **unchanged**: HUM has no public hostname, so SSL monitoring
  correctly has no entry for it.
- Agent memory + skills (`hum-widget-ops`, `itguy-project-config`) updated.

## Addendum 2 (2026-10-05) — "waiting for usage data" after AB's redeploy

**Symptom (AB):** after a redeploy, every field showed `Waiting for usage data`.

**Diagnosis:** server verified healthy first (units active, tailnet-only listener, ledger <1 s old,
10-string payload with correct headers/compact JSON). All fields at their **baked-in defaults**,
including the static agent-name headers, means the WebParser had never completed a successful
fetch. Cause: the redeploy restored Nora's package `HUM.ini`, whose
`Endpoint=http://127.0.0.1:8765/hum` points at the laptop itself.

**Fix (AB):** set `[Variables] Endpoint` to `http://100.88.230.67:8765/hum` and reload the skin.
Confirmed working.

**Lesson recorded** in `/opt/itguy/deployments/hum/README.md` pitfall 5 and the `hum-widget-ops`
skill: always deliver the pre-configured `HUM.ini` from the deployments folder, never the package
copy; an all-defaults card is a skin-side endpoint problem, not a server outage; and a variable
change needs a skin reload, not just Refresh all.

**Still outstanding:** the deliberate-stop STALE acceptance test and a sustained 90 s refresh soak.
