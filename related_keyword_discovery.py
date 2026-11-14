"""Niche Intelligence — cautious, reusable Etsy seller-phrase discovery.

Offline observations only. No network calls, ranking predictions, Etsy buyer-search
volume, AI translation, or fabricated sales. Uses the current relevance module's
Unicode normalization so results are comparable with its existing audit.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Mapping

from universal_relevance_v3 import normalize, stable_id, terms


def discover(keyword: str, listings: list[Mapping[str, Any]], *, limit: int = 30,
             min_listings: int = 2) -> dict[str, Any]:
    """Find related *seller-used* phrases from tags and contiguous title n-grams.

    Phrases need at least one distinctive query word; semantic equivalence of
    synonyms / different languages is not asserted. Cross-listing counts are
    deduplicated by listing ID, with per-phrase shop counts and favorites coverage.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError('keyword required')
    if not isinstance(listings, list) or len(listings) > 5000:
        raise ValueError('listings must be a list of at most 5000 items')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be 1..100')
    if type(min_listings) is not int or not 1 <= min_listings <= 5000:
        raise ValueError('min_listings must be 1..5000')

    query = normalize(keyword)
    query_terms = set(terms(keyword))
    unique = {}
    for raw in listings:
        if not isinstance(raw, Mapping):
            continue
        lid = stable_id(raw.get('listing_id', raw.get('listingId')))
        if lid and lid not in unique:
            unique[lid] = raw

    observations = defaultdict(lambda: {
        'tag_ids': set(), 'title_ids': set(), 'shops': set(),
        'favorites_by_listing': {}, 'example_listing_ids': []
    })

    for lid, raw in unique.items():
        title_tokens = normalize(raw.get('title')).split()
        tag_phrases = set()
        tags = raw.get('tags')
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str):
                    phrase = normalize(tag)
                    if phrase:
                        tag_phrases.add(phrase)
        title_phrases = set()
        for size in (2, 3, 4):
            for i in range(len(title_tokens) - size + 1):
                title_phrases.add(' '.join(title_tokens[i:i + size]))
        for phrase in tag_phrases | title_phrases:
            # Do not claim a synonym or multilingual semantic match.
            meaningful = set(terms(phrase))
            if not query_terms or not (meaningful & query_terms) or phrase == query:
                continue
            row = observations[phrase]
            if phrase in tag_phrases:
                row['tag_ids'].add(lid)
            if phrase in title_phrases:
                row['title_ids'].add(lid)
            sid = stable_id(raw.get('shop_id'))
            if sid:
                row['shops'].add(sid)
            fav = raw.get('favorites')
            if type(fav) is int and fav >= 0:
                row['favorites_by_listing'][lid] = fav
            if len(row['example_listing_ids']) < 3:
                row['example_listing_ids'].append(lid)

    suggestions = []
    for phrase, row in observations.items():
        ids = row['tag_ids'] | row['title_ids']
        if len(ids) < min_listings:
            continue
        favorites = list(row['favorites_by_listing'].values())
        suggestions.append({
            'phrase': phrase,
            'listing_count': len(ids),
            'seller_tag_listing_count': len(row['tag_ids']),
            'title_phrase_listing_count': len(row['title_ids']),
            'unique_shops_with_ids': len(row['shops']),
            'listings_with_favorites': len(favorites),
            'median_listing_favorites': median(favorites) if favorites else None,
            'example_listing_ids': row['example_listing_ids'],
            'relation': 'shared_keyword_terms_not_semantic_validation',
        })
    suggestions.sort(key=lambda x: (-x['listing_count'], -x['seller_tag_listing_count'],
                                     -x['title_phrase_listing_count'], x['phrase']))
    return {
        'keyword': keyword,
        'method': 'offline_seller_phrase_observations_v1',
        'sampled_unique': len(unique),
        'min_listings': min_listings,
        'suggestions': suggestions[:limit],
        'etsy_monthly_searches': None,
        'keyword_difficulty': None,
        'direct_competitors_verified': False,
        'cross_language_semantics_verified': False,
        'notes': [
            'Phrases are extracted from sampled listing titles and seller tags; they are NOT verified Etsy buyer searches.',
            'Counts reflect sampled listings only; a phrase appearing in both title and tags counts once per listing.',
            'Median favorites indicate observed listing engagement, not sales or tag-attributed conversions.',
            'Suggestions require literal normalized keyword overlap; synonyms and translations are not inferred.',
            'Use the same buyer market and collector cohort when comparing phrase evidence.'
        ]
    }
