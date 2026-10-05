#!/usr/bin/env bash
# ITGuy vetted OBSERVE script: read-only HUM status. No state is changed.
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root" >&2
  exit 1
fi

echo "=== UNITS ==="
echo "hum.service       : $(systemctl is-active hum.service 2>&1) / $(systemctl is-enabled hum.service 2>&1)"
echo "hum-sampler.timer : $(systemctl is-active hum-sampler.timer 2>&1) / $(systemctl is-enabled hum-sampler.timer 2>&1)"
echo "last sampler run  : $(systemctl show hum-sampler.service -p ExecMainStartTimestamp --value 2>&1)"
echo "next sampler run  : $(systemctl list-timers hum-sampler.timer --no-pager 2>/dev/null | sed -n '2p')"

echo
echo "=== BINDING ==="
IP="$(tailscale ip -4 2>/dev/null | head -1)"
echo "tailnet IP: ${IP:-<none>}"
ss -lntp 2>/dev/null | grep ':8765' || echo "no listener on 8765"
if ss -lnt 2>/dev/null | grep -Eq '0\.0\.0\.0:8765|\*:8765'; then
  echo "FAIL: 8765 is bound to a public interface"
else
  echo "PASS: 8765 is not bound to a public interface"
fi

echo
echo "=== LEDGER ==="
ls -l /var/lib/hum/ 2>&1
echo "ledger age (s): $(python3 -c "
import os,time
p='/var/lib/hum/hum-ledger.sqlite'
print(round(time.time()-os.path.getmtime(p),1) if os.path.exists(p) else 'missing')" 2>&1)"

echo
echo "=== NORA EXPORT ==="
python3 -c "
import json,os,time
from datetime import datetime
p='/opt/data/hum-export/nora-usage.json'
if not os.path.exists(p):
    print('missing'); raise SystemExit
d=json.load(open(p))
age=time.time()-datetime.fromisoformat(d['published_at']).timestamp()
print('published_at :', d['published_at'])
print('age (s)      :', round(age,1), '| stale' if age>300 else '| fresh')
print('partial      :', d.get('partial'))
print('reconcile    :', (d.get('reconciliation') or {}).get('status'))
" 2>&1

echo
echo "=== ENDPOINT PAYLOAD ==="
if [ -n "${IP:-}" ]; then
  HDR="$(mktemp)"; BODY="$(mktemp)"
  if curl -fsS -o "$BODY" -D "$HDR" --max-time 10 "http://${IP}:8765/hum"; then
    echo "headers:"; grep -iE 'content-type|cache-control' "$HDR"
    python3 - "$BODY" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
disp = d.get('display')
print('display entries:', len(disp) if isinstance(disp, list) else 'NOT A LIST')
for i, line in enumerate(disp or [], 1):
    print('%2d| %s' % (i, line))
PY
  else
    echo "FAIL: endpoint did not answer"
  fi
  rm -f "$HDR" "$BODY"
else
  echo "skipped: no tailnet IP"
fi

echo
echo "=== RECENT SERVICE LOG ==="
journalctl -u hum.service --no-pager -n 10 2>&1 | tail -10
