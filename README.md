# Niche Intelligence v1.7.0

Local-only Etsy seller research tool: real Etsy Open API v3 data, no invented
numbers. Enter a keyword → get a 100-listing sample → shop profiles,
category/tag analysis, related keywords, and a **GO / TEST / SKIP verdict** —
whether the keyword is worth working on, and what can help you rank for it.

## What's new (v1.6.0, 14 Nov 2026)
- **Verdict:** every report includes GO / TEST / SKIP + reasons + ranking
  levers (relevance, 13 tags, first photo, price, listing type,
  personalization, shop quality, recency). The verdict is based only on
  observed evidence; buyer-search demand is still unverified and is stated
  as such.
- **Listings with images:** the collector fetches the first photo in the
  same call via `includes=Images` (zero extra API calls). If a listing's
  photo is missing, the frontend loads it lazily for visible rows via
  `GET /etsy/listing/{id}/image`.
- **Listing type and details:** every listing saves `listing_type`
  (physical/digital/both), materials, who_made/when_made, personalizable,
  quantity and created_timestamp.
- **Speed fix:** every search used to run in `mode=full` — nothing showed
  until all uncached shops were fetched. Now it is **quick-first**:
  listings + cached shops show in 2-4 seconds, then
  `POST /etsy/search/enrich` fills in the remaining shop profiles in the
  background. Listing pages are now fetched in parallel. Total API calls
  are the same, but the first screen no longer waits.
- **Cleaned:** `.venv` (2,389 files), 11 `backup_before_*` folders, the
  `update49/` old snapshot, all `__pycache__`, old relevance
  engines/collectors and duplicate READMEs removed. Install again from
  `requirements.txt`. `universal_contract.py` was not deleted — it is in
  `experiments/`.

## What's new (v1.7.0, 14 Nov 2026)
- **Hosted deploy ready.** Set `NI_PUBLIC=1` and the tool runs on a public
  host (Render free tier, Railway, Docker): Etsy credentials come from
  environment variables (`ETSY_KEYSTRING`, `ETSY_SHARED_SECRET`) instead
  of Windows Credential Manager, and every page except `/health` is
  behind HTTP Basic Auth (password from `NI_ADMIN_PASSWORD`). The server
  refuses to start in public mode without a password. Local use is
  unchanged — without `NI_PUBLIC=1` it stays localhost-only with keyring
  credentials. Files: `Dockerfile`, `render.yaml`, `Procfile`,
  `.env.example`. See "Deploy it live" below.
- **Demo FULL report:** `research_results/..._FULL.json` — same 100
  listings, now with a mock shop profile on every listing (shop age,
  lifetime sales, rating, review count, 100/100 enriched).

## What's new (v1.6.2, 14 Nov 2026)
- **All English, everywhere.** Every user-facing string in the tool —
  verdict labels, reasons, ranking levers, error messages and this README —
  is now in English. No Roman Urdu text remains in the tool.

## Demo (no credentials needed)
`research_results/` contains two ready demo reports for
"minimalist wedding invitation", market US:
- `..._QUICK.json` — quick mode: listings instantly, shop data not yet
  enriched (this is what quick-first shows before enrich runs).
- `..._FULL.json` — full mode: complete, with every listing's shop age,
  lifetime sales, rating and review count (100/100 listings enriched).
  Search mode **full** to see the complete report. Both are mock data
  (photos from picsum), not real Etsy data.

## Run it
```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python etsy_credentials.py
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
Then open in your browser: http://127.0.0.1:8000/dashboard

The same commands work on Linux/Mac (activate with
`source .venv/bin/activate`). Etsy credentials are saved in Windows
Credential Manager (`keyring`); on Linux, keyring uses its own backend
(Secret Service). There is no hardcoded API key in the code.

## Deploy it live (Render free tier)
This makes the real tool reachable from anywhere, including your phone.
The free tier sleeps after inactivity, so the first open can take ~1
minute to wake up. The shop cache and reports live on the server's disk;
on the free tier that disk resets on redeploy, so cached reports rebuild
on the next search — nothing is lost, it just takes one more API call.

1. **Get your Etsy credentials.** Open https://www.etsy.com/developers/apps
   and open the app you already use for this tool. Copy its **keystring**
   and **shared secret** (the same values saved in Windows Credential
   Manager on your PC).
2. **Put the code on GitHub.** Create a private repo and push this folder
   (or upload the zip contents). Do NOT commit a `.env` file.
3. **Create the service.** In https://dashboard.render.com → New →
   Web Service → pick the repo. Render reads `render.yaml` automatically.
4. **Set the secrets.** In the service's Environment tab, set:
   - `ETSY_KEYSTRING` = your keystring
   - `ETSY_SHARED_SECRET` = your shared secret
   - `NI_ADMIN_PASSWORD` = a long random password (this is your login)
   - `NI_PUBLIC` = `1`
5. **Open it.** Render gives a URL like
   `https://niche-intelligence.onrender.com`. Open `/dashboard` — the
   browser asks for a username (anything) and the password you set.
   `/health` stays open without login so Render can check the service.

Security notes: only you should know the password — anyone with it can
spend your Etsy API quota. If the password leaks, change it in the
dashboard and redeploy. Never share the keystring/shared secret.
Railway or Docker work the same way: set the same four variables and run
`uvicorn backend.main:app --host 0.0.0.0 --port $PORT` (see `Dockerfile`).

## Flow (quick-first)
1. The frontend sends `POST /etsy/search/start` (`mode=quick`).
2. The backend first checks the 15-minute cached report.
3. The collector fetches 2 pages from Etsy in parallel (max 100 listings,
   `sort_on=score`), applies the `buyer_country` filter + native-currency
   cohort.
4. The listings preview appears immediately; then the frontend fills in
   shop profiles in the background via `POST /etsy/search/enrich`
   (`mode=full`).
5. Every response attaches the relevance audit, competitor evidence,
   related keywords, keyword demand (currently `unavailable` — a provider
   still needs to be connected), verdict and ranking levers.

## Endpoints (localhost only — 403 from outside)
- `GET /health`, `GET /dashboard` (frontend)
- `POST /etsy/search/start`, `GET /etsy/search/progress/{job_id}`
- `POST /etsy/search/enrich`, `GET /etsy/listing/{id}/image`
- `POST /research/relevance`, `POST /research/related-keywords`
- `GET /research/keyword-demand`, `POST /research/verdict`
- `GET /keywords/analyze` (legacy placeholder — volume/KD `null`)

## Tests
```cmd
python -m unittest discover -s tests
```
78 original + new verdict/image/collector tests. Live Etsy collection
needs credentials; without them `/etsy/search/start` fails gracefully
("No stale data shown") instead of crashing.

## Honest limits
- **No search volume / KD.** This tool never invents demand. The verdict
  rests on competition evidence; connecting an authorized provider is the
  next step to verify demand.
- **No sales estimates** — shop sales are shop-wide lifetime totals only,
  not per-listing sales.
- **Local-only, single user.** One search at a time; a second one gets 429.
- **Images:** `includes=Images` must be supported by Etsy; if the API
  returns 400, the collector retries plain and the lazy endpoint fills in
  photos. Still to be verified against live Etsy.
