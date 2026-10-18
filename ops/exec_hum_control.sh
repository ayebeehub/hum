#!/usr/bin/env bash
# ITGuy vetted EXECUTE script: start / stop / restart / park / resume the HUM endpoint.
#
#   start | stop | restart | status   control hum.service alone.
#       NOTE: `stop` cannot hold the endpoint down. hum-sampler.service carries
#       Requires=hum.service, so the 90s sampler timer resurrects the service
#       within one interval (measured 53s on 2026-10-17). Use `park` to hold it down.
#
#   park              disable+stop hum-sampler.timer AND hum.service so nothing can
#                     restart the endpoint. INDEFINITE: stays down (and survives a
#                     reboot) until `resume`. This is the deliberate-outage STALE
#                     acceptance test.
#   park <minutes>    SELF-LIMITING form: the same park, plus a one-shot transient
#                     timer that runs `resume` automatically after <minutes> (1..1440),
#                     so a forgotten park cannot leave the endpoint dark. If the timer
#                     cannot be armed the script un-parks and exits non-zero rather
#                     than leaving the endpoint down with no way back.
#                     CAVEAT: the timer is a transient unit (it lives in
#                     /run/systemd/transient), so a REBOOT clears the guard and leaves
#                     the endpoint parked. Fine for a minutes-long test window; do not
#                     rely on it for a long unattended park.
#   resume            re-enable+start both, cancel any pending auto-resume, then verify
#                     the endpoint is actually serving.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root" >&2
  exit 1
fi

SELF="$(readlink -f "$0")"
ACTION="${1:-}"
MINUTES="${2:-}"

if ! printf '%s' "$ACTION" | grep -Eq '^(start|stop|restart|status|park|resume)$'; then
  echo "usage: $0 {start|stop|restart|status|park [minutes]|resume}" >&2
  exit 2
fi

if [ -n "$MINUTES" ]; then
  if [ "$ACTION" != "park" ]; then
    echo "ERROR: a minutes argument is only valid for 'park'" >&2
    echo "usage: $0 {start|stop|restart|status|park [minutes]|resume}" >&2
    exit 2
  fi
  if ! printf '%s' "$MINUTES" | grep -Eq '^[0-9]+$' || [ "$MINUTES" -lt 1 ] || [ "$MINUTES" -gt 1440 ]; then
    echo "ERROR: park minutes must be a whole number between 1 and 1440" >&2
    exit 2
  fi
fi

unit_state() { printf '%s / %s' "$(systemctl is-active "$1")" "$(systemctl is-enabled "$1" 2>/dev/null || true)"; }

# Confirm the endpoint answers on the tailnet IP (never a public interface).
endpoint_serving() {
  local ip http
  ip="$(tailscale ip -4 2>/dev/null | sed -n '1p')"
  if [ -z "$ip" ]; then
    echo "ERROR: no tailnet IPv4 available (tailscaled down or not logged in)" >&2
    return 1
  fi
  http="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://${ip}:8765/hum" || true)"
  if [ "$http" != "200" ]; then
    echo "ERROR: endpoint did not answer (HTTP ${http})" >&2
    return 1
  fi
  return 0
}

case "$ACTION" in
  start)   systemctl start hum.service ;;
  stop)    systemctl stop hum.service ;;
  restart) systemctl restart hum.service ;;
  status)  systemctl status hum.service --no-pager || true
           systemctl list-timers hum-autoresume.timer --no-pager 2>/dev/null || true ;;

  park)
    systemctl disable --now hum-sampler.timer hum.service
    echo "hum-sampler.timer: $(unit_state hum-sampler.timer)"
    echo "hum.service      : $(unit_state hum.service)"
    if [ "$(systemctl is-active hum-sampler.timer)" != "inactive" ] \
       || [ "$(systemctl is-active hum.service)" != "inactive" ]; then
      echo "ERROR: park did not take - a unit is still active" >&2
      exit 1
    fi
    LISTENING="$(ss -lnt 2>/dev/null | grep ':8765' || true)"
    if [ -n "$LISTENING" ]; then
      echo "ERROR: something is still listening on 8765: ${LISTENING}" >&2
      exit 1
    fi
    echo "PARKED: endpoint down, timer disabled - nothing can restart it."

    if [ -z "$MINUTES" ]; then
      echo "INDEFINITE: no auto-resume armed. Run '$0 resume' when the test is over."
    else
      systemctl stop hum-autoresume.timer 2>/dev/null || true
      systemctl reset-failed hum-autoresume.service 2>/dev/null || true
      systemd-run --collect --on-active="${MINUTES}min" --unit=hum-autoresume \
        --description="HUM self-limiting park: auto-resume the usage endpoint" \
        "$SELF" resume || true
      if [ "$(systemctl is-active hum-autoresume.timer)" != "active" ]; then
        echo "ERROR: could not arm the auto-resume timer - un-parking rather than" >&2
        echo "       leaving the endpoint down with no way back." >&2
        systemctl enable --now hum-sampler.timer hum.service
        exit 1
      fi
      echo "SELF-LIMITING: auto-resume armed for $(date -u -d "+${MINUTES} minutes" '+%H:%M:%SZ') (in ${MINUTES} min)."
      echo "NOTE: a reboot clears this transient guard and leaves the endpoint parked."
    fi
    ;;

  resume)
    systemctl stop hum-autoresume.timer 2>/dev/null || true
    systemctl enable --now hum-sampler.timer hum.service
    sleep 5
    endpoint_serving || { echo "ERROR: units are up but the endpoint is not serving" >&2; exit 1; }
    echo "RESUMED: hum-sampler.timer $(unit_state hum-sampler.timer), hum.service $(unit_state hum.service), endpoint serving."
    ;;
esac

echo "hum.service: $(systemctl is-active hum.service)"
