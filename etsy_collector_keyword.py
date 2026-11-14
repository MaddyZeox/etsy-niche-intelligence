"""Niche Intelligence — 100-listing Etsy collector with bounded SQLite shop cache.

Run: python etsy_collector.py "minimalist wedding invitation"
Credentials come from environment variables first (ETSY_KEYSTRING and
ETSY_SHARED_SECRET — used by hosted deploys), then fall back to Windows
Credential Manager via `keyring` (local PC use).
"""

import argparse
import hashlib
import os
import re
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import copy
from shop_enrichment_v49 import plan_enrichment, shop_coverage, attach_shop_data, shop_ids_in_order
from etsy_category_taxonomy import load_taxonomy, annotate_categories, category_summary

import keyring

# Environment-variable names for hosted deploys. Env wins; keyring is the
# fallback for local PC use. Never log or print these values.
_CREDENTIAL_ENV = {"keystring": "ETSY_KEYSTRING", "shared_secret": "ETSY_SHARED_SECRET"}


def _credential(username):
    """Environment first (hosted deploys), keyring second (local PC).
    Returns None instead of raising (no backend, etc.)."""
    env_name = _CREDENTIAL_ENV.get(username)
    if env_name:
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip()
    try:
        return keyring.get_password(SERVICE, username)
    except Exception:
        return None


BASE = "https://api.etsy.com/v3/application"
SERVICE = "NicheIntelligence-Etsy"
PAGE_SIZE = 50
PAGES = 2
WORKERS = 4
BATCH_SHOP_LISTINGS = 10  # verified cross-shop count on this Etsy application; scale only after live tests
REQUEST_INTERVAL_SECONDS = 0.26  # conservative fallback until Etsy confirms QPS via headers
CONFIRMED_QPS_CEILING = 5  # never exceed verified application limit
QPS_SAFETY_MULTIPLIER = 1.10  # 10% headroom: 5 QPS -> 0.22s spacing
MAX_RETRIES = 3
CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_MAX_BYTES = 100 * 1024 * 1024
DIR = Path(__file__).resolve().parent
DB_FILE = DIR / "etsy_shop_cache.sqlite3"
OUTPUT_DIR = DIR / "research_results"

_api_lock = threading.Lock()
_next_request = 0.0
_api_requests = 0
_effective_interval_seconds = REQUEST_INTERVAL_SECONDS
_reported_qps = None


def observe_rate_limit_headers(response_headers):
    """Apply the API-provided QPS cap across all worker threads. No blind speedup."""
    global _effective_interval_seconds, _reported_qps
    raw = response_headers.get("x-limit-per-second") if response_headers else None
    try:
        qps = int(raw)
    except (TypeError, ValueError):
        return
    if qps <= 0:
        return
    # Cap locally to the 5 QPS observed for this user's application, even if
    # a response advertises more. Lower server limits slow us down.
    safe_qps = min(qps, CONFIRMED_QPS_CEILING)
    safe_interval = QPS_SAFETY_MULTIPLIER / safe_qps
    with _api_lock:
        _reported_qps = qps
        _effective_interval_seconds = safe_interval


def request_slot():
    global _next_request, _api_requests
    with _api_lock:
        now = time.monotonic()
        scheduled = max(now, _next_request)
        _next_request = scheduled + _effective_interval_seconds
        _api_requests += 1
    if scheduled > now:
        time.sleep(scheduled - now)


