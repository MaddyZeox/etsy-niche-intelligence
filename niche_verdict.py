"""Niche Intelligence v1.6 — "Should I work on this?" verdict.

Pure function: no network, no Etsy calls, no invented volume/KD/sales.
It scores ONLY what the collector actually observed in the 100-listing
sample plus Etsy's reported matching-listing count.

Verdict levels:
  GO   — evidence says this keyword is beatable; start with a small test batch.
  TEST — mixed evidence; test with 3-5 listings before committing.
  SKIP — evidence says this keyword is crowded/strong right now; skip or re-scope.

Scoring (documented thresholds, all from observed data):
  reported matching listings: <=10k +2, <=50k +1, <=150k 0, <=400k -1, >400k -2
  strong title-match share:   <25% +1 (gap), 25-50% 0, >50% -1, >75% -2
  median shop lifetime sales: <100 +1, <1000 0, >=1000 -1, >=10000 -2
  median favorites:           <5 +1, otherwise 0
  top-category share:         >=60% +1 (clear niche), <30% -1 (vague keyword)
  average tags per listing:   <10 +1 (sellers under-use tags), >=10 0
  Total >= 3 -> GO, 0..2 -> TEST, < 0 -> SKIP.
  Sample < 10 listings -> TEST (cannot judge), low confidence.

Demand honesty: buyer-search volume is NOT observed by this tool
(provider "unavailable" until an authorized demand source is connected).
A GO verdict therefore always carries "demand unverified — test first";
it never claims proven demand. Ranking levers come from Etsy's public
ranking factors (Seller Handbook: relevance, listing quality/CTR,
recency, shop quality, shipping price, completeness) mapped onto the
observed sample — each lever cites its own evidence numbers.

Language: every user-facing string in this module is English.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return float(median(vals))


def _share(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return part / whole


def verdict(keyword: str, listings: list[Mapping[str, Any]],
            reported_matching_listings: int | None = None,
            relevance: Mapping[str, Any] | None = None,
            demand: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return an evidence-based GO / TEST / SKIP verdict for a keyword."""
    keyword = " ".join(str(keyword or "").strip().split())
    rows = [r for r in (listings or []) if isinstance(r, Mapping)]
    n = len(rows)

    counts: dict[str, Any] = {}
    if isinstance(relevance, Mapping):
        counts = relevance.get("counts", {}) or {}
    strong = int(counts.get("strong_title_match") or 0)
    if strong == 0 and n:
        # Fall back to counting from the rows when no audit was supplied.
        terms = [w for w in keyword.casefold().split() if w]
        for row in rows:
            title = str(row.get("title") or "").casefold()
            if terms and all(t in title for t in terms):
                strong += 1
    strong_share = _share(strong, n)

    shop_sales = [_num(r.get("shop_total_sales")) for r in rows]
    shop_sales = [v for v in shop_sales if v is not None and v >= 0]
    med_sales = _median(shop_sales)
    med_favs = _median([v for v in (_num(r.get("favorites")) for r in rows) if v is not None])
    med_reviews = _median([v for v in (_num(r.get("shop_review_count")) for r in rows) if v is not None])
    med_rating = _median([v for v in (_num(r.get("shop_rating")) for r in rows) if v is not None])

    prices: dict[str, list[float]] = {}
    for row in rows:
        price = _num(row.get("price"))
        cur = str(row.get("currency") or "").upper()
        if price is not None and price >= 0 and cur:
            prices.setdefault(cur, []).append(price)
    dom_currency = max(prices, key=lambda c: len(prices[c])) if prices else None
    med_price = _median(prices[dom_currency]) if dom_currency else None

    categories: dict[str, int] = {}
    for row in rows:
        path = row.get("category_path") or row.get("category_name")
        if path:
            categories[str(path)] = categories.get(str(path), 0) + 1
    top_category = max(categories, key=categories.get) if categories else None
    top_cat_share = _share(categories[top_category], n) if top_category else None

    types: dict[str, int] = {}
    for row in rows:
        t = str(row.get("listing_type") or "").strip().lower()
        if t in ("physical", "digital", "download", "both"):
            t = "digital" if t == "download" else t
            types[t] = types.get(t, 0) + 1
    dom_type = max(types, key=types.get) if types else None
    dom_type_share = _share(types[dom_type], sum(types.values())) if dom_type else None

    tag_counts = [len(r.get("tags") or []) for r in rows if isinstance(r.get("tags"), list)]
    avg_tags = (sum(tag_counts) / len(tag_counts)) if tag_counts else None
    under_tagged = sum(1 for c in tag_counts if c < 13)
    under_tag_share = _share(under_tagged, len(tag_counts)) if tag_counts else None

    with_image = sum(1 for r in rows if r.get("image_url"))
    image_share = _share(with_image, n)

    personalizable = sum(1 for r in rows if r.get("is_personalizable") is True)
    personalizable_share = _share(personalizable, n) if n else None

    demand_status = "unverified"
    if isinstance(demand, Mapping):
        provider = str(demand.get("provider") or "")
        metrics = demand.get("metrics")
        # NOTE: normalize_record returns a metrics dict whose values are all
        # None when nothing is known — a non-empty dict is truthy, so check
        # for at least one real value, not just for the dict existing.
        has_metric = (isinstance(metrics, Mapping)
                      and any(v is not None for v in metrics.values()))
        if provider and provider != "unavailable" and has_metric:
            demand_status = "available"

    reasons: list[str] = []
    score = 0

    if reported_matching_listings is None:
        reasons.append("Etsy did not report a total matching-listing count — competition size is unknown.")
    elif reported_matching_listings <= 10_000:
        score += 2
        reasons.append(f"Only {reported_matching_listings:,} matching listings in total — competition is small.")
    elif reported_matching_listings <= 50_000:
        score += 1
        reasons.append(f"{reported_matching_listings:,} matching listings — medium competition.")
    elif reported_matching_listings <= 150_000:
        reasons.append(f"{reported_matching_listings:,} matching listings — competition is high.")
    elif reported_matching_listings <= 400_000:
        score -= 1
        reasons.append(f"{reported_matching_listings:,} matching listings — competition is very high.")
    else:
        score -= 2
        reasons.append(f"{reported_matching_listings:,} matching listings — this is a very crowded keyword.")

    if n == 0:
        return {
            "verdict": "TEST", "level": "amber",
            "label": "TEST — Get data first",
            "summary": "No listing sample was found for this keyword, so no decision is possible.",
            "confidence": "low",
            "score": 0,
            "reasons": ["The sample is empty — the collector returned no listings."],
            "ranking_levers": [],
            "demand_status": demand_status,
            "demand_note": "Buyer-search demand is not verified.",
            "evidence": {"sample_size": 0},
        }

    if strong_share is not None:
        if strong_share < 0.25:
            score += 1
            reasons.append(f"Only {strong_share:.0%} of listings have a title that strongly matches the keyword — exact-match competition is low.")
        elif strong_share > 0.75:
            score -= 2
            reasons.append(f"{strong_share:.0%} of listings have a title that strongly matches the keyword — exact-match competition is very high.")
        elif strong_share > 0.50:
            score -= 1
            reasons.append(f"{strong_share:.0%} of listings have a title that strongly matches the keyword — exact-match competition is high.")

    if med_sales is not None:
        if med_sales < 100:
            score += 1
            reasons.append(f"Median shop lifetime sales are only {med_sales:,.0f} — shops are weak and beatable.")
        elif med_sales >= 10_000:
            score -= 2
            reasons.append(f"Median shop lifetime sales are {med_sales:,.0f} — large, strong shops dominate.")
        elif med_sales >= 1_000:
            score -= 1
            reasons.append(f"Median shop lifetime sales are {med_sales:,.0f} — shops are strong.")

    if med_favs is not None and med_favs < 5:
        score += 1
        reasons.append(f"Median favorites are only {med_favs:,.0f} — listings get little engagement.")

    if top_cat_share is not None:
        if top_cat_share >= 0.60:
            score += 1
            reasons.append(f"Top category '{top_category}' has a {top_cat_share:.0%} share — the niche is clear.")
        elif top_cat_share < 0.30:
            score -= 1
            reasons.append("No category dominates — the keyword's meaning is scattered and intent is unclear.")

    if avg_tags is not None and avg_tags < 10:
        score += 1
        reasons.append(f"Average tags are only {avg_tags:.1f} of 13 — sellers are not filling their tags, so you can get ahead with all 13.")

    if n < 10:
        return {
            "verdict": "TEST", "level": "amber",
            "label": "TEST — Sample too small",
            "summary": f"Only {n} listings were sampled — any decision would be weak, so run a small test first.",
            "confidence": "low", "score": score,
            "reasons": reasons or ["The sample is too small."],
            "ranking_levers": _ranking_levers(locals()),
            "demand_status": demand_status,
            "demand_note": "Buyer-search demand is not verified — without volume/KD, this decision rests on competition only.",
            "evidence": _evidence(locals()),
        }

    if score >= 3:
        level, label = "GO", "GO — Work on it"
        summary = (f"'{keyword}' is worth working on — the competition is beatable. "
                   "Start with a small test batch of 3-5 listings, then scale up.")
        confidence = "medium"
    elif score >= 0:
        level, label = "TEST", "TEST — Run a small test"
        summary = (f"Do not go all in on '{keyword}' yet — test with 3-5 listings first "
                   "to see whether buyers click and buy, then decide.")
        confidence = "medium"
    else:
        level, label = "SKIP", "SKIP — Not now"
        summary = (f"Skip '{keyword}' for now — the competition is strong. "
                   "Look for a longer (long-tail) version of this niche or a different angle.")
        confidence = "medium"

    if demand_status == "unverified":
        summary += " Note: buyer-search demand is not verified yet — until a volume/KD source is connected, this decision rests on competition evidence only."

    return {
        "verdict": level, "level": level.lower(),
        "label": label, "summary": summary,
        "confidence": confidence, "score": score,
        "reasons": reasons,
        "ranking_levers": _ranking_levers(locals()),
        "demand_status": demand_status,
        "demand_note": ("Buyer-search demand is not verified — until a volume/KD source is connected, "
                        "this decision rests on competition evidence only." if demand_status == "unverified"
                        else "Buyer-search demand source is connected."),
        "evidence": _evidence(locals()),
    }


