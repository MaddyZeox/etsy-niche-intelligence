# Niche Intelligence Universal Engine v3 — Phase 1 Foundation

**Status:** Implemented and locally tested: a lossless, language-safe JSON contract and validation layer. **NOT** a functioning multilingual classifier, country-specific Etsy API filter, or search-volume provider.

## Purpose
Freeze a uniform record structure for any product niche and any original listing language. Preserve Etsy source fields, never mislabel shop sales as listing sales, and never invent country-level keyword demand or relevance confidence.

## Setup
Place `universal_contract.py` in `Desktop\NicheIntelligence` (do not replace your working Etsy collector or v2.2 engine). Python 3.10+; no extra packages or API credentials.

Run against an existing collector JSON, e.g.:

```cmd
cd /d "%USERPROFILE%\Desktop\NicheIntelligence"
python universal_contract.py --input "research_results\etsy_minimalist-wedding-invitation_2e737ddc.json"
```

This writes a **separate** file with `_v3_contract.json` suffix. It does not modify original data or make Etsy requests.

`--country US` can annotate research target, but **does not mean** results have been filtered to US buyers. For that we need a **future collector integration** that sends supported Etsy API parameters and stores the actual applied filter / response context. `--scope buyer_shipping_eligible --country US` records requested intent, **not verification**.

Run tests: `python -m unittest -v test_contract.py`.

## What the next stages must implement and validate
1. Detect language using a tested language identifier, recording confidence and `unknown` for ambiguous short texts; keep originals and multilingual titles intact.
2. Multilingual semantic product and buyer-intent understanding with a shared model, not per-niche keyword lists. Extract comparable type, subtype, format, material and recipient.
3. Country-aware Etsy API collection, with clearly differentiated *shipping eligibility*, *shop location*, *currency*, *buyer search volume* and *ranking*; do not conflate them.
4. Independent human-reviewed multilingual and cross-niche benchmark labels, unseen test set and regression-gate metrics. Unknown items should abstain.

## Data caveats
- Etsy returned listing count is **not** monthly buyer searches.
- Shop sales are lifetime shop-level totals and **not** per-listing sales.
- Currency conversion hasn't happened; do not compare raw figures across currencies.
- Model classifications are empty by design; treating these records as classified is a bug.