def fetch_json(path, headers):
    for attempt in range(MAX_RETRIES):
        request_slot()
        req = urllib.request.Request(BASE + path, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                observe_rate_limit_headers(response.headers)
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES - 1:
                raise
            retry_after = exc.headers.get("Retry-After")
            try:
                pause = min(60, max(0, float(retry_after))) if retry_after else 2 ** attempt
            except (ValueError, TypeError):
                pause = 2 ** attempt
            time.sleep(pause)
        except (TimeoutError, OSError):
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(2 ** attempt)


def init_cache(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS shops (
        shop_id INTEGER PRIMARY KEY,
        payload TEXT NOT NULL,
        fetched_at INTEGER NOT NULL,
        last_used INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shops_last_used ON shops(last_used)")
    conn.commit()


def read_cached(conn, shop_ids):
    """Only use unexpired entries; update their access time for LRU cleanup."""
    if not shop_ids:
        return {}
    now = int(time.time())
    placeholders = ",".join("?" for _ in shop_ids)
    rows = conn.execute(
        f"SELECT shop_id, payload FROM shops WHERE shop_id IN ({placeholders}) "
        "AND fetched_at >= ?",
        [*shop_ids, now - CACHE_TTL_SECONDS],
    ).fetchall()
    result = {}
    for shop_id, payload in rows:
        try:
            result[shop_id] = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
    if result:
        conn.executemany(
            "UPDATE shops SET last_used = ? WHERE shop_id = ?",
            [(now, shop_id) for shop_id in result],
        )
        conn.commit()
    return result


def store_shops(conn, shops):
    now = int(time.time())
    conn.executemany(
        """INSERT INTO shops(shop_id, payload, fetched_at, last_used)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(shop_id) DO UPDATE SET
            payload=excluded.payload,
            fetched_at=excluded.fetched_at,
            last_used=excluded.last_used""",
        [(sid, json.dumps(details, ensure_ascii=False), now, now) for sid, details in shops.items()],
    )
    conn.commit()


def tidy_cache(conn):
    """Remove stale entries and least-recently-used entries until file <= 100 MiB."""
    now = int(time.time())
    removed = conn.execute("DELETE FROM shops WHERE fetched_at < ?", (now - CACHE_TTL_SECONDS,)).rowcount
    conn.commit()
    # VACUUM rewrites the database and is expensive on every search. Only
    # compact when rows have expired or the cache exceeds its size bound.
    if removed:
        conn.execute("VACUUM")
    while DB_FILE.exists() and DB_FILE.stat().st_size > CACHE_MAX_BYTES:
        ids = conn.execute(
            "SELECT shop_id FROM shops ORDER BY last_used ASC LIMIT 200"
        ).fetchall()
        if not ids:
            break
        conn.executemany("DELETE FROM shops WHERE shop_id = ?", ids)
        conn.commit()
        conn.execute("VACUUM")


def money_value(raw):
    if not isinstance(raw, dict):
        return None
    try:
        amount, divisor = raw.get("amount"), raw.get("divisor")
        return round(int(amount) / int(divisor), 2) if amount is not None and int(divisor) else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def age_years(timestamp):
    if timestamp is None:
        return None
    try:
        created = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        return round((datetime.now(timezone.utc) - created).total_seconds() / (365.25 * 86400), 2)
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def _primary_image(listing):
    """Primary image from an `includes=Images` payload, in rank order.

    Returns (url_570xN, url_fullxfull, image_count, image_source).
    """
    images = listing.get("images")
    if not isinstance(images, list) or not images:
        return None, None, 0, "missing"
    ranked = sorted(
        (img for img in images if isinstance(img, dict)),
        key=lambda img: img.get("rank") if type(img.get("rank")) is int else 999,
    )
    if not ranked:
        return None, None, 0, "missing"
    first = ranked[0]
    return first.get("url_570xN"), first.get("url_fullxfull"), len(images), "includes"


def _listing_type_value(listing):
    raw = listing.get("listing_type") or listing.get("type")
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if value in ("download", "digital"):
        return "digital"
    if value in ("physical", "both"):
        return value
    return None


def _listing_record(listing, shop=None):
    """One stored listing row: listing fields + shop fields + image + type.

    Shop fields are None/False when no shop profile is attached yet
    (preview / quick mode); assemble_with_shops fills them in later.
    """
    shop = shop or {}
    raw_price = listing.get("price")
    image_url, image_large, image_count, image_source = _primary_image(listing)
    materials = listing.get("materials")
    return {
        "listing_id": listing.get("listing_id"),
        "title": listing.get("title"),
        "tags": listing.get("tags") or [],
        "taxonomy_id": listing.get("taxonomy_id"),
        "category_name": None, "category_path": None,
        "price": money_value(raw_price),
        "currency": raw_price.get("currency_code") if isinstance(raw_price, dict) else None,
        "favorites": listing.get("num_favorers"),
        "listing_url": listing.get("url"),
        "shop_id": listing.get("shop_id"),
        "shop_name": shop.get("shop_name"),
        "shop_total_sales": shop.get("transaction_sold_count"),
        "shop_created_timestamp": shop.get("created_timestamp"),
        "shop_age_years": age_years(shop.get("created_timestamp")),
        "shop_review_count": shop.get("review_count"),
        "shop_rating": shop.get("review_average"),
        "shop_data_available": bool(shop),
        "listing_type": _listing_type_value(listing),
        "is_supply": listing.get("is_supply"),
        "who_made": listing.get("who_made"),
        "when_made": listing.get("when_made"),
        "materials": [m for m in materials if isinstance(m, str)][:5] if isinstance(materials, list) else None,
        "is_personalizable": listing.get("is_personalizable"),
        "is_customizable": listing.get("is_customizable"),
        "quantity": listing.get("quantity"),
        "created_timestamp": listing.get("created_timestamp"),
        "image_url": image_url,
        "image_url_large": image_large,
        "image_count": image_count,
        "image_source": image_source,
    }


def fetch_listing_image(listing_id, headers):
    """Primary image for one listing, via GET /listings/{id}/images.

    Used ONLY as a lazy fallback when `includes=Images` returned nothing
    for that listing, so the main search stays fast. Returns a dict or None.
    """
    listing_id = _positive_id(listing_id)
    if listing_id is None:
        return None
    try:
        payload = fetch_json(f"/listings/{listing_id}/images", headers)
    except Exception:
        return None
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        return None
    ranked = sorted(
        (img for img in results if isinstance(img, dict)),
        key=lambda img: img.get("rank") if type(img.get("rank")) is int else 999,
    )
    if not ranked:
        return None
    first = ranked[0]
    url = first.get("url_570xN")
    if not url:
        return None
    return {
        "listing_id": listing_id,
        "image_url": url,
        "image_url_large": first.get("url_fullxfull"),
        "image_count": len(results),
        "image_source": "images_endpoint",
    }


def shop_request(shop_id, headers):
    try:
        details = fetch_json(f"/shops/{shop_id}", headers)
        return shop_id, details, None
    except urllib.error.HTTPError as exc:
        return shop_id, None, f"HTTP {exc.code}"
    except Exception as exc:
        return shop_id, None, type(exc).__name__


SHOP_PROFILE_FIELDS = (
    "shop_id", "shop_name", "transaction_sold_count", "created_timestamp",
    "review_count", "review_average",
)
SHOP_REQUIRED_METRICS = (
    "transaction_sold_count", "created_timestamp", "review_count", "review_average",
)


def _positive_id(value):
    """Accept native positive integer IDs, not bool, float, or malformed text."""
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and value == str(int(value)) and int(value) > 0:
        return int(value)
    return None


def shop_listing_pairs(listings, needed_ids):
    """One real listing ID per missing shop, preserving listing search order."""
    required = set(needed_ids)
    pairs = []
    seen_shops = set()
    for row in listings:
        if not isinstance(row, dict):
            continue
        sid, lid = _positive_id(row.get("shop_id")), _positive_id(row.get("listing_id"))
        if sid in required and sid not in seen_shops and lid is not None:
            pairs.append((lid, sid))
            seen_shops.add(sid)
    return pairs


def parse_batch_shop_response(payload, pairs):
    """Never attribute a shop's metrics without matching BOTH IDs and required fields."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return {}
    expected = dict(pairs)
    profiles = {}
    for row in payload["results"]:
        if not isinstance(row, dict):
            continue
        lid = _positive_id(row.get("listing_id"))
        sid = _positive_id(row.get("shop_id"))
        if lid is None or sid is None or expected.get(lid) != sid or sid in profiles:
            continue
        shop = row.get("Shop")
        if not isinstance(shop, dict):
            shop = row.get("shop")
        if not isinstance(shop, dict) or _positive_id(shop.get("shop_id")) != sid:
            continue
        if not all(shop.get(field) is not None for field in SHOP_REQUIRED_METRICS):
            continue
        profiles[sid] = {field: shop.get(field) for field in SHOP_PROFILE_FIELDS}
    return profiles


def retrieve_missing_shops(listings, missing_shop_ids, headers):
    """Use validated mixed-shop Etsy listing batches; fall back for any missing profile.

    Batches use the same shared request limiter/retry handler as all other Etsy calls.
    Batch failures do not create fake profile records and cannot cross shop IDs.
    """
    requested = list(dict.fromkeys(missing_shop_ids))
    pairs = shop_listing_pairs(listings, requested)
    retrieved = {}
    failed_batches = 0
    batch_requests = 0
    # A single missing shop needs just one request either way; keep legacy lookup.
    # This also avoids batch overhead for the common one-shop case.
    batch_pairs = pairs if len(pairs) >= 2 else []
    for offset in range(0, len(batch_pairs), BATCH_SHOP_LISTINGS):
        batch = batch_pairs[offset:offset + BATCH_SHOP_LISTINGS]
        query = urllib.parse.urlencode({
            "listing_ids": ",".join(str(lid) for lid, _ in batch),
            "includes": "Shop",
        }, safe=",")
        batch_requests += 1
        try:
            response = fetch_json("/listings/batch?" + query, headers)
            retrieved.update(parse_batch_shop_response(response, batch))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 401):
                raise  # Do not amplify quota/authentication failure with fallback requests.
            failed_batches += 1
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            failed_batches += 1
            # Keep the fallback; do not print raw errors, remote responses or secrets.
    unresolved = [sid for sid in requested if sid not in retrieved]
    errors = {}
    if unresolved:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = [executor.submit(shop_request, sid, headers) for sid in unresolved]
            for future in as_completed(futures):
                sid, shop, error = future.result()
                if error:
                    errors[str(sid)] = error
                elif (isinstance(shop, dict) and bool(shop)
                      and (shop.get("shop_id") is None or _positive_id(shop.get("shop_id")) == sid)):
                    retrieved[sid] = {field: shop.get(field) for field in SHOP_PROFILE_FIELDS}
                else:
                    errors[str(sid)] = "missing_or_mismatched_shop_metrics"
    return retrieved, errors, {
        "batch_requests": batch_requests,
        "batch_failed_requests": failed_batches,
        "individual_shop_requests": len(unresolved),
        "batch_shops_resolved": len(requested) - len(unresolved),
    }


MARKET_CURRENCY = {
    "US": "USD", "GB": "GBP", "CA": "CAD", "AU": "AUD", "NZ": "NZD",
    "DE": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR", "NL": "EUR",
    "BE": "EUR", "AT": "EUR", "FI": "EUR", "IE": "EUR", "PT": "EUR",
    "CH": "CHF", "SE": "SEK", "DK": "DKK", "NO": "NOK", "PL": "PLN",
    "JP": "JPY", "IN": "INR", "PK": "PKR", "MX": "MXN", "BR": "BRL",
    "TR": "TRY", "AE": "AED", "SG": "SGD",
}


def research_filename(keyword, buyer_country=None, display_currency=None):
    """Stable safe filename; hash prevents slug collisions."""
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.casefold()).strip("-")[:65].strip("-")
    digest = hashlib.sha256(keyword.casefold().encode("utf-8")).hexdigest()[:8]
    market = f"_{buyer_country.upper()}" if buyer_country else ""
    currency_suffix = f"_{display_currency.upper()}" if display_currency else ""
    return f"etsy_{slug or 'keyword'}_{digest}{market}{currency_suffix}.json"


def result_file_path(keyword, buyer_country=None, mode="full"):
    currency = MARKET_CURRENCY.get(buyer_country) if buyer_country else None
    filename = research_filename(keyword, buyer_country, f"native-{currency}" if currency else None)
    path = OUTPUT_DIR / filename
    return path.with_name(path.stem + "_QUICK.json") if mode == "quick" else path


def assemble_with_shops(records, profiles):
    updated = attach_shop_data(records, profiles)
    for row in updated:
        row["shop_age_years"] = age_years(row.get("shop_created_timestamp"))
    return updated


def enrich_from_quick(keyword, buyer_country, mode, selected_shop_ids, run_id, output_path):
    """Upgrade a validated recent Quick report, without refetching its listings."""
    if mode not in ("selected", "full"):
        raise ValueError("Invalid enrichment mode")
    quick_path = result_file_path(keyword, buyer_country, "quick")
    source = json.loads(quick_path.read_text(encoding="utf-8"))
    currency = MARKET_CURRENCY.get(buyer_country) if buyer_country else None
    if (source.get("source") != "Etsy Open API v3"
        or source.get("keyword") != keyword
        or source.get("buyer_country") != buyer_country
        or source.get("listing_currency_filter") != currency
        or source.get("currency_filter_mode") != ("original_listing_currency" if currency else "none")
        or source.get("enrichment_mode") != "quick"
        or source.get("stage") != "quick_complete"
        or not isinstance(source.get("listings"),list)
        or any(not isinstance(row,dict) or (currency and row.get("currency") != currency) for row in source["listings"])):
        raise ValueError("Quick report market or currency mismatch")
    if not isinstance(source.get("collection_run_id"),str) or not re.fullmatch(r"[a-f0-9]{32}",source["collection_run_id"]):
        raise ValueError("Quick report receipt invalid")
    collected = datetime.fromisoformat(source["collected_at"].replace("Z","+00:00"))
    if collected.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-collected).total_seconds() <= 900:
        raise ValueError("Quick report expired")
    if currency:
        a=source.get("sample_before_currency_filter"); b=source.get("excluded_currency_count")
        if type(a) is not int or type(b) is not int or a != len(source["listings"])+b:
            raise ValueError("Currency cohort counts invalid")
    listings=source["listings"]
    start=time.monotonic()
    conn=sqlite3.connect(DB_FILE)
    try:
        init_cache(conn)
        cached=read_cached(conn, shop_ids_in_order(listings))
        plan=plan_enrichment(listings,cached,mode,selected_shop_ids)
        if mode=="selected" and not plan.fetch_ids and not plan.cached_ids:
            raise ValueError("No shops selected")
        retrieved={}; errors={}
        batch_stats={"batch_requests": 0, "batch_failed_requests": 0,
                     "individual_shop_requests": 0, "batch_shops_resolved": 0}
        if plan.fetch_ids:
            key=_credential("keystring")
            secret=_credential("shared_secret")
            if not key or not secret:
                print("COLLECTION_ERROR: credentials_missing")
                raise SystemExit(1)
            headers={"x-api-key":f"{key}:{secret}","Accept":"application/json"}
            retrieved, errors, batch_stats = retrieve_missing_shops(listings, plan.fetch_ids, headers)
        if retrieved:store_shops(conn,retrieved)
        tidy_cache(conn)
        profiles={**cached,**retrieved}
    finally:
        conn.close()
    result=copy.deepcopy(source)
    result["collection_run_id"]=run_id
    result["collected_at"]=datetime.now(timezone.utc).isoformat()
    result["listings"]=assemble_with_shops(listings,profiles)
    if not all(r.get("category_path") for r in result["listings"] if r.get("taxonomy_id")):
        if "headers" not in locals():
            key=_credential("keystring")
            secret=_credential("shared_secret")
            headers={"x-api-key":f"{key}:{secret}","Accept":"application/json"} if key and secret else None
        taxonomy, taxonomy_source = load_taxonomy(fetch_json, headers, DIR / "etsy_seller_taxonomy_cache.json") if headers else ({}, "unavailable")
        annotate_categories(result["listings"], taxonomy)
        result["category_data_source"] = taxonomy_source
        result["category_resolved_listings"] = sum(bool(r.get("category_path")) for r in result["listings"])
        result["category_distribution"] = category_summary(result["listings"])
    result["enrichment_mode"]=mode
    result["stage"]="full_complete" if mode=="full" else "selected_complete"
    result.update(shop_coverage(result["listings"],profiles))
    result["shop_cache_hits"]=len(cached)
    result["shop_api_fetches"]=batch_stats["individual_shop_requests"]
    result["shop_batch_requests"]=batch_stats["batch_requests"]
    result["shop_batch_resolved"]=batch_stats["batch_shops_resolved"]
    result["shop_batch_failed_requests"]=batch_stats["batch_failed_requests"]
    result["shop_fetch_errors"]=errors
    result["total_api_requests"]=_api_requests
    result["enrichment_elapsed_seconds"]=round(time.monotonic()-start,3)
    result["notes"]=(source.get("notes") or "") + " Partial shop records contain null values, not zeros."
    target=Path(output_path)
    # Backend validates the result before promoting a Full upgrade into the
    # stable Full Research cache. Avoid accepting unverified output.
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(target)
    print("ENRICHMENT COMPLETE",mode,"batch requests",batch_stats["batch_requests"],
          "individual requests",batch_stats["individual_shop_requests"],
          "seconds",result["enrichment_elapsed_seconds"])


def main(keyword, pages=2, buyer_country=None, run_id=None, preview_path=None, mode="full"):
    if mode not in ("full","quick"):
        raise ValueError("Invalid research mode")
    # Strict native-price currency cohort, not currency conversion.
    native_currency = MARKET_CURRENCY.get(buyer_country) if buyer_country else None
    display_currency = f"native-{native_currency}" if native_currency else None
    start = time.monotonic()
    key = _credential("keystring")
    secret = _credential("shared_secret")
    if not key or not secret:
        print("COLLECTION_ERROR: credentials_missing")
        raise SystemExit(1)
    headers = {"x-api-key": f"{key}:{secret}", "Accept": "application/json"}
    print("Niche Intelligence — Keyword Etsy Collector")
    print("Keyword:", keyword)
    print("Buyer country:", buyer_country or "Global / no country filter")
    print("Native listing currency filter:", native_currency or "All original listing currencies")

    # Fetch only the first two pages; pagination doesn't mean all matches are relevant.
    seen = set()
    listings = []
    reported_count = None
    listing_api_seconds = 0.0
    # v1.6: fetch listing pages in parallel (the shared rate limiter still
    # applies) and ask Etsy to include primary images in the same call —
    # zero extra requests for photos. If the API rejects includes=Images,
    # retry plain and let the lazy image endpoint fill photos on demand.
    # Note: both pages are fetched up front, so a short first page costs
    # one extra call; the parallel fetch saves a full round-trip otherwise.
    def _fetch_listing_page(page, with_images):
        offset = page * PAGE_SIZE
        params = {"keywords": keyword, "limit": PAGE_SIZE, "offset": offset, "sort_on": "score"}
        if buyer_country:
            params["buyer_country"] = buyer_country
        if with_images:
            params["includes"] = "Images"
        # Never request Etsy price conversion: we need original currency codes.
        started = time.monotonic()
        response = fetch_json("/listings/active?" + urllib.parse.urlencode(params), headers)
        return page, time.monotonic() - started, response

    page_responses = []
    try:
        with ThreadPoolExecutor(max_workers=min(pages, 2)) as executor:
            page_responses = list(executor.map(lambda p: _fetch_listing_page(p, True), range(pages)))
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        print("IMAGES_INCLUDE_UNSUPPORTED: retrying listing pages without includes", flush=True)
        with ThreadPoolExecutor(max_workers=min(pages, 2)) as executor:
            page_responses = list(executor.map(lambda p: _fetch_listing_page(p, False), range(pages)))
    for page, page_seconds, response in sorted(page_responses):
        listing_api_seconds += page_seconds
        print(f"TIMING listing_page_{page + 1}_seconds: {page_seconds:.3f}", flush=True)
        reported_count = response.get("count", reported_count)
        page_records = response.get("results") or []
        new_count = 0
        for listing in page_records:
            listing_id = listing.get("listing_id")
            if listing_id is None or listing_id in seen:
                continue
            seen.add(listing_id)
            listings.append(listing)
            new_count += 1
        print(f"Page {page + 1}/{pages}: received {len(page_records)}, new unique {new_count}")
        # No early break: pages were already fetched in parallel, so a short
        # first page must not discard the second page's records. Dedupe above
        # keeps this safe, and a short page normally means page 2 is empty.

    print(f"TIMING listing_api_total_seconds: {listing_api_seconds:.3f}", flush=True)

    # Exclude other currency listings BEFORE shop enrichment and analysis.
    # A US-dollar listing can still be owned by a non-US shop.
    listings_before_currency_filter = len(listings)
    if native_currency:
        listings = [item for item in listings if isinstance(item.get("price"), dict)
                    and str(item["price"].get("currency_code", "")).upper() == native_currency]
    excluded_currency_count = listings_before_currency_filter - len(listings)
    print(f"Currency cohort: {len(listings)} kept, {excluded_currency_count} excluded")

    shop_ids = list(dict.fromkeys(
        listing.get("shop_id") for listing in listings if listing.get("shop_id") is not None
    ))

    # Write a verified, temporary LISTING-ONLY snapshot before slow shop enrichment.
    # This is never stored as a completed research report or offered by the 15m cache.
    if preview_path is not None:
        preview_started = time.monotonic()
        preview_records = [_listing_record(listing) for listing in listings]
        preview_result = {
            "source": "Etsy Open API v3", "stage": "listings_only",
            "collection_run_id": run_id, "keyword": keyword,
            "buyer_country": buyer_country, "listing_currency_filter": native_currency,
            "currency_filter_mode": "original_listing_currency" if native_currency else "none",
            "sample_before_currency_filter": listings_before_currency_filter,
            "excluded_currency_count": excluded_currency_count,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "reported_matching_listings": reported_count,
            "unique_shops": len(shop_ids), "shop_cache_hits": None,
            "shop_api_fetches": None, "shop_fetch_errors": None,
            "total_api_requests": _api_requests, "listings": preview_records,
        }
        preview_file = Path(preview_path)
        preview_file.parent.mkdir(parents=True, exist_ok=True)
        temp_preview = preview_file.with_suffix(".tmp")

    db_started = time.monotonic()
    conn = sqlite3.connect(DB_FILE)
    try:
        init_cache(conn)
        cached = read_cached(conn, shop_ids)
        # Publish the preview ONCE, after reading the existing shop cache.
        # Publishing an empty preview first races the browser's first poll,
        # so cached shop details might otherwise never appear until completion.
        if preview_path is not None:
            cached_preview = assemble_with_shops(preview_records, cached)
            preview_result["listings"] = cached_preview
            preview_result["shop_cache_hits"] = len(cached)
            preview_result.update(shop_coverage(cached_preview, cached))
            temp_preview.write_text(json.dumps(preview_result, ensure_ascii=False), encoding="utf-8")
            temp_preview.replace(preview_file)
            print("PREVIEW_READY", flush=True)
            print(f"TIMING preview_write_seconds: {time.monotonic() - preview_started:.3f}", flush=True)
            print(f"TIMING listings_ready_elapsed_seconds: {time.monotonic() - start:.3f}", flush=True)
        print(f"TIMING sqlite_cache_read_seconds: {time.monotonic() - db_started:.3f}", flush=True)
        missing = [shop_id for shop_id in shop_ids if shop_id not in cached]
        print(f"Shops: {len(shop_ids)} | cache hits: {len(cached)} | API shop fetches: {len(missing)}")
        plan = plan_enrichment(listings, cached, mode)
        retrieved = {}
        shop_errors = {}
        enrichment_started = time.monotonic()
        batch_stats = {"batch_requests": 0, "batch_failed_requests": 0,
                       "individual_shop_requests": 0, "batch_shops_resolved": 0}
        if plan.fetch_ids:
            retrieved, shop_errors, batch_stats = retrieve_missing_shops(listings, plan.fetch_ids, headers)
        print(f"TIMING shop_enrichment_seconds: {time.monotonic() - enrichment_started:.3f}", flush=True)
        db_write_started = time.monotonic()
        if retrieved:
            store_shops(conn, retrieved)
        tidy_cache(conn)
        print(f"TIMING sqlite_write_cleanup_seconds: {time.monotonic() - db_write_started:.3f}", flush=True)
        shops = {**cached, **retrieved}
    finally:
        conn.close()

    finalize_started = time.monotonic()
    records = []
    for listing in listings:
        sid = listing.get("shop_id")
        shop = shops.get(sid, {})
        records.append(_listing_record(listing, shop))

    taxonomy, taxonomy_source = load_taxonomy(fetch_json, headers, DIR / "etsy_seller_taxonomy_cache.json")
    annotate_categories(records, taxonomy)
    result = {
        "source": "Etsy Open API v3",
        "enrichment_mode": mode,
        "stage": "quick_complete" if mode == "quick" else "full_complete",
        "collection_run_id": run_id,
        "keyword": keyword,
        "buyer_country": buyer_country,
        "requested_display_currency": None,
        "listing_currency_filter": native_currency,
        "currency_filter_mode": "original_listing_currency" if native_currency else "none",
        "sample_before_currency_filter": listings_before_currency_filter,
        "excluded_currency_count": excluded_currency_count,
        "market_filter": "buyer_country" if buyer_country else "not_applied",
        "market_note": (f"Listings eligible to ship to {buyer_country}, with original listing currency {native_currency}; this does not mean shops are based in {buyer_country}." if buyer_country else "No buyer-country filter applied; not exhaustive worldwide coverage."),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "reported_matching_listings": reported_count,
        "sample_size": len(records),
        "category_data_source": taxonomy_source,
        "category_resolved_listings": sum(bool(r.get("category_path")) for r in records),
        "category_distribution": category_summary(records),
        "unique_shops": len(shop_ids),
        "shop_cache_hits": len(cached),
        "shop_api_fetches": batch_stats["individual_shop_requests"],
        "shop_batch_requests": batch_stats["batch_requests"],
        "shop_batch_resolved": batch_stats["batch_shops_resolved"],
        "shop_batch_failed_requests": batch_stats["batch_failed_requests"],
        "shop_fetch_errors": shop_errors,
        "total_api_requests": _api_requests,
        "cache_ttl_hours": 24,
        "notes": "Shop sales are shop-wide totals. Original listing currency codes are used to filter listings; no conversion. Reported matching count is before currency filtering and is not buyer searches or currency-specific competition.",
        "listings": records,
    }
    result.update(shop_coverage(records, shops))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = result_file_path(keyword, buyer_country, mode)
    # Atomic replacement of only this keyword's report. Other keywords remain intact.
    temp_file = output_file.with_suffix(".json.tmp")
    temp_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_file.replace(output_file)
    print(f"TIMING finalize_report_seconds: {time.monotonic() - finalize_started:.3f}", flush=True)
    print(f"TIMING full_report_elapsed_seconds: {time.monotonic() - start:.3f}", flush=True)
    print("\nCOLLECTION COMPLETE")
    print("Listings collected:", len(records))
    print("Unique shops:", len(shop_ids))
    print("Cache hits:", len(cached))
    print("Individual shop API requests:", batch_stats["individual_shop_requests"])
    print("Batch shop API requests:", batch_stats["batch_requests"])
    print("Shops resolved by batch:", batch_stats["batch_shops_resolved"])
    print("Shop fetch errors:", len(shop_errors))
    print("Total API requests:", _api_requests)
    print("Rate limit QPS header:", _reported_qps if _reported_qps is not None else "not provided")
    print("Effective API request spacing seconds:", round(_effective_interval_seconds, 3))
    print("Elapsed seconds:", round(time.monotonic() - start, 2))
    print("SQLite size KB:", round(DB_FILE.stat().st_size / 1024, 1))
    print("Saved file:", output_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect real Etsy listings for a user-supplied keyword")
    parser.add_argument("keyword", help='Keyword in quotes, e.g. "minimalist wedding invitation"')
    parser.add_argument("--pages", type=int, choices=(1, 2, 3, 4), default=2,
                        help="Pages of up to 50 listings; default 2 (100 records max)")
    parser.add_argument("--buyer-country", type=str.upper, default=None,
                        help="ISO 3166-1 alpha-2 destination country (e.g. US, GB, DE); leave out for Global")
    parser.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--preview-path", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("quick","full","selected"), default="full")
    parser.add_argument("--enrich-from-quick", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--selected-shop-ids", default="", help=argparse.SUPPRESS)
    parser.add_argument("--output-path", default="", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.buyer_country and not re.fullmatch(r"[A-Z]{2}", options.buyer_country):
        parser.error("--buyer-country must be a two-letter country code")
    if not options.keyword.strip():
        parser.error("Keyword cannot be empty")
    if options.run_id is not None and not re.fullmatch(r"[0-9a-f]{32}", options.run_id):
        parser.error("Invalid internal run identifier")
    try:
        if options.enrich_from_quick:
            if not options.run_id or not options.output_path:
                parser.error("Internal enrichment arguments missing")
            selected = [int(i) for i in options.selected_shop_ids.split(",") if i]
            enrich_from_quick(options.keyword.strip(),options.buyer_country,
                              options.mode, selected, options.run_id, options.output_path)
        else:
            main(options.keyword.strip(), pages=options.pages,
                 buyer_country=options.buyer_country, run_id=options.run_id,
                 preview_path=options.preview_path, mode=options.mode)
    except urllib.error.HTTPError as exc:
        print(f"COLLECTION_ERROR: HTTP {exc.code}")
        raise SystemExit(1)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print("COLLECTION_ERROR: network_error")
        raise SystemExit(1)
    except Exception as exc:
        # Never print arbitrary exception text. Some libraries embed secrets
        # or remote URLs in error messages.
        print("COLLECTION_ERROR: unexpected_error")
        raise SystemExit(1)
