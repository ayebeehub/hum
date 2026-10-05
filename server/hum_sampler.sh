#!/usr/bin/env bash
# HUM server-side sampler.
#
# Keeps ITGuy's ledger covered while the Windows skin is closed: one GET of the
# local private endpoint, which is the single ledger writer. Silent on success;
# errors go to stderr so the systemd journal records the failure (never a body).
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

IP="$(tailscale ip -4 2>/dev/null | head -1)"
if [ -z "${IP:-}" ]; then
  echo "hum-sampler: no tailnet IPv4 available" >&2
  exit 1
fi

exec curl -fsS -o /dev/null --max-time 30 "http://${IP}:8765/hum"