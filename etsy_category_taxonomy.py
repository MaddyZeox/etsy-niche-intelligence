"""Etsy seller taxonomy resolution. Only API-sourced categories; no guessed names."""
import json
import time
from collections import Counter
from pathlib import Path

CACHE_TTL = 7 * 86400
MAX_CACHE_BYTES = 3 * 1024 * 1024


def parse_taxonomy(payload):
    """Flatten nested Etsy seller taxonomy nodes to ID -> human-readable full path."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("invalid taxonomy tree")
    mapping = {}
    def visit(node, parents, depth=0):
        if depth > 24 or not isinstance(node, dict):
            return
        nid, name = node.get("id"), node.get("name")
        if type(nid) is not int or nid <= 0 or not isinstance(name, str) or not name.strip():
            return
        path = [*parents, name.strip()]
        mapping[nid] = {"category_name": path[-1], "category_path": " > ".join(path)}
        for child in (node.get("children") or []):
            visit(child, path, depth + 1)
    for node in payload["results"]:
        visit(node, [])
    if not mapping:
        raise ValueError("no valid taxonomy nodes")
    return mapping


def load_taxonomy(fetch_json, headers, cache_path):
    """Return (mapping, source). Gracefully use stale cache if API is unavailable."""
    cache_path = Path(cache_path)
    cached = None
    if cache_path.is_file() and cache_path.stat().st_size <= MAX_CACHE_BYTES:
        try:
            blob = json.loads(cache_path.read_text(encoding="utf-8"))
            cached = (parse_taxonomy(blob["taxonomy"]), blob.get("fetched_at", 0))
            if time.time() - float(cached[1]) < CACHE_TTL:
                return cached[0], "cached"
        except (ValueError, OSError, KeyError, TypeError):
            cached = None
    try:
        response = fetch_json("/seller-taxonomy/nodes", headers)
        mapping = parse_taxonomy(response)
        blob = json.dumps({"fetched_at": time.time(), "taxonomy": response}, ensure_ascii=False)
        if len(blob.encode("utf-8")) <= MAX_CACHE_BYTES:
            try:
                temp = cache_path.with_suffix(".tmp")
                temp.write_text(blob, encoding="utf-8")
                temp.replace(cache_path)
            except OSError:
                pass
        return mapping, "api"
    except Exception:
        return (cached[0], "stale_cache") if cached else ({}, "unavailable")


def annotate_categories(listings, mapping):
    for item in listings:
        node_id = item.get("taxonomy_id")
        record = mapping.get(node_id) if type(node_id) is int else None
        item["category_name"] = record["category_name"] if record else None
        item["category_path"] = record["category_path"] if record else None
    return listings


def category_summary(listings):
    counts = Counter((r.get("taxonomy_id"), r.get("category_path")) for r in listings
                     if type(r.get("taxonomy_id")) is int and r.get("category_path"))
    return [{"taxonomy_id": k[0], "category_path": k[1], "listing_count": n}
            for k, n in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0][1]))]
