
from fastapi import FastAPI, Query

app = FastAPI(
    title="Niche Intelligence API",
    version="1.7.0",
    description="Backend for keyword and competitor research"
)

@app.get("/")
def home():
    return {
        "app": "Niche Intelligence",
        "version": "1.7.0",
        "status": "running",
        "etsy_api": "collector_integration_available"
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "backend",
        "etsy_connected": "not_checked"
    }

@app.get("/keywords/analyze")
def analyze_keyword(
    keyword: str = Query(
        ...,
        min_length=2,
        max_length=100,
        description="Keyword to research"
    ),
    market: str = Query(
        default="US",
        description="Target country code"
    )
):
    cleaned_keyword = " ".join(keyword.strip().lower().split())

    return {
        "keyword": cleaned_keyword,
        "market": market.upper(),
        "status": "legacy_placeholder",
        "etsy_search_volume": None,
        "google_search_volume": None,
        "competition": None,
        "keyword_difficulty": None,
        "data_source": None,
        "message": "Use POST /etsy/search for local live Etsy collector research. Country filtering is not yet implemented."
    }

from pydantic import BaseModel, Field
from typing import Optional, Literal


class CompetitorListing(BaseModel):
    listingId: str
    title: str
    tags: list[str] = Field(default_factory=list)
    price: Optional[float] = None
    favorites: Optional[int] = None
    views: Optional[int] = None


class CompetitorRequest(BaseModel):
    keyword: str
    listings: list[CompetitorListing]


@app.post("/competitors/analyze")
def analyze_competitors(request: CompetitorRequest):
    seen_ids = set()
    valid = []
    issues = []

    for index, listing in enumerate(request.listings, start=1):
        listing_id = listing.listingId.strip()

        if not listing_id:
            issues.append({
                "record": index,
                "problem": "Missing listing ID"
            })
            continue

        if listing_id in seen_ids:
            issues.append({
                "record": index,
                "problem": "Duplicate listing ID"
            })
            continue

        if (
            (listing.price is not None and listing.price < 0)
            or (listing.favorites is not None and listing.favorites < 0)
            or (listing.views is not None and listing.views < 0)
        ):
            issues.append({
                "record": index,
                "problem": "Invalid numeric value"
            })
            continue

        seen_ids.add(listing_id)
        valid.append(listing)

    keyword = " ".join(request.keyword.lower().split())

    tag_counts = {}
    title_matches = 0

    for listing in valid:
        if keyword in " ".join(listing.title.lower().split()):
            title_matches += 1

        for tag in set(t.lower().strip() for t in listing.tags if t.strip()):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "keyword": keyword,
        "raw_records": len(request.listings),
        "valid_records": len(valid),
        "excluded_records": len(issues),
        "title_phrase_matches": title_matches,
        "top_tags": dict(
            sorted(
                tag_counts.items(),
                key=lambda item: (-item[1], item[0])
            )[:20]
        ),
        "issues": issues,
        "keyword_difficulty": None,
        "data_source": "user_supplied",
        "message": "Competitor analysis only. No verified Etsy KD."
    }

from fastapi.responses import FileResponse
from pathlib import Path

FRONTEND_FILE = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "index.html"
)

@app.get("/dashboard", include_in_schema=False)
def dashboard():
    if not FRONTEND_FILE.is_file():
        return {
            "error": "Frontend file not found",
            "expected_path": str(FRONTEND_FILE)
        }

    return FileResponse(FRONTEND_FILE, media_type="text/html")



# v1.5: local-only live collection, preserving the existing manual analysis endpoints.
import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException, Request

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_COLLECTOR = _PROJECT_ROOT / "etsy_collector_keyword.py"
_COLLECTION_LOCK = threading.Lock()
# A short-lived *full research report* cache, separate from the 24h shop cache.
# This never combines country or original-currency cohorts.
RESEARCH_RESULT_CACHE_SECONDS = 15 * 60


# v1.7.0: hosted (public) mode for a real deployment.
# Default stays local-only: NI_PUBLIC unset -> localhost only, keyring creds.
# NI_PUBLIC=1 -> the server is meant to bind 0.0.0.0, Etsy creds come from
# environment variables, and every route except /health requires HTTP
# Basic Auth (password from NI_ADMIN_PASSWORD). The server refuses to
# start in public mode without a password, so a deployment can never
# accidentally go live open.
import base64
import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse as _StarletteJSONResponse

