#!/usr/bin/env bash
# ITGuy vetted EXECUTE script: install / refresh the HUM service on the VPS host.
#
# Installs the code, the ledger directory, and the systemd units, starts the
# service and timer, then verifies the endpoint end to end. Idempotent.
#
# Payload source: /opt/itguy/temp/hum_stage/payload  (usage.py, hum_merge.py,
# hum_server.py, hum_sampler.sh, hum.service, hum-sampler.service, hum-sampler.timer)
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root" >&2
  exit 1
fi

PAYLOAD="${HUM_PAYLOAD:-/opt/itguy/hum-payload}"
# App code must live OUTSIDE /opt/itguy: that directory is mode 0750 itguy:itguy, so
# uid 10000 cannot traverse it and systemd fails to chdir (status=200/CHDIR).
APP=/opt/hum
LEDGER_DIR=/var/lib/hum
SERVICE_USER=10000          # the uid that owns /opt/data (profile data)
SERVICE_GROUP=10000
PORT=8765

for f in usage.py hum_merge.py hum_server.py hum_sampler.sh \
         hum.service hum-sampler.service hum-sampler.timer; do
  [ -f "$PAYLOAD/$f" ] || { echo "ERROR: missing payload file $PAYLOAD/$f" >&2; exit 1; }
done

# --- code ---------------------------------------------------------------
install -d -o root -g root -m 0755 "$APP"
install -o root -g root -m 0644 "$PAYLOAD/usage.py"      "$APP/usage.py"
install -o root -g root -m 0644 "$PAYLOAD/hum_merge.py"  "$APP/hum_merge.py"
install -o root -g root -m 0644 "$PAYLOAD/hum_server.py" "$APP/hum_server.py"
install -o root -g root -m 0755 "$PAYLOAD/hum_sampler.sh" "$APP/hum_sampler.sh"

# --- service account ----------------------------------------------------
# systemd refuses to start a unit whose numeric User=/Group= has no passwd or
# group entry ("Failed to determine user credentials: No such process"), and the
# inputs this service reads are mode 0600 owned by uid 10000 — so the account
# must be created ON uid/gid 10000, never root and never a different uid.
# Login-disabled placeholder account; idempotent.
if ! getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
  groupadd -g "$SERVICE_GROUP" hum
  echo "created group hum (gid $SERVICE_GROUP)"
fi
if ! getent passwd "$SERVICE_USER" >/dev/null 2>&1; then
  useradd -u "$SERVICE_USER" -g "$SERVICE_GROUP" -M -d /nonexistent -s /usr/sbin/nologin hum
  echo "created user hum (uid $SERVICE_USER, nologin)"
fi

# --- access preflight ---------------------------------------------------
# Fail loudly with a real message rather than letting systemd produce the cryptic
# status=200/CHDIR: prove the service uid can actually read its code and inputs.
if command -v runuser >/dev/null 2>&1; then
  for p in "$APP/hum_server.py" /opt/data/profiles/itguy/state.db; do
    if ! runuser -u hum -- test -r "$p"; then
      echo "ERROR: user 'hum' (uid $SERVICE_USER) cannot read $p" >&2
      echo "       check the permissions of every directory on that path" >&2
      exit 1
    fi
  done
  echo "preflight: uid $SERVICE_USER can read the code and its database"
fi

# --- durable ledger -----------------------------------------------------
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0700 "$LEDGER_DIR"

# --- systemd units ------------------------------------------------------
install -o root -g root -m 0644 "$PAYLOAD/hum.service"         /etc/systemd/system/hum.service
install -o root -g root -m 0644 "$PAYLOAD/hum-sampler.service" /etc/systemd/system/hum-sampler.service
install -o root -g root -m 0644 "$PAYLOAD/hum-sampler.timer"   /etc/systemd/system/hum-sampler.timer

systemctl daemon-reload
systemctl enable --now hum.service
systemctl enable --now hum-sampler.timer

# --- verification -------------------------------------------------------
echo
echo "=== SERVICE STATE ==="
echo "hum.service        : $(systemctl is-active hum.service) / $(systemctl is-enabled hum.service)"
echo "hum-sampler.timer  : $(systemctl is-active hum-sampler.timer) / $(systemctl is-enabled hum-sampler.timer)"

IP="$(tailscale ip -4 2>/dev/null | head -1)"
if [ -z "${IP:-}" ]; then
  echo "ERROR: no tailnet IPv4 — cannot verify endpoint" >&2
  exit 1
fi
echo "tailnet IP         : $IP"

echo
echo "=== ENDPOINT ==="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
OK=0
for _ in $(seq 1 25); do
  if curl -fsS -o "$TMP/body.json" -D "$TMP/headers.txt" --max-time 10 "http://${IP}:${PORT}/hum"; then
    OK=1
    break
  fi
  sleep 1
done
if [ "$OK" -ne 1 ]; then
  echo "FAIL: endpoint did not answer within 25s"
  journalctl -u hum.service --no-pager -n 20 || true
  exit 1
fi

python3 - "$TMP/body.json" "$TMP/headers.txt" <<'PY'
import json, sys
body = open(sys.argv[1], encoding='utf-8').read()
headers = open(sys.argv[2], encoding='utf-8', errors='ignore').read().lower()
data = json.loads(body)
display = data.get('display')
checks = [
    ('payload is one object with a display array', isinstance(display, list)),
    ('display has exactly 10 entries', isinstance(display, list) and len(display) == 10),
    ('every entry is a string', isinstance(display, list)
        and all(isinstance(x, str) for x in display)),
    ('content-type is application/json; charset=utf-8',
        'content-type: application/json; charset=utf-8' in headers),
    ('cache-control is no-store', 'cache-control: no-store' in headers),
]
failed = False
for name, ok in checks:
    print(('PASS  ' if ok else 'FAIL  ') + name)
    failed = failed or not ok
print()
print('--- payload ---')
for i, line in enumerate(display if isinstance(display, list) else [], 1):
    print('%2d| %s' % (i, line))
raise SystemExit(1 if failed else 0)
PY

echo
echo "=== LISTENERS ON ${PORT} (must be the tailnet IP only) ==="
ss -lntp 2>/dev/null | grep ":${PORT}" || echo "no listener on ${PORT}"
if ss -lnt 2>/dev/null | grep -E "0\.0\.0\.0:${PORT}|\*:${PORT}"; then
  echo "FAIL: port ${PORT} is bound to a public interface" >&2
  exit 1
fi
echo
echo "VERIFY: endpoint OK"
