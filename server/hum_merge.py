"""HUM merge: combine ITGuy's own state.db windows with Nora's published export.

Data boundary (doc 05, non-negotiable):
  * this module reads ITGuy's OWN state.db (via usage.collect) and Nora's
    pre-aggregated export file only;
  * Nora's state.db is never opened, referenced, or probed here.

Rules:
  * one source per agent — the two are summed for Today gross ONLY, never
    added per-row;
  * a missing/unreadable Nora export yields null windows (rendered as
    warming/partial), never a fabricated number;
  * a merely stale export is still summed but forces the PARTIAL footer.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from usage import IST

STALE_AFTER_SECONDS = 300
# A billed figure older than this must never masquerade as current. The renderer
# falls back to the EST. USD display instead — never a silent $0.00.
BILLED_MAX_AGE_SECONDS = 900
BILLED_PATH = os.environ.get('HUM_BILLED', '/var/lib/hum/billed.json')

_UNAVAILABLE_SESSION = {'status': 'unavailable', 'model': None, 'input_tokens': None,
                        'output_tokens': None, 'cache_read_tokens': None, 'cost_usd': None}
_UNAVAILABLE_WINDOW = {'input_tokens': None, 'output_tokens': None, 'cache_read_tokens': None,
                       'cost_usd': None, 'partial': True, 'model': None}


def load_nora(path, now=None):
    """Load Nora's export, adding a 'stale' flag. Returns None if unusable."""
    if now is None:
        now = datetime.now(IST).timestamp()
    try:
        with open(path, encoding='utf-8') as fh:
            export = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(export, dict) or export.get('agent') != 'Nora':
        return None
    try:
        published = datetime.fromisoformat(export['published_at']).timestamp()
    except (KeyError, TypeError, ValueError):
        return None
    for key in ('today', 'session', 'last_24h'):
        if not isinstance(export.get(key), dict):
            return None
    export['stale'] = (now - published) > STALE_AFTER_SECONDS
    return export


def load_billed(path=None, now=None):
    """Load /var/lib/hum/billed.json written by the hum-billed timer.

    Returns None when the file is absent, malformed or older than
    BILLED_MAX_AGE_SECONDS. None means "no billed figure available", which the
    renderer shows as the EST. USD display — a provider outage or a broken timer
    must degrade to the honest estimate, never to a stale number or $0.00.
    """
    if path is None:
        path = BILLED_PATH
    if now is None:
        now = datetime.now(IST).timestamp()
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or not isinstance(data.get('account'), dict)
            or not isinstance(data.get('agents'), dict)):
        return None
    try:
        fetched = datetime.fromisoformat(data['fetched_at']).timestamp()
    except (KeyError, TypeError, ValueError):
        return None
    age = now - fetched
    if age < 0 or age > BILLED_MAX_AGE_SECONDS:
        return None
    data['age_seconds'] = age
    return data


def merge(itguy, nora, billed='auto'):
    """Merge ITGuy's collect() result with Nora's export -> (merged, reconciliation).

    `billed` is the provider-billed figure set (see load_billed). Pass None to
    force the estimate-only display; 'auto' (the default) loads it from disk.
    The provider-billed money and the local estimates are reported side by side
    and are never summed together.
    """
    today = dict(itguy['today'])
    itguy_partial = bool(today.get('partial'))

    if nora is None:
        today = {'input_tokens': None, 'output_tokens': None,
                 'cost_usd': None, 'partial': True}
        session_nora = dict(_UNAVAILABLE_SESSION)
        hours_nora = dict(_UNAVAILABLE_WINDOW)
        updated_at = itguy['updated_at']
    else:
        for key in ('input_tokens', 'output_tokens'):
            left, right = today.get(key), nora['today'].get(key)
            today[key] = (left + right) if (left is not None and right is not None) else None
        left, right = today.get('cost_usd'), nora['today'].get('cost_usd')
        if left is not None and right is not None:
            today['cost_usd'] = round(left + right, 8)
        today['partial'] = bool(itguy_partial or nora['today'].get('partial')
                                or nora.get('stale'))
        session_nora = nora['session']
        hours_nora = nora['last_24h']
        updated_at = max(itguy['updated_at'], nora['published_at'])

    if billed == 'auto':
        billed = load_billed()
    # Nora's media generation cost. Carried through opaque (like her
    # reconciliation verdict). MEASURED 2026-10-20: this spend bills to her own
    # key, so it is ALREADY inside the account total and inside her per-agent
    # row. It is a component of both, never an addend — the old note claiming it
    # was "not attributable to a key" is what produced the double count.
    media = None
    if isinstance(nora, dict) and isinstance(nora.get('media_gen'), dict):
        media = nora['media_gen']

    merged = {
        'updated_at': updated_at,
        'today': today,
        'session': {'Nora': session_nora, 'ITGuy': itguy['session']['ITGuy']},
        'last_24h': {'Nora': hours_nora, 'ITGuy': itguy['last_24h']['ITGuy']},
        'billed': billed,
        'media': media,
    }
    return merged, (nora or {}).get('reconciliation')