NI_PUBLIC = os.environ.get("NI_PUBLIC") == "1"
if NI_PUBLIC and not os.environ.get("NI_ADMIN_PASSWORD"):
    raise RuntimeError(
        "NI_PUBLIC=1 requires NI_ADMIN_PASSWORD to be set. "
        "Refusing to start without a password."
    )


class _PublicAuthMiddleware(BaseHTTPMiddleware):
    """In public mode, require HTTP Basic Auth on every route except /health
    (hosts use /health for health checks). Local mode passes through."""

    async def dispatch(self, request, call_next):
        if not NI_PUBLIC:
            return await call_next(request)
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        password = os.environ.get("NI_ADMIN_PASSWORD") or ""
        ok = False
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8", "ignore")
                _, _, given = decoded.partition(":")
                ok = bool(given) and secrets.compare_digest(given, password)
            except Exception:
                ok = False
        if not ok:
            return _StarletteJSONResponse(
                {"detail": "Authentication required."},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Niche Intelligence"'},
            )
        return await call_next(request)


app.add_middleware(_PublicAuthMiddleware)


def _matching_research_result(result, keyword, country, native_currency, run_id=None):
    """Reject mismatched, incomplete and cross-market cached reports."""
    if not isinstance(result, dict):
        return False
    # A preview is valid for progress display only, never for full-report caching.
    if result.get("stage") == "listings_only" and run_id is None:
        return False
    if run_id is not None and result.get("collection_run_id") != run_id:
        return False
    if (result.get("keyword") != keyword
        or result.get("buyer_country") != country
        or result.get("listing_currency_filter") != native_currency
        or result.get("currency_filter_mode") != ("original_listing_currency" if country else "none")
        or result.get("source") != "Etsy Open API v3"
        or not isinstance(result.get("listings"), list)
        or not all(isinstance(item, dict) for item in result["listings"])):
        return False
    if country:
        count_before = result.get("sample_before_currency_filter")
        excluded = result.get("excluded_currency_count")
        if (type(count_before) is not int or type(excluded) is not int or excluded < 0
            or count_before != len(result["listings"]) + excluded
            or any(item.get("currency") != native_currency for item in result["listings"])):
            return False
    return True


