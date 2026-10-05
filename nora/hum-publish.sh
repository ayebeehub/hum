#!/usr/bin/env bash
# Nora's HUM publisher sampler: refresh the minimal usage export for ITGuy.
# Runs every 60s via cron (no_agent). Silent on success; errors go to stderr.
set -uo pipefail
exec python3 /opt/data/profiles/nora/home/hum/nora_publish.py \
  --nora-db /opt/data/profiles/nora/state.db \
  --ledger /opt/data/profiles/nora/home/hum/ledger/nora-hum-ledger.sqlite \
  --out /opt/data/hum-export/nora-usage.json >/dev/null