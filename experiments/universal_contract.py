"""Niche Intelligence Universal Engine v3: lossless research contract (stdlib only).

Not a translation model, country-specific demand estimator, or relevance classifier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "3.0.0-foundation"
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
SCOPES = {"unfiltered", "buyer_shipping_eligible", "shop_location"}


def validate_country(country: str | None) -> str | None:
    if country is None:
        return None
    value = country.strip().upper()
    if not COUNTRY_RE.fullmatch(value):
        raise ValueError("Country must be a 2-letter ISO country code, e.g. US, DE, FR")
    return value


def parse_record(raw: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"Listing {index} must be an object")
    listing_id = raw.get("listing_id")
    if listing_id is None:
        raise ValueError(f"Listing {index} is missing listing_id")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Listing {index} has no usable original title")
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError(f"Listing {index} tags must be strings")
    currency = raw.get("currency")
    if currency is not None and (not isinstance(currency, str) or not CURRENCY_RE.fullmatch(currency.upper())):
        raise ValueError(f"Listing {index} has invalid currency")
    return {
        "listing_id": listing_id,
        "source": "Etsy Open API v3",
        "original": {
            "title": title,
            "tags": tags,
            "currency": currency,
            "price": raw.get("price"),
            "listing_url": raw.get("listing_url"),
        },
        "language": {
            "detected": None,
            "detection_method": "not_run",
            "confidence": None,
            "translated_title": None,
        },
        "product": {
            "type": None,
            "subtype": None,
            "format": None,
            "material": None,
            "intended_recipient": None,
            "buyer_intent": None,
            "attributes_extraction_status": "pending",
        },
        "relevance": {
            "label": "not_evaluated",
            "confidence": None,
            "evidence": [],
            "model_version": None,
        },
        "seller_signals": {
            "shop_id": raw.get("shop_id"),
            "shop_name": raw.get("shop_name"),
            "shop_total_sales": raw.get("shop_total_sales"),
            "shop_review_count": raw.get("shop_review_count"),
            "shop_rating": raw.get("shop_rating"),
            "shop_age_years": raw.get("shop_age_years"),
            "listing_favorites": raw.get("favorites"),
            "shop_data_available": raw.get("shop_data_available"),
        },
        "sales_disclaimer": "shop_total_sales is cumulative SHOP sales, never listing sales",
    }


def normalize(data: dict, *, country: str | None, scope: str) -> dict:
    if scope not in SCOPES:
        raise ValueError(f"Invalid scope {scope!r}")
    country = validate_country(country)
    if scope != "unfiltered" and not country:
        raise ValueError("Country required for country-restricted scope")
    listings = data.get("listings")
    if not isinstance(listings, list):
        raise ValueError("Input JSON must include listings array")
    records = [parse_record(raw, i) for i, raw in enumerate(listings)]
    ids = [str(x["listing_id"]) for x in records]
    duplicates = len(ids) - len(set(ids))
    if duplicates:
        raise ValueError(f"Input contains {duplicates} duplicate listing IDs; resolve first")
    keyword = data.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError("Input keyword missing")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_metadata": {
            "source": data.get("source"),
            "keyword_original": keyword,
            "collected_at": data.get("collected_at"),
            "reported_matching_listings": data.get("reported_matching_listings"),
            "reported_matching_listings_definition": "Etsy API reported listing matches, NOT buyer search volume",
            "input_count": len(records),
            "unique_shops_reported": data.get("unique_shops"),
        },
        "market": {
            "target_country": country,
            "country_scope_requested": scope,
            "country_filter_verified_applied_at_collection": False,
            "country_scope_note": "Country here is research metadata. The source dataset was already collected; this script does not re-fetch or geo-filter it.",
            "buyer_search_volume_by_country": None,
            "buyer_search_volume_status": "not_available",
            "currency_conversion_performed": False,
        },
        "pipeline": {
            "language_detection": "pending",
            "multilingual_embeddings": "pending",
            "product_attribute_extraction": "pending",
            "relevance_classification": "pending",
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Niche Intelligence v3 lossless data contract and validation")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--country", help="Two-letter target country, metadata ONLY until Etsy API filtering is implemented")
    parser.add_argument("--scope", choices=sorted(SCOPES), default="unfiltered")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raw_bytes = args.input.read_bytes()
    raw = json.loads(raw_bytes.decode("utf-8-sig"))
    result = normalize(raw, country=args.country, scope=args.scope)
    result["source_metadata"]["input_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    out = args.output or args.input.with_name(args.input.stem + "_v3_contract.json")
    if out.resolve() == args.input.resolve():
        parser.error("Output must differ from input (never overwrite the collected data)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"PASS: {len(result['records'])} records validated; 0 duplicate IDs")
    print(f"Source keyword: {result['source_metadata']['keyword_original']}")
    print(f"Country metadata: {result['market']['target_country'] or 'not selected'} (NOT an applied API filter)")
    print("Language / attributes / relevance: pending (no classification claims)")
    print(f"Output: {out}")


if __name__ == "__main__":
    main()
