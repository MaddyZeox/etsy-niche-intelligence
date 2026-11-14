"""Niche Intelligence v3: conservative, product-agnostic relevance baseline.

Does not call Etsy, external AI, translate, predict ranking, infer sales or use
credentials. Supports Unicode / multilingual source strings without destroying
original labels. Cross-language semantic equivalence requires a validated model
and is intentionally NOT claimed by this lexical baseline.
"""
from __future__ import annotations
import html
import re
import unicodedata
from collections import Counter
from typing import Any, Mapping

GENERIC_WORDS = frozenset({
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'for', 'with', 'in', 'by', 'on',
    'from', 'at', 'your', 'my', 'our', 'new', 'best', 'gift', 'gifts',
    'personalized', 'personalised', 'custom', 'customized', 'customisable',
    'unique', 'modern', 'premium', 'luxury', 'beautiful', 'editable',
})


def normalize(value: Any) -> str:
    """Case-fold and tokenize while preserving Unicode letters/numbers."""
    value = unicodedata.normalize('NFKC', html.unescape(str(value or ''))).casefold()
    return ' '.join(re.findall(r'[^\W_]+', value, flags=re.UNICODE))


def terms(value: Any) -> tuple[str, ...]:
    return tuple(w for w in normalize(value).split() if w not in GENERIC_WORDS)


