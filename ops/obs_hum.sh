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
echo "=== LEDGER COVERAGE (what makes the footer read PARTIAL) ==="
python3 - <<'PY' 2>&1
import sqlite3, time
from datetime import datetime, timezone

LEDGER = '/var/lib/hum/hum-ledger.sqlite'
GAP = 180                      # usage.py: any gap > 180s marks the window partial
IST = 5.5 * 3600
now = time.time()
utc = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')
boundary_today = ((now + IST) // 86400) * 86400 + 6 * 3600 - IST   # 06:00 IST today

try:
    con = sqlite3.connect('file:%s?mode=ro' % LEDGER, uri=True, timeout=5)
    con.execute('PRAGMA busy_timeout=5000')
    agents = [r[0] for r in con.execute('SELECT DISTINCT agent FROM sample ORDER BY agent')]
except sqlite3.Error as exc:
    print('ledger unreadable: %s' % exc)
    raise SystemExit(0)

print('agents sampled in this ledger: %s' % (', '.join(agents) if agents else 'none'))
print('(Nora is not sampled here by design - her windows come from her export, above)')

for agent in agents:
    rows = [r[0] for r in con.execute(
        'SELECT at FROM sample WHERE agent=? AND at<=? ORDER BY at', (agent, now))]
    if not rows:
        print('%s: no samples' % agent)
        continue
    gaps = [(b - a, a, b) for a, b in zip(rows, rows[1:])]
    print('')
    print('%s: first=%s  last=%s  samples=%d' % (agent, utc(rows[0]), utc(rows[-1]), len(rows)))
    for label, since, clears in (('today', boundary_today, boundary_today + 86400),
                                 ('24h', now - 86400, None)):
        holes = [g for g in gaps if g[0] > GAP and g[2] >= since]
        if rows[0] > since:
            print('  %-5s PARTIAL - ledger starts after the window opens' % label)
        elif holes:
            for length, a, b in holes:
                print('  %-5s PARTIAL - %ds gap %s -> %s' % (label, length, utc(a), utc(b)))
            if clears is None:
                print('  %-5s clears %s' % (label, utc(max(h[2] for h in holes) + 86400)))
            else:
                print('  %-5s clears at the next 06:00 IST boundary (%s)' % (label, utc(clears)))
        else:
            print('  %-5s OK - fully covered, no gap > %ds' % (label, GAP))
PY

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