def _read_recent_report(path, keyword, country, native_currency, mode="full"):
    """Only reuse a complete, recent report; never silently substitute a stale one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not _matching_research_result(data, keyword, country, native_currency):
            return None
        # Never reuse a Quick/Selected report as completed Full research.
        if mode == "quick":
            if data.get("enrichment_mode") != "quick" or data.get("stage") != "quick_complete":
                return None
        elif mode == "full":
            if data.get("enrichment_mode", "full") != "full" or data.get("stage") in ("quick_complete", "selected_complete"):
                return None
        else:
            return None
        # Only server-mediated collections have a per-run receipt.
        receipt = data.get("collection_run_id")
        if not isinstance(receipt, str) or len(receipt) != 32 or any(c not in "0123456789abcdef" for c in receipt):
            return None
        timestamp = datetime.fromisoformat(data["collected_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if not 0 <= age <= RESEARCH_RESULT_CACHE_SECONDS:
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _niche_verdict(result, keyword, country, relevance_audit, demand_report, strict=False):
    """GO / TEST / SKIP verdict from observed evidence only (niche_verdict.py).

    Non-strict (research responses): a missing module degrades to a neutral
    TEST verdict instead of failing the whole search. Strict (the dedicated
    /research/verdict endpoint): missing module is a 503.
    """
    import importlib.util
    module_file = _PROJECT_ROOT / "niche_verdict.py"
    if not module_file.is_file():
        if strict:
            raise HTTPException(status_code=503, detail="Verdict module missing.")
        return {"verdict": "TEST", "level": "amber",
                "label": "TEST — verdict unavailable",
                "summary": "Verdict module missing; competition evidence is still in this report.",
                "confidence": "low", "score": 0,
                "reasons": ["niche_verdict.py is not installed next to the collector."],
                "ranking_levers": [], "demand_status": "unverified",
                "demand_note": "Buyer-search demand is not verified.",
                "evidence": {"sample_size": len(result.get("listings", []))}}
    spec = importlib.util.spec_from_file_location("niche_verdict", module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verdict(
        keyword, result["listings"],
        reported_matching_listings=result.get("reported_matching_listings"),
        relevance=relevance_audit, demand=demand_report,
    )


# v1.6: lazy primary-image lookup for one listing (only when includes=Images
# returned nothing for it, so the main search stays fast). In-memory cache.
_IMAGE_CACHE: dict[int, dict] = {}


def _research_response(result, keyword, country, from_cache):
    relevance_audit = __relevance_audit(keyword, result["listings"])
    demand_report = _keyword_demand_report(keyword, country or "GLOBAL")
    verdict = _niche_verdict(result, keyword, country, relevance_audit, demand_report)
    return {
        "source": result.get("source"),
        "keyword": keyword,
        "buyer_country": country,
        "requested_display_currency": None,
        "listing_currency_filter": result.get("listing_currency_filter"),
        "currency_filter_mode": result.get("currency_filter_mode"),
        "sample_before_currency_filter": result.get("sample_before_currency_filter"),
        "excluded_currency_count": result.get("excluded_currency_count"),
        "market_filter": "buyer_country" if country else "not_applied",
        "market_note": (f"Listings eligible to ship to {country}; not local buyer demand or Etsy ranking." if country else "Unfiltered Etsy listing sample; not complete global coverage."),
        "collected_at": result.get("collected_at"),
        "reported_matching_listings": result.get("reported_matching_listings"),
        "sample_size": len(result["listings"]),
        "unique_shops": result.get("unique_shops"),
        "shop_cache_hits": result.get("shop_cache_hits"),
        "shop_api_fetches": result.get("shop_api_fetches"),
        "shop_fetch_errors": result.get("shop_fetch_errors"),
        "total_api_requests": result.get("total_api_requests"),
        "listings": result["listings"],
        "relevance_audit": relevance_audit,
        "competitor_evidence": __competitor_evidence(keyword, result["listings"]),
        "related_keywords": _related_keyword_observations(keyword, result["listings"]),
        "keyword_demand": demand_report,
        "verdict": verdict,
        "ranking_levers": verdict.get("ranking_levers", []),
        "report_cache": "hit" if from_cache else "fresh",
        "enrichment_mode": result.get("enrichment_mode", "full"),
        "shop_data_status": result.get("shop_data_status", "partial" if any(not r.get("shop_data_available") for r in result["listings"]) else "complete"),
        "shop_data_complete": result.get("shop_data_complete", all(r.get("shop_data_available") for r in result["listings"])),
        "enriched_shops": result.get("enriched_shops", len({r.get("shop_id") for r in result["listings"] if r.get("shop_id") is not None and r.get("shop_data_available")})),
        "unenriched_shops": result.get("unenriched_shops"),
        "report_cache_ttl_seconds": RESEARCH_RESULT_CACHE_SECONDS,
        "notes": "Shop sales are shop-wide lifetime totals; matches are not buyer searches. For selected buyer markets, only listings whose original price currency matches the market currency are included. No exchange-rate conversion is applied; shop location is not restricted."
    }


class LiveEtsyRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)
    # Explicit mode. Legacy clients default to Full Research.
    mode: Literal["quick", "full"] = "full"
    # Destination market, not seller nationality or country search volume.
    buyer_country: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")


@app.post("/etsy/search")
def live_etsy_search(payload: LiveEtsyRequest, request: Request):
    # Collector can consume real Etsy API quota; do not expose it as a public write endpoint.
    if os.environ.get("NI_PUBLIC") != "1" and (request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient")):
        raise HTTPException(status_code=403, detail="Live collection is local-only. Run the API on localhost.")
    keyword = " ".join(payload.keyword.strip().split())
    country = payload.buyer_country
    if len(keyword) < 2:
        raise HTTPException(status_code=422, detail="Enter at least 2 characters.")
    if not _COLLECTOR.is_file():
        raise HTTPException(status_code=503, detail="etsy_collector_keyword.py was not found in the project root.")
    # Import collector for its deterministic file naming; no Etsy requests here.
    import importlib.util
    spec = importlib.util.spec_from_file_location("niche_keyword_collector", _COLLECTOR)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"Collector dependency unavailable: {exc.name}. Install it into the backend Python environment.")
    if country and country not in module.MARKET_CURRENCY:
        raise HTTPException(status_code=422, detail="Selected buyer market is not supported by currency filtering.")
    native_currency = module.MARKET_CURRENCY.get(country) if country else None
    result_path = _PROJECT_ROOT / "research_results" / module.research_filename(keyword, country, (f"native-{native_currency}" if native_currency else None))

    return _collect_result(keyword, country, native_currency, _mode_path(result_path, payload.mode), mode=payload.mode)


def _mode_path(result_path, mode):
    return result_path.with_name(result_path.stem + "_QUICK.json") if mode == "quick" else result_path


def _collect_result(keyword, country, native_currency, result_path, preview_path=None, mode="full"):
    cached = _read_recent_report(result_path, keyword, country, native_currency, mode)
    if cached is not None:
        return _research_response(cached, keyword, country, from_cache=True)

    # Keep existing single collector behavior and quota protection.
    if not _COLLECTION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A collection is running. Try again after it finishes.")
    try:
        # Recheck after taking the lock; avoids a redundant Etsy call if the
        # report appeared between the first cache check and lock acquisition.
        cached = _read_recent_report(result_path, keyword, country, native_currency, mode)
        if cached is not None:
            return _research_response(cached, keyword, country, from_cache=True)
        # A per-request receipt proves that the JSON was generated by THIS run.
        # Filesystem timestamps alone are not reliable for freshness validation.
        run_id = preview_path.stem.removeprefix("preview_") if preview_path else uuid.uuid4().hex
        try:
            process = subprocess.run(
                [sys.executable, str(_COLLECTOR), keyword]
                + (["--buyer-country", country] if country else [])
                + ["--run-id", run_id, "--mode", mode]
                + (["--preview-path", str(preview_path)] if preview_path else []),
                cwd=str(_PROJECT_ROOT), capture_output=True, text=True,
                errors="replace", timeout=180, check=False
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Etsy collection timed out. Try again later.")
        if process.returncode != 0:
            # Only expose our own sanitized marker, never arbitrary subprocess
            # output (which can include credentials or remote error bodies).
            marker = next((line.strip() for line in process.stdout.splitlines()
                           if line.startswith("COLLECTION_ERROR:")), None)
            safe_errors = {"COLLECTION_ERROR: HTTP 429", "COLLECTION_ERROR: HTTP 401",
                           "COLLECTION_ERROR: HTTP 403", "COLLECTION_ERROR: HTTP 500",
                           "COLLECTION_ERROR: HTTP 502", "COLLECTION_ERROR: HTTP 503",
                           "COLLECTION_ERROR: HTTP 504", "COLLECTION_ERROR: network_error",
                           "COLLECTION_ERROR: unexpected_error", "COLLECTION_ERROR: credentials_missing"}
            explanation = marker if marker in safe_errors else "COLLECTION_ERROR: unexpected_error"
            raise HTTPException(status_code=502, detail=f"Etsy collector failed ({explanation}). No stale data shown.")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise HTTPException(status_code=502, detail="Collector output was missing or unreadable. No stale data shown.")
        if result.get("collection_run_id") != run_id:
            raise HTTPException(status_code=502, detail="Collector result did not match this request. Stale data withheld.")
        if not _matching_research_result(result, keyword, country, native_currency, run_id):
            raise HTTPException(status_code=502, detail="Collector output did not match the request, or native currency validation failed.")
        # Full Research from older collector fixtures has no explicit mode;
        # still validate its receipt and full-market/currency contract.
        if mode == "quick" and (result.get("enrichment_mode") != "quick" or result.get("stage") != "quick_complete"):
            raise HTTPException(status_code=502, detail="Quick Research mode mismatch. Results withheld.")
        if mode == "full" and (result.get("enrichment_mode", "full") != "full" or result.get("stage") in ("quick_complete","selected_complete")):
            raise HTTPException(status_code=502, detail="Full Research mode mismatch. Results withheld.")
        return _research_response(result, keyword, country, from_cache=False)
    finally:
        _COLLECTION_LOCK.release()


# v4.5 non-blocking two-stage research. Each job is bound to an unpredictable ID.
# One collector at a time to honor Etsy API limits. Existing POST /etsy/search stays intact.
import time
from fastapi.responses import JSONResponse
_JOBS = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL = 15 * 60


def _local_only(request):
    """Local-only guard. Overrides: NI_PUBLIC_DEMO=1 (demo server, no real
    Etsy credentials) and NI_PUBLIC=1 (hosted mode — Basic Auth is enforced
    by middleware) let a remote browser reach the research endpoints.
    Never set NI_PUBLIC_DEMO=1 on a machine holding real Etsy credentials.
    """
    if os.environ.get("NI_PUBLIC_DEMO") == "1" or os.environ.get("NI_PUBLIC") == "1":
        return
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Local research only.")


def _collect_background(job_id, keyword, country, currency, result_path, preview_path, mode):
    try:
        output = _collect_result(keyword, country, currency, result_path, preview_path, mode)
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "complete"
            _JOBS[job_id]["data"] = output
    except HTTPException as exc:
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "failed"
            _JOBS[job_id]["error"] = str(exc.detail)
    except Exception:
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "failed"
            _JOBS[job_id]["error"] = "Research failed. Check backend logs."
    finally:
        try:
            preview_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.post("/etsy/search/start")
def start_research(payload: LiveEtsyRequest, request: Request):
    _local_only(request)
    keyword = " ".join(payload.keyword.strip().split())
    country = payload.buyer_country
    if len(keyword) < 2 or not _COLLECTOR.is_file():
        raise HTTPException(status_code=422, detail="Invalid keyword or collector missing.")
    import importlib.util
    spec = importlib.util.spec_from_file_location("niche_keyword_collector_progress", _COLLECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if country and country not in module.MARKET_CURRENCY:
        raise HTTPException(status_code=422, detail="Unsupported market.")
    currency = module.MARKET_CURRENCY.get(country) if country else None
    path = _PROJECT_ROOT / "research_results" / module.research_filename(keyword, country, (f"native-{currency}" if currency else None))
    path = _mode_path(path, payload.mode)
    cached = _read_recent_report(path, keyword, country, currency, payload.mode)
    if cached is not None:
        return {"status": "complete", "data": _research_response(cached, keyword, country, True)}
    with _JOBS_LOCK:
        now = time.monotonic()
        for job_id, job in list(_JOBS.items()):
            if now - job["created"] > _JOB_TTL and job["status"] != "running":
                del _JOBS[job_id]
        for job_id, job in _JOBS.items():
            if job["status"] == "running":
                if job["keyword"] == keyword and job["country"] == country and job["mode"] == payload.mode:
                    return {"status": "running", "job_id": job_id}
                raise HTTPException(status_code=429, detail="Another Etsy collection is running. Please finish that search first.")
        if _COLLECTION_LOCK.locked():
            raise HTTPException(status_code=429, detail="Another Etsy collection is running.")
        job_id = uuid.uuid4().hex
        preview = _PROJECT_ROOT / "research_results" / f"preview_{job_id}.json"
        _JOBS[job_id] = {"status": "running", "created": now, "keyword": keyword,
                         "country": country, "currency": currency, "mode": payload.mode, "preview": preview,
                         "last_preview": None, "data": None, "error": None}
    worker = threading.Thread(target=_collect_background,
                              args=(job_id, keyword, country, currency, path, preview, payload.mode), daemon=True)
    worker.start()
    return {"status": "running", "job_id": job_id}


@app.get("/etsy/search/progress/{job_id}")
def research_progress(job_id: str, request: Request):
    _local_only(request)
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(status_code=404, detail="Research job not found.")
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job or time.monotonic() - job["created"] > _JOB_TTL:
            raise HTTPException(status_code=404, detail="Research job expired; retry the keyword.")
        status = job["status"]
        result = job["data"]
        error = job["error"]
        keyword, country, currency, preview = job["keyword"], job["country"], job["currency"], job["preview"]
    if status == "complete":
        return {"status": "complete", "data": result}
    if status == "failed":
        return {"status": "failed", "error": error}
    # The browser displays the preview only once. Do not resend the entire
    # listings payload on every 750ms poll while shop enrichment runs.
    with _JOBS_LOCK:
        if _JOBS[job_id].get("last_preview") == "delivered":
            return {"status": "collecting", "stage": "shops_enrichment",
                    "message": "Shop profiles are loading; listings preview already sent."}
    try:
        stage = json.loads(preview.read_text(encoding="utf-8"))
        if (stage.get("stage") == "listings_only"
                and _matching_research_result(stage, keyword, country, currency, job_id)):
            response = _research_response(stage, keyword, country, False)
            response["research_stage"] = "listings_only"
            response["enrichment_mode"] = job.get("mode", "full")
            response["shop_data_status"] = "partial"
            response["shop_data_complete"] = False
            response["report_cache"] = "not_cached_preview"
            with _JOBS_LOCK:
                # If overlapping polls race, only the first gets the large preview.
                if _JOBS[job_id].get("last_preview") == "delivered":
                    return {"status": "collecting", "stage": "shops_enrichment",
                            "message": "Shop profiles are loading; listings preview already sent."}
                _JOBS[job_id]["last_preview"] = "delivered"
            return {"status": "listings_ready", "data": response}
    except (OSError, ValueError, TypeError):
        pass
    return {"status": "collecting", "message": "Fetching Etsy listings and preparing research."}


# v4.9: On-demand shop enrichment of an existing verified Quick report.
# Never re-search listings, select unrelated shops, or mix country/currency cohorts.
class ShopEnrichmentRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)
    buyer_country: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}$")
    mode: Literal["selected", "full"]
    shop_ids: list[int] = Field(default_factory=list, max_length=20)


@app.post("/etsy/search/enrich")
def enrich_research(payload: ShopEnrichmentRequest, request: Request):
    _local_only(request)
    keyword = " ".join(payload.keyword.strip().split())
    country = payload.buyer_country
    if payload.mode == "selected" and not payload.shop_ids:
        raise HTTPException(422, detail="Choose at least one shop")
    if any(type(sid) is not int or sid <= 0 for sid in payload.shop_ids) or len(set(payload.shop_ids)) != len(payload.shop_ids):
        raise HTTPException(422, detail="Invalid or repeated shop IDs")
    import importlib.util
    spec=importlib.util.spec_from_file_location("niche_shop_collector", _COLLECTOR)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if country and country not in module.MARKET_CURRENCY:
        raise HTTPException(422, detail="Unsupported market")
    currency=module.MARKET_CURRENCY.get(country) if country else None
    base=_PROJECT_ROOT/"research_results"/module.research_filename(keyword,country,f"native-{currency}" if currency else None)
    quick_path=_mode_path(base,"quick")
    quick=_read_recent_report(quick_path,keyword,country,currency,"quick")
    if quick is None:
        raise HTTPException(409,detail="Quick Research is missing or older than 15 minutes. Run Quick Research again.")
    available={r.get("shop_id") for r in quick["listings"] if type(r.get("shop_id")) is int and r["shop_id"]>0}
    if payload.mode=="selected" and not set(payload.shop_ids).issubset(available):
        raise HTTPException(422,detail="Selected shop is not in this research sample")
    if not _COLLECTION_LOCK.acquire(blocking=False):
        raise HTTPException(429,detail="Another Etsy collection is running")
    run_id=uuid.uuid4().hex
    # Random temporary output is removed on success or failure. Full mode writes
    # the stable Full report itself, preserving the legacy cached endpoint.
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="ni49_enrich_") as tempdir:
            temporary=Path(tempdir)/"enriched.json"
            cmd=[sys.executable,str(_COLLECTOR),keyword]
            if country: cmd += ["--buyer-country",country]
            cmd += ["--run-id",run_id,"--mode",payload.mode,"--enrich-from-quick",
                    "--output-path",str(temporary)]
            if payload.mode=="selected":cmd += ["--selected-shop-ids",",".join(map(str,payload.shop_ids))]
            try:
                proc=subprocess.run(cmd,cwd=str(_PROJECT_ROOT),capture_output=True,text=True,errors="replace",timeout=180,check=False)
            except subprocess.TimeoutExpired:
                raise HTTPException(504,detail="Shop enrichment timed out")
            if proc.returncode:
                marker=next((line.strip() for line in proc.stdout.splitlines() if line.startswith("COLLECTION_ERROR:")),None)
                allowed={"COLLECTION_ERROR: credentials_missing","COLLECTION_ERROR: HTTP 429","COLLECTION_ERROR: HTTP 401","COLLECTION_ERROR: HTTP 403","COLLECTION_ERROR: network_error"}
                raise HTTPException(502,detail="Shop enrichment failed ("+(marker if marker in allowed else "unexpected_error")+")")
            try:
                result=json.loads((base if payload.mode=="full" else temporary).read_text(encoding="utf-8"))
            except (OSError,ValueError):
                raise HTTPException(502,detail="Enrichment output missing")
            if (not _matching_research_result(result,keyword,country,currency,run_id)
                or result.get("enrichment_mode")!=payload.mode
                or len(result["listings"])!=len(quick["listings"])
                or any((r.get("listing_id"),r.get("currency"),r.get("price")) !=
                       (q.get("listing_id"),q.get("currency"),q.get("price"))
                       for r,q in zip(result["listings"],quick["listings"]))):
                raise HTTPException(502,detail="Enrichment result integrity check failed")
            if payload.mode == "full":
                # Promote only a verified Full report into its own cache.
                base.parent.mkdir(parents=True,exist_ok=True)
                atomic=base.with_suffix(".json.tmp")
                atomic.write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
                atomic.replace(base)
            return {"status":"complete","data":_research_response(result,keyword,country,False)}
    finally:
        _COLLECTION_LOCK.release()


class VerdictRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)
    listings: list[dict] = Field(default_factory=list, max_length=5000)
    reported_matching_listings: Optional[int] = Field(default=None, ge=0)


@app.post("/research/verdict")
def verdict_endpoint(payload: VerdictRequest, request: Request):
    """GO / TEST / SKIP verdict for an uploaded or already-collected sample."""
    _local_only(request)
    keyword = " ".join(payload.keyword.strip().split())
    if len(keyword) < 2:
        raise HTTPException(status_code=422, detail="Keyword must have at least two non-space characters.")
    relevance_audit = __relevance_audit(keyword, payload.listings)
    demand_report = _keyword_demand_report(keyword, "GLOBAL")
    result = {"listings": payload.listings,
              "reported_matching_listings": payload.reported_matching_listings}
    return _niche_verdict(result, keyword, None, relevance_audit, demand_report, strict=True)


@app.get("/etsy/listing/{listing_id}/image")
def listing_image_endpoint(listing_id: int, request: Request):
    """Lazy primary image for one listing (fallback when includes=Images missed it)."""
    _local_only(request)
    if listing_id <= 0:
        raise HTTPException(status_code=422, detail="Invalid listing id.")
    if listing_id in _IMAGE_CACHE:
        return _IMAGE_CACHE[listing_id]
    import importlib.util
    import keyring
    spec = importlib.util.spec_from_file_location("niche_keyword_collector_image", _COLLECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        key = keyring.get_password(module.SERVICE, "keystring")
        secret = keyring.get_password(module.SERVICE, "shared_secret")
    except Exception:
        # No keyring backend on this machine (e.g. Linux without Secret Service).
        raise HTTPException(status_code=503, detail="Etsy credentials missing.")
    if not key or not secret:
        raise HTTPException(status_code=503, detail="Etsy credentials missing.")
    headers = {"x-api-key": f"{key}:{secret}", "Accept": "application/json"}
    image = module.fetch_listing_image(listing_id, headers)
    if not image:
        raise HTTPException(status_code=404, detail="No image found for this listing.")
    if len(_IMAGE_CACHE) >= 500:
        _IMAGE_CACHE.clear()
    _IMAGE_CACHE[listing_id] = image
    return image


# Relevance API for uploaded or already collected datasets; does not call Etsy.
class RelevanceAuditRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)
    product_type: str = Field(default='', max_length=100)
    listings: list[dict] = Field(max_length=5000)


def __relevance_audit(keyword, listings, product_type=''):
    import importlib.util
    module_file = _PROJECT_ROOT / 'universal_relevance_v3.py'
    if not module_file.is_file():
        raise HTTPException(status_code=503, detail='Relevance module missing; install universal_relevance_v3.py at project root.')
    spec = importlib.util.spec_from_file_location('universal_relevance_v3', module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.analyze(keyword, listings, product_type=product_type)


def __competitor_evidence(keyword, listings):
    """Read-only category/tag observations for every Etsy response stage.

    This never makes an Etsy API request and never invents search volume,
    direct-competitor status, or per-listing sales. Keep legacy audit intact.
    """
    import importlib.util
    module_file = _PROJECT_ROOT / 'universal_relevance_v3.py'
    if not module_file.is_file():
        raise HTTPException(status_code=503, detail='Competitor evidence module missing.')
    spec = importlib.util.spec_from_file_location('niche_category_tag_evidence', module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, 'competitor_evidence', None)):
        raise HTTPException(status_code=503, detail='Install the updated relevance module to enable category and tag analysis.')
    return module.competitor_evidence(keyword, listings)


@app.post('/research/relevance')
def relevance_audit(payload: RelevanceAuditRequest, request: Request):
    if os.environ.get("NI_PUBLIC") != "1" and (request.client is None or request.client.host not in ('127.0.0.1', '::1', 'localhost', 'testclient')):
        raise HTTPException(status_code=403, detail='Research audit is local-only.')
    return __relevance_audit(payload.keyword, payload.listings, payload.product_type)


# Keyword Demand foundation v1: read-only, source-independent response contract.
# This does NOT contact Etsy, Google, or an unlicensed provider.
def _keyword_demand_report(keyword: str, market: str = "GLOBAL", language: str = "und") -> dict:
    import importlib.util
    module_file = _PROJECT_ROOT / "keyword_demand_intelligence.py"
    if not module_file.is_file():
        raise HTTPException(status_code=503, detail="Keyword demand foundation module missing.")
    spec = importlib.util.spec_from_file_location("niche_keyword_demand_foundation", module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.empty_research(keyword, market, language)


@app.get("/research/keyword-demand")
def keyword_demand_endpoint(
    request: Request,
    keyword: str = Query(..., min_length=2, max_length=100),
    market: str = Query("GLOBAL", pattern=r"^(GLOBAL|[A-Z]{2})$"),
    language: str = Query("und", pattern=r"^[a-z]{2,3}(?:-[A-Za-z]{2,8})*$"),
):
    _local_only(request)
    keyword = " ".join(keyword.strip().split())
    if len(keyword) < 2:
        raise HTTPException(status_code=422, detail="Keyword must have at least two non-space characters.")
    return _keyword_demand_report(keyword, market, language)


# Related Keyword Discovery — read-only, market-cohort-safe analysis.
# Extracts seller wording from the *same validated listing sample*; no extra API calls.
def _related_keyword_observations(keyword: str, listings: list[dict], *, limit: int = 30, min_listings: int = 2) -> dict:
    import importlib.util
    module_path = _PROJECT_ROOT / "related_keyword_discovery.py"
    if not module_path.is_file():
        raise HTTPException(status_code=503, detail="Related Keyword Discovery module missing.")
    spec = importlib.util.spec_from_file_location("niche_related_keyword_discovery", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "discover", None)):
        raise HTTPException(status_code=503, detail="Related Keyword Discovery function missing.")
    return module.discover(keyword, listings, limit=limit, min_listings=min_listings)


class RelatedKeywordsRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=100)
    listings: list[dict] = Field(max_length=5000)
    limit: int = Field(default=30, ge=1, le=100)
    min_listings: int = Field(default=2, ge=1, le=5000)


@app.post("/research/related-keywords")
def related_keyword_endpoint(payload: RelatedKeywordsRequest, request: Request):
    _local_only(request)
    keyword = " ".join(payload.keyword.strip().split())
    if len(keyword) < 2:
        raise HTTPException(status_code=422, detail="Keyword must have at least two non-space characters.")
    return _related_keyword_observations(keyword, payload.listings,
                                         limit=payload.limit, min_listings=payload.min_listings)
