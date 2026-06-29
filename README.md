# JeansFinder

A personal tool that monitors Vinted for a specific style of jeans (dark/charcoal baggy denim), scores every listing using CLIP image embeddings plus colour analysis, and surfaces the best matches in a mobile-friendly feed that updates in real time.

Runs as two processes: a background scraper pipeline that polls Vinted every 15–25 minutes, and a Flask web UI you open on your phone over the same WiFi.

![scoring](https://img.shields.io/badge/scoring-CLIP_%2B_colour-blue) ![python](https://img.shields.io/badge/python-3.8%2B-green)

---

## How it works

**1. Scraper (`scraper.py`)** — a custom Vinted API client built with `httpx`. It fetches a session cookie from the Vinted homepage, then queries the internal `/api/v2/catalog/items` JSON endpoint directly — the same endpoint Vinted's own frontend uses. No browser, no HTML parsing, so it's fast and resilient to layout changes. Each query pulls multiple pages across two sort orders (newest + relevance) for coverage, and the cookie auto-refreshes if it expires mid-run.

**2. Scorer (`scorer.py`)** — two complementary signals:
- **Colour pre-filter** — extracts denim pixels from a tight centre crop (ignoring white backgrounds), hard-rejects obvious mismatches (vivid blue, black, brown, beige). Runs first because it's cheap.
- **CLIP** (`clip-ViT-B-32`) — embeds each surviving image and measures cosine similarity to your reference photos, with disliked references applying a penalty. Thresholds auto-calibrate once you have 4+ references.
- Final score: `0.6 × CLIP + 0.4 × colour`, adjustable from the UI.

**3. Pipeline (`pipeline.py`)** — orchestrates each run: scrape → pre-filter (dedupe, size, price) → parallel image download (12 threads) → sequential CLIP scoring (CLIP isn't thread-safe) → save matches. Listings are committed the instant they pass threshold so they appear on your phone immediately.

**4. Feedback loop** — liking a listing crops it to the jeans region and adds it as a positive reference; disliking adds a negative one. The detector recalibrates on the next run, so it learns your taste over time.

**5. UI (`app.py`)** — Flask app at `http://<your-pc-ip>:5000`. Real-time updates via Server-Sent Events, filter by unseen/liked, sort by score/price/newest, manage queries and settings, mark items as purchased.

---

## Architecture notes

- **Parallel I/O, sequential inference** — image downloads are network-bound and parallelised across 12 threads; CLIP scoring runs single-threaded because the PyTorch model isn't thread-safe. Colour scoring (pure NumPy) runs safely inside the download threads as a pre-filter.
- **Two-stage download** — a small thumbnail is colour-screened before the full image is fetched, avoiding full downloads for ~80% of listings.
- **SQLite in WAL mode** — lets the scraper write while the Flask server reads concurrently without locking.
- **Atomic image writes** — images write to a `.tmp` file then `os.rename`, so the server never serves a half-written file.

---

## Setup

**Requirements:** Python 3.8+

```bash
pip install -r requirements.txt
```

Add a few reference photos of the jeans you're hunting to `reference_images/` (the more the better — 10+ calibrates CLIP well).

Then run the two processes:

```bash
python pipeline.py    # terminal 1 — the scraper loop
python app.py         # terminal 2 — the web UI
```

Open the printed `http://<ip>:5000` address on your phone (same WiFi).

Windows users can double-click `start.bat` to launch both at once.

---

## Where this pattern applies

The core architecture — scrape a marketplace's JSON API, embed images/text, rank by similarity to a reference set, learn from feedback — generalises to any "find me things that look/read like this" problem: Depop/Grailed for vintage, Rightmove for property aesthetics, AutoTrader for a specific car spec, arXiv for similar papers (swap CLIP for a text embedding model), and so on.

---

## Note

Built for personal use. Respect Vinted's terms of service and rate limits. The scraper uses conservative delays between requests.
