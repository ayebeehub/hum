#!/usr/bin/env python3
"""HUM private endpoint.

Serves exactly one route: GET /hum -> {"display": [ten strings]}.
Bound to the Tailscale interface only (never 0.0.0.0). No HTML, no request
bodies, no raw records, no authentication material. Response bodies and query
results are never logged; access logging is disabled entirely.

HUM_BIND_IP may be an explicit address, or "auto" (default) to discover the
node's own tailnet IPv4 at startup, so the service survives a node rename or
an address change without an edit.
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hum_merge  # noqa: E402
from usage import collect, render_display  # noqa: E402

BIND_IP_SETTING = os.environ.get('HUM_BIND_IP', 'auto')
PORT = int(os.environ.get('HUM_PORT', '8765'))
ITGUY_DB = os.environ.get('HUM_ITGUY_DB', '/opt/data/profiles/itguy/state.db')
LEDGER = os.environ.get('HUM_LEDGER', '/var/lib/hum/hum-ledger.sqlite')
NORA_EXPORT = os.environ.get('HUM_NORA_EXPORT', '/opt/data/hum-export/nora-usage.json')

# The ledger has exactly one writer: the whole collect+merge runs under a lock.
_LOCK = threading.Lock()


def resolve_bind_ip() -> str | None:
    """Explicit address, or the node's own 100.x tailnet IPv4 when 'auto'."""
    setting = BIND_IP_SETTING.strip()
    if setting and setting.lower() != 'auto':
        return setting
    try:
        result = subprocess.run(['tailscale', 'ip', '-4'], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        address = line.strip()
        if address.startswith('100.'):
            return address
    return None


def build_payload() -> bytes:
    with _LOCK:
        itguy = collect({'ITGuy': ITGUY_DB}, LEDGER)
        nora = hum_merge.load_nora(NORA_EXPORT)
        merged, _reconciliation = hum_merge.merge(itguy, nora)
        body = json.dumps({'display': render_display(merged)},
                          separators=(',', ':'), ensure_ascii=False)
    return body.encode('utf-8')


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'hum'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def _respond(self, code: int, body: bytes = b''):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path.split('?', 1)[0] != '/hum':
            return self._respond(404, b'{"error":"not found"}')
        try:
            body = build_payload()
        except Exception as exc:  # log the class name only, never values
            print('hum: collection failed: %s' % type(exc).__name__,
                  file=sys.stderr, flush=True)
            return self._respond(503, b'{"error":"unavailable"}')
        self._respond(200, body)

    def do_HEAD(self):
        self._respond(405, b'{"error":"method not allowed"}')

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD

    def log_message(self, fmt, *args):
        """Access logging disabled: never record request paths or bodies."""
        return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    bind_ip = resolve_bind_ip()
    if not bind_ip:
        print('hum: no tailnet IPv4 available (tailscaled down or not logged in)',
              file=sys.stderr, flush=True)
        return 1
    try:
        with Server((bind_ip, PORT), Handler) as httpd:
            print('hum: listening on http://%s:%d/hum' % (bind_ip, PORT), flush=True)
            httpd.serve_forever()
    except OSError as exc:
        print('hum: cannot bind %s:%d (%s)' % (bind_ip, PORT, exc),
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())