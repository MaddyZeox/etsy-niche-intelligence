"""Niche Intelligence v4.9: pure planning/merging for optional shop enrichment.

No Etsy calls, no filesystem access, no changes to existing collector behavior.
The caller must authenticate, rate-limit, fetch and cache profiles itself.
"""
from dataclasses import dataclass
from typing import Mapping, Optional


def shop_ids_in_order(listings):
    """Unique positive integer shop IDs, in listing order; never infer product relevance."""
    seen = set()
    result = []
    for row in listings:
        if not isinstance(row, dict):
            continue
        raw = row.get('shop_id')
        if isinstance(raw, bool):
            continue
        try:
            sid = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if sid <= 0 or sid in seen or str(raw).strip() != str(sid):
            continue
        result.append(sid)
        seen.add(sid)
    return result


@dataclass(frozen=True)
class EnrichmentPlan:
    mode: str
    shop_ids: tuple
    cached_ids: tuple
    fetch_ids: tuple
    deferred_ids: tuple

    @property
    def requested_count(self):
        return len(self.fetch_ids)

    def as_dict(self):
        return {
            'mode': self.mode,
            'unique_shop_count': len(self.shop_ids),
            'cached_shop_count': len(self.cached_ids),
            'fetch_shop_count': len(self.fetch_ids),
            'deferred_shop_count': len(self.deferred_ids),
        }


def plan_enrichment(listings, cached_profiles: Mapping, mode='full', requested_shop_ids=None):
    """Plan by explicit mode; never auto-exclude shops based on unverified relevance.

    quick: use available valid cached profiles, fetch none.
    selected: fetch only explicitly selected IDs present in this listing sample.
    full: fetch all uncached shops, as the current collector already does.
    """
    if mode not in {'quick', 'selected', 'full'}:
        raise ValueError('mode must be quick, selected or full')
    all_ids = shop_ids_in_order(listings)
    present = set(all_ids)
    if mode == 'selected':
        if requested_shop_ids is None:
            raise ValueError('selected mode requires explicitly requested shop IDs')
        if len(requested_shop_ids) > 100:
            raise ValueError('too many requested shop IDs')
        selected = set(shop_ids_in_order([{'shop_id': s} for s in requested_shop_ids]))
        if selected - present:
            raise ValueError('requested shop ID is outside current research sample')
    else:
        selected = present if mode == 'full' else set()

    # Empty shop profiles are not valid coverage; allow int or string dictionary keys.
    def is_cached(sid):
        profile = cached_profiles.get(sid, cached_profiles.get(str(sid)))
        return isinstance(profile, dict) and bool(profile)

    cached_ids = tuple(sid for sid in all_ids if is_cached(sid))
    fetched = tuple(sid for sid in all_ids if sid in selected and not is_cached(sid))
    deferred = tuple(sid for sid in all_ids if sid not in selected and not is_cached(sid))
    return EnrichmentPlan(mode, tuple(all_ids), cached_ids, fetched, deferred)


def shop_coverage(listings, profiles: Mapping):
    """Coverage metadata only. Incomplete never means zero reviews/sales."""
    ids = shop_ids_in_order(listings)
    known = 0
    for sid in ids:
        x = profiles.get(sid, profiles.get(str(sid)))
        known += isinstance(x, dict) and bool(x)
    return {
        'unique_shops': len(ids),
        'enriched_shops': int(known),
        'unenriched_shops': len(ids) - int(known),
        'shop_data_complete': known == len(ids),
        'shop_data_status': 'complete' if known == len(ids) else 'partial',
    }


def attach_shop_data(listings, profiles: Mapping):
    """Merge shop profile signals without overwriting listing/currency fields.

    Caller supplies fresh, validated/cached profiles. Unknown shop fields are None.
    """
    import copy
    updated = []
    for item in listings:
        row = copy.deepcopy(item)
        sid = row.get('shop_id')
        profile = profiles.get(sid, profiles.get(str(sid))) if sid is not None else None
        if not isinstance(profile, dict) or not profile:
            profile = None
        row['shop_data_available'] = profile is not None
        row['shop_name'] = profile.get('shop_name') if profile else None
        row['shop_total_sales'] = profile.get('transaction_sold_count') if profile else None
        row['shop_review_count'] = profile.get('review_count') if profile else None
        row['shop_rating'] = profile.get('review_average') if profile else None
        row['shop_created_timestamp'] = profile.get('created_timestamp') if profile else None
        # shop_age_years is left for caller's existing age_years() function.
        row['shop_age_years'] = None
        updated.append(row)
    return updated
