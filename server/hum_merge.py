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
from datetime import datetime

from usage import IST

STALE_AFTER_SECONDS = 300

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


def merge(itguy, nora):
    """Merge ITGuy's collect() result with Nora's export -> (merged, reconciliation)."""
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

    merged = {
        'updated_at': updated_at,
        'today': today,
        'session': {'Nora': session_nora, 'ITGuy': itguy['session']['ITGuy']},
        'last_24h': {'Nora': hours_nora, 'ITGuy': itguy['last_24h']['ITGuy']},
    }
    return merged, (nora or {}).get('reconciliation')