def _evidence(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_size": ctx["n"],
        "reported_matching_listings": ctx["reported_matching_listings"],
        "strong_title_match": ctx["strong"],
        "strong_title_match_share": ctx["strong_share"],
        "median_shop_lifetime_sales": ctx["med_sales"],
        "median_favorites": ctx["med_favs"],
        "median_shop_reviews": ctx["med_reviews"],
        "median_shop_rating": ctx["med_rating"],
        "median_price": ctx["med_price"],
        "dominant_currency": ctx["dom_currency"],
        "top_category": ctx["top_category"],
        "top_category_share": ctx["top_cat_share"],
        "dominant_listing_type": ctx["dom_type"],
        "dominant_listing_type_share": ctx["dom_type_share"],
        "average_tags": ctx["avg_tags"],
        "under_tagged_share": ctx["under_tag_share"],
        "image_share": ctx["image_share"],
        "personalizable_share": ctx["personalizable_share"],
    }


def _ranking_levers(ctx: dict[str, Any]) -> list[dict[str, str]]:
    """Etsy ranking factors, each mapped to what the sample actually shows."""
    levers: list[dict[str, str]] = []
    strong_share = ctx["strong_share"]
    if strong_share is not None:
        levers.append({
            "lever": "Relevance — exact phrase",
            "why": "Etsy first shows listings whose title, tags and category match the query.",
            "evidence": f"{strong_share:.0%} of sampled listings have a title that strongly matches the keyword.",
            "action": "Put the buyer's exact phrase at the start of the title, in a natural sentence — no keyword stuffing.",
        })
    avg_tags = ctx["avg_tags"]
    if avg_tags is not None:
        levers.append({
            "lever": "Fill all 13 tags",
            "why": "Every empty tag is a search you will not show up in.",
            "evidence": f"Average tags are {avg_tags:.1f}; {ctx['under_tag_share']:.0%} of listings have fewer than 13 tags." if ctx["under_tag_share"] is not None else f"Average tags are {avg_tags:.1f}.",
            "action": "Use all 13 tags, each up to 20 characters, with long-tail phrases. Take seller phrases from the Tag Intelligence table.",
        })
    image_share = ctx["image_share"]
    if image_share is not None:
        levers.append({
            "lever": "First photo (hero image) — CTR",
            "why": "Click-through rate is the biggest part of the listing quality score; the first photo and the price win the click.",
            "evidence": f"{image_share:.0%} of sampled listings have a photo visible in this tool.",
            "action": "Use a clear, bright first photo that shows the product at a glance; make it better than the competing photos.",
        })
    if ctx["med_price"] is not None and ctx["dom_currency"]:
        levers.append({
            "lever": "Price positioning",
            "why": "Price affects both the click and the purchase.",
            "evidence": f"Median price is {ctx['med_price']:,.2f} {ctx['dom_currency']}.",
            "action": "Price near the median — far too cheap looks low quality, far too expensive wins no clicks.",
        })
    if ctx["dom_type"]:
        levers.append({
            "lever": "Listing type",
            "why": "The type that sells most is the type buyers are looking for.",
            "evidence": f"The dominant type in the sample is '{ctx['dom_type']}' ({ctx['dom_type_share']:.0%}).",
            "action": f"Make your listing the dominant type ('{ctx['dom_type']}'), or differentiate clearly with a different type.",
        })
    if ctx["personalizable_share"] is not None and ctx["personalizable_share"] > 0:
        levers.append({
            "lever": "Personalization",
            "why": "Personalized listings engage buyers more.",
            "evidence": f"{ctx['personalizable_share']:.0%} of sampled listings are personalizable.",
            "action": "If the product allows it, offer personalization (name, color, size).",
        })
    if ctx["med_rating"] is not None:
        levers.append({
            "lever": "Shop quality",
            "why": "Review average, response rate and case rate affect the ranking of the whole shop.",
            "evidence": f"Median shop rating is {ctx['med_rating']:.1f}, median reviews are {ctx['med_reviews']:,.0f}." if ctx["med_reviews"] is not None else f"Median shop rating is {ctx['med_rating']:.1f}.",
            "action": "Reply fast, ship on time, protect your ratings — for a new shop, getting the first reviews is the biggest job.",
        })
    levers.append({
        "lever": "Recency + completeness",
        "why": "New listings get a small temporary boost; incomplete listings (few photos, missing attributes) fall behind.",
        "evidence": "Recency and shipping price cannot be seen from the API sample — they are in your control.",
        "action": "Publish only when the title, 13 tags, attributes, photos and return policy are complete. For US buyers, offer free shipping or under $6.",
    })
    return levers
