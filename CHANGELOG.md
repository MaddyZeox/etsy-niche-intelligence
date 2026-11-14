# CHANGELOG — Niche Intelligence

## v1.7.0 (14 Nov 2026) — hosted deploy ready
- **NI_PUBLIC mode:** set `NI_PUBLIC=1` and the tool runs on a public host
  (Render free tier, Railway, Docker). Etsy credentials come from env vars
  (`ETSY_KEYSTRING`, `ETSY_SHARED_SECRET` — env wins, keyring is the local
  fallback). Every route except `/health` requires HTTP Basic Auth
  (password from `NI_ADMIN_PASSWORD`); the server refuses to start in
  public mode without a password. Local use unchanged (localhost-only,
  no password needed).
- **Deploy files:** `Dockerfile`, `render.yaml` (free-tier web service,
  health check `/health`), `Procfile`, `.dockerignore`, `.env.example`.
- **README:** step-by-step "Deploy it live (Render free tier)" guide.
- New tests: `tests/test_public_deploy.py` (env credentials, public auth,
  fail-fast without password).

## v1.6.3 (14 Nov 2026) — demo data now has shop profiles
- **Demo FULL report:** `research_results/..._FULL.json` — same 100
  listings, now with a mock shop profile on every listing (shop age,
  lifetime sales, rating, review count, 100/100 enriched). Verdict now
  scores shop evidence too (GO, score 6, incl. "Median shop lifetime
  sales are only 79") and shows the Shop quality ranking lever. The
  QUICK file is unchanged (quick mode honestly shows listings before
  shop enrichment). Regenerator: `workspace/etsy-tool/regen_demo_shop_data.py`.
- No code change in this release — version bump is for the new demo data.

## v1.6.2 (14 Nov 2026) — all-English UI
- **English only:** every user-facing string is now English — verdict
  labels, reasons, ranking levers, error messages and README. The Roman
  Urdu strings that shipped in v1.6.0/v1.6.1 (niche_verdict.py, one
  demand_note in backend/main.py, README) are removed. Audit: zero Urdu
  tokens across all tool code + docs + demo data; 94/94 tests pass.
- Demo data regenerated in English (`demo-data.json`, GO verdict
  "GO — Work on it"); hosted demo page updated to match.

## v1.6.1 (14 Nov 2026) — public demo support
- **Demo flag:** `NI_PUBLIC_DEMO=1` env var lets the research endpoints serve
  a remote browser (default stays local-only). Never set it on a machine
  holding real Etsy credentials.
- **Demo report included:** `research_results/etsy_minimalist-wedding-invitation_2e737ddc_US_NATIVE-USD_QUICK.json`
  — a 100-listing snapshot (mock data, picsum photos) so the full report
  with verdict + images can be seen instantly without Etsy credentials.
  Search "minimalist wedding invitation", market US, mode quick.
- The live-IP attempt from this VM failed: the VM has no inbound path
  (CGNAT + egress proxy, connection refused from outside). A hosted demo
  page was built instead (see v1.6.0 build report).

## v1.6.0 (14 Nov 2026) — cleaned build + verdict + images + speed fix
- **Cleaned:** removed `backend/.venv` (2,389 files, 33.75 MB), 11
  `backup_before_*` folders, `update49/` old snapshot, all `__pycache__`,
  old relevance engines (v1/v2/v21/v22), old collectors
  (`etsy_collector.py`, `_backup`, `_wedding`), one-off test scripts,
  33 of 34 `research_results/` outputs (1 sample kept), nested phase-1 zip,
  duplicate READMEs. `universal_contract.py` moved to `experiments/`
  (not deleted). Added `requirements.txt`, `README.md`, `.gitignore`.
- **Verdict (new):** `niche_verdict.py` — GO / TEST / SKIP from observed
  evidence only (reported matches, title-match share, shop strength,
  favorites, category clarity, tag usage). Never invents volume/KD/sales.
  Demand stays `unverified` until an authorized provider is connected.
  Wired into every research response + new `POST /research/verdict`.
  Ranking levers (Etsy factors mapped to sample evidence) included.
- **Images (new):** collector requests `includes=Images` on
  `/listings/active` (zero extra calls); primary image stored per listing
  (`image_url` 570xN + `image_url_large`). 400 fallback retries plain.
  New `GET /etsy/listing/{id}/image` lazily fills photos the includes
  call missed; frontend loads them only for visible rows. Listings table
  now shows Photo + Type columns.
- **Listing fields (new):** `listing_type` (physical/digital/both),
  materials, who_made/when_made, personalizable, customizable, quantity,
  created_timestamp per listing.
- **Speed fix:** frontend used to hardcode `mode=full` — nothing showed
  until ALL uncached shops were fetched. Now **quick-first**: listings +
  cached shops render first, then `POST /etsy/search/enrich` fills shop
  profiles in the background (existing endpoint, previously unused by UI).
  Listing pages now fetch in parallel. Mocked timing (0.25s/call):
  quick 0.51s / 3 calls vs full 2.52s / 11 calls.
- **Robustness fixes found by live test:** verdict degrades to neutral
  TEST instead of failing a search when its module is missing; keyring
  reads return None instead of raising on machines without a keyring
  backend (image endpoint now 503, not 500); verdict demand check no
  longer treats an all-None metrics dict as "available".
- **Tests:** 94 pass (78 original + 16 new: verdict, images, collector
  mocked end-to-end incl. includes=Images 400-fallback).

## v1.5.0 (before, from Tool_0_ub85.zip)
- FastAPI backend + single-page frontend + Etsy API v3 collector
  (100 listings, buyer-country + native-currency cohort, 24h shop cache).
