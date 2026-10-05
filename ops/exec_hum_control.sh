#!/usr/bin/env bash
# ITGuy vetted EXECUTE script: start / stop / restart the HUM endpoint.
# Used for the deliberate-outage STALE test and for rollback.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root" >&2
  exit 1
fi

ACTION="${1:-}"
if ! printf '%s' "$ACTION" | grep -Eq '^(start|stop|restart|status)$'; then
  echo "usage: $0 {start|stop|restart|status}" >&2
  exit 2
fi

case "$ACTION" in
  start)   systemctl start hum.service ;;
  stop)    systemctl stop hum.service ;;
  restart) systemctl restart hum.service ;;
  status)  systemctl status hum.service --no-pager || true ;;
esac

echo "hum.service: $(systemctl is-active hum.service)"
