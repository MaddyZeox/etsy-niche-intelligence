"""Provider-independent, evidence-first keyword demand record normalization.

No external requests, guessed demand or keyword difficulty. This module does not
claim an Etsy Open API search-volume endpoint. Connect an authorized provider
adapter separately after verifying permitted commercial use.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping
import re

SCHEMA_VERSION = '1.0'
METRICS = ('etsy_monthly_searches', 'etsy_monthly_clicks', 'etsy_ctr',
           'google_monthly_searches', 'etsy_competing_listings')


def _nullable_number(name: str, value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f'{name} must be a nonnegative numeric value or null')
    if value < 0 or value != value or value == float('inf') or value == float('-inf'):
        raise ValueError(f'{name} must be finite and nonnegative')
    if name == 'etsy_ctr':
        if value > 100:
            raise ValueError('etsy_ctr must be between 0 and 100 percent')
    elif int(value) != value:
        raise ValueError(f'{name} must be an integer count')
    return value if name == 'etsy_ctr' else int(value)


def empty_research(keyword: str, market: str = 'GLOBAL', language: str = 'und') -> dict:
    """Generate a *clearly unavailable* demand report, never fake zeros."""
    return normalize_record({
        'keyword': keyword, 'market': market, 'language': language,
        'provider': 'unavailable', 'source_type': 'not_available',
        'license_status': 'not_applicable', 'collected_at': None,
        'period_start': None, 'period_end': None,
        'metrics': {}, 'monthly_history': [],
    })


def normalize_record(raw: Mapping[str, Any]) -> dict:
    """Normalize data from a *documented and authorized* demand source.

    Accepted source_type: first_party, third_party_estimate, google_search,
    not_available. Etsy and Google counts must never be silently substituted.
    A provider integration is not authorized merely by setting a label here.
    """
    if not isinstance(raw, Mapping):
        raise ValueError('record must be a mapping')
    keyword = raw.get('keyword')
    if not isinstance(keyword, str) or not 2 <= len(keyword.strip()) <= 100:
        raise ValueError('keyword must be between 2 and 100 characters')
    market = raw.get('market', 'GLOBAL')
    if not isinstance(market, str) or not re.fullmatch(r'GLOBAL|[A-Z]{2}', market):
        raise ValueError('market must be GLOBAL or ISO 2-letter uppercase code')
    language = raw.get('language', 'und')
    if not isinstance(language, str) or not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2,8})*', language):
        raise ValueError('language must be a BCP-47-like language tag or und')
    source_type = raw.get('source_type', 'not_available')
    if source_type not in {'first_party', 'third_party_estimate', 'google_search', 'not_available'}:
        raise ValueError('invalid source_type')
    provider = raw.get('provider')
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError('provider is required')
    license_status = raw.get('license_status', 'unverified')
    if license_status not in {'authorized', 'unverified', 'restricted', 'not_applicable'}:
        raise ValueError('invalid license_status')
    incoming = raw.get('metrics') or {}
    if not isinstance(incoming, Mapping) or set(incoming).difference(METRICS):
        raise ValueError('unknown metric names or invalid metrics map')
    metrics = {key: _nullable_number(key, incoming.get(key)) for key in METRICS}
    if source_type == 'not_available' and any(v is not None for v in metrics.values()):
        raise ValueError('unavailable source must not contain claimed metrics')
    if source_type == 'google_search' and any(metrics[key] is not None for key in ('etsy_monthly_searches', 'etsy_monthly_clicks', 'etsy_ctr')):
        raise ValueError('Google metrics cannot be misrepresented as Etsy buyer demand')
    period_start, period_end = raw.get('period_start'), raw.get('period_end')
    for field, value in (('period_start', period_start), ('period_end', period_end)):
        if value is not None:
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                raise ValueError(f'{field} must be ISO YYYY-MM-DD') from None
    if period_start and period_end and period_start > period_end:
        raise ValueError('period_start cannot exceed period_end')
    history = raw.get('monthly_history') or []
    if not isinstance(history, list):
        raise ValueError('monthly_history must be a list')
    cleaned_history = []
    last_month = None
    for point in history:
        if not isinstance(point, Mapping) or set(point) != {'month', 'searches'}:
            raise ValueError('monthly_history entries require month and searches')
        month = point['month']
        if not isinstance(month, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
            raise ValueError('month must be YYYY-MM')
        searches = _nullable_number('monthly searches', point['searches'])
        if last_month is not None and month <= last_month:
            raise ValueError('months must be strictly ascending and unique')
        cleaned_history.append({'month': month, 'searches': searches})
        last_month = month
    if history and source_type == 'not_available':
        raise ValueError('unavailable source cannot supply historical data')
    if source_type == 'google_search' and history:
        raise ValueError('Google search history needs its own explicitly named series')
    collected_at = raw.get('collected_at')
    if collected_at is not None:
        if not isinstance(collected_at, str):
            raise ValueError('collected_at must be ISO-8601 or null')
        try:
            parsed = datetime.fromisoformat(collected_at.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('invalid collected_at') from None
        if parsed.tzinfo is None:
            raise ValueError('collected_at must include timezone')
    return {
        'schema_version': SCHEMA_VERSION,
        'keyword': ' '.join(keyword.split()), 'market': market,
        'language': language, 'provider': provider.strip(),
        'source_type': source_type, 'license_status': license_status,
        'collected_at': collected_at, 'period_start': period_start,
        'period_end': period_end, 'metrics': metrics,
        'monthly_history': cleaned_history,
        'data_available': any(value is not None for value in metrics.values()) or bool(cleaned_history),
        'notes': raw.get('notes', '') if isinstance(raw.get('notes', ''), str) else '',
    }