def stable_id(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    v = str(value).strip()
    return v or None


def analyze(keyword: str, listings: list[Mapping[str, Any]], *, product_type: str = '') -> dict[str, Any]:
    """Non-predictive retrieval audit; NEVER a verified direct-competition verdict.

    Evidence precedence: explicit phrase in title > all meaningful tokens in
    title > some meaningful tokens > tags alone > unresolved. No guessed
    translation, no shop-sales-as-listing-sales, no impossible ranking verdict.
    An explicit product type can sharpen token evidence but remains unverified.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError('keyword required')
    if not isinstance(listings, list) or len(listings) > 5000:
        raise ValueError('listings must be a list with at most 5000 items')
    k = normalize(keyword)
    key_terms = tuple(dict.fromkeys(terms(keyword)))
    p_terms = tuple(dict.fromkeys(terms(product_type)))
    results: list[dict[str, Any]] = []
    seen = set()
    counts: Counter[str] = Counter()
    for raw in listings:
        if not isinstance(raw, Mapping):
            continue
        ident = stable_id(raw.get('listing_id', raw.get('listingId')))
        if not ident or ident in seen:
            continue
        seen.add(ident)
        title = normalize(raw.get('title'))
        title_set = set(terms(raw.get('title')))
        tags = raw.get('tags')
        if not isinstance(tags, list):
            tags = []
        tag_set = set()
        for t in tags:
            if isinstance(t, str):
                tag_set.update(terms(t))
        matched_title = [w for w in key_terms if w in title_set]
        matched_tags = [w for w in key_terms if w in tag_set]
        product_title = [w for w in p_terms if w in title_set]
        product_tag = [w for w in p_terms if w in tag_set]
        exact = bool(k and re.search(r'(?<!\w)' + re.escape(k) + r'(?!\w)', title))
        if not key_terms:
            label, reason = 'needs_review', 'Keyword contains no distinctive words after normalization'
        elif p_terms and not product_title and not product_tag:
            label, reason = 'needs_review', 'Selected product type is not established by title or tags'
        elif exact and (not p_terms or len(product_title) == len(p_terms)):
            label, reason = 'strong_title_match', 'Full keyword in title' + ('; specified product type also in title' if p_terms else '')
        elif len(matched_title) == len(key_terms) and (not p_terms or len(product_title) == len(p_terms)):
            label, reason = 'strong_title_match', 'All distinctive keyword words in title' + (' and selected product type' if p_terms else '')
        elif matched_title:
            label, reason = 'partial_match', 'Only some keyword words in title; product equivalence unconfirmed'
        elif matched_tags:
            label, reason = 'tags_only', 'Match appears in seller tags, not in listing title'
        else:
            label, reason = 'needs_review', 'No lexical match; may be another language or different product'
        counts[label] += 1
        results.append({
            'listing_id': ident,
            'label': label,
            'reason': reason,
            'matched_title_terms': matched_title,
            'matched_tag_terms': matched_tags,
            'matched_product_type_terms': product_title,
        })
    return {
        'keyword': keyword, 'product_type': product_type,
        'method': 'unicode_lexical_evidence_v3_baseline',
        'model_validated': False,
        'multilingual_semantic_verified': False,
        'difficulty': None, 'ranking_likelihood': None,
        'verdict': 'NOT_SCORED',
        'counts': {x: counts[x] for x in ('strong_title_match', 'partial_match', 'tags_only', 'needs_review')},
        'sampled_unique': len(results),
        'explanation': 'Retrieval audit, not validated direct competition, Etsy buyer demand, or beginner ranking difficulty.',
        'listings': results,
    }


# Optional v3-compatible research layer. This is a grouping / observation aid,
# not a classifier validated against human-labelled product equivalence.
def competitor_evidence(keyword: str, listings: list[Mapping[str, Any]], *, product_type: str = '', top_tags: int = 20) -> dict[str, Any]:
    """Observe Etsy category and exact seller-tag patterns alongside lexical matches.

    Never treat category coincidence, favorites, or shop-wide sales as proof of
    direct product competition or listing sales. Missing values remain unknown.
    """
    if not isinstance(top_tags, int) or isinstance(top_tags, bool) or not 1 <= top_tags <= 100:
        raise ValueError('top_tags must be an integer from 1 to 100')
    audit = analyze(keyword, listings, product_type=product_type)
    by_id = {r['listing_id']: r for r in audit['listings']}
    observed = {}
    categories = {}
    # Only non-duplicate records that participated in the legacy lexical audit.
    for raw in listings:
        if not isinstance(raw, Mapping):
            continue
        lid = stable_id(raw.get('listing_id', raw.get('listingId')))
        if lid not in by_id or lid in observed:
            continue
        observed[lid] = raw
        path = raw.get('category_path')
        name = raw.get('category_name')
        tid = raw.get('taxonomy_id')
        # Non-empty path from the taxonomy resolver is strongest display evidence.
        category = path.strip() if isinstance(path, str) and path.strip() else (
            name.strip() if isinstance(name, str) and name.strip() else None)
        if not category:
            continue
        # Multiple taxonomy IDs may have same short name. Keep IDs distinct.
        key = str(tid) if isinstance(tid, int) and not isinstance(tid, bool) and tid > 0 else 'path:' + category
        entry = categories.setdefault(key, {'taxonomy_id': tid if isinstance(tid, int) and not isinstance(tid, bool) else None,
                                             'category_path': category, 'listing_count': 0,
                                             'strong_title_matches': 0, 'listing_ids': []})
        entry['listing_count'] += 1
        entry['listing_ids'].append(lid)
        if by_id[lid]['label'] == 'strong_title_match':
            entry['strong_title_matches'] += 1
    groups = sorted(categories.values(), key=lambda e: (-e['listing_count'], e['category_path'].casefold()))

    def clean_tags(rows):
        tag_records = {}
        for lid, raw in rows:
            values = raw.get('tags')
            if not isinstance(values, list):
                continue
            local = set()
            for val in values:
                if not isinstance(val, str):
                    continue
                norm = normalize(val)
                if not norm or norm in local:
                    continue
                local.add(norm)
                tag_records.setdefault(norm, {'tag': norm, 'listing_ids': [], 'favorite_values': []})
                rec = tag_records[norm]
                rec['listing_ids'].append(lid)
                fav = raw.get('favorites')
                if isinstance(fav, int) and not isinstance(fav, bool) and fav >= 0:
                    rec['favorite_values'].append(fav)
        result = []
        for rec in tag_records.values():
            f = sorted(rec.pop('favorite_values'))
            mid = (f[(len(f)-1)//2] if len(f)%2 else (f[len(f)//2-1]+f[len(f)//2])/2) if f else None
            result.append({'tag': rec['tag'], 'listing_count': len(rec['listing_ids']),
                           'listings_with_favorites': len(f), 'median_listing_favorites': mid})
        return sorted(result, key=lambda r: (-r['listing_count'], r['tag']))[:top_tags]

    all_rows = list(observed.items())
    strong_rows = [(lid, raw) for lid, raw in all_rows if by_id[lid]['label'] == 'strong_title_match']
    return {
        'method': 'category_and_tag_observation_v1',
        'keyword': keyword,
        'product_type': product_type,
        'sampled_unique': len(observed),
        'category_covered_listings': sum(g['listing_count'] for g in groups),
        'category_groups': groups,
        'top_seller_tags_all_sample': clean_tags(all_rows),
        'top_seller_tags_strong_title_matches': clean_tags(strong_rows),
        'strong_title_match_count': len(strong_rows),
        'direct_competitors_verified': False,
        'etsy_search_volume': None,
        'listing_sales': None,
        'recommended_category': None,
        'verdict': 'NEEDS_PRODUCT_VALIDATION',
        'notes': [
            'Category distribution describes returned Etsy listings, not category-specific keyword demand.',
            'Strong title matches are lexical signals, not validated direct competitors.',
            'Listing favorites are observed engagement, not listing sales or tag-attributed conversions.',
            'Seller tag frequency is not Etsy buyer search volume.',
            'Original keyword and seller tags are normalized for counting, not translated across languages.',
        ],
    }
