# JeansFinder

**Live demo: https://mohib314159.github.io/JeansFinder/** (a real snapshot; like or dislike listings and the feed re-ranks in your browser)

A tool I use to find one specific style of jeans on Vinted. It checks new listings automatically, filters out obviously wrong colours first, then compares the remaining photos against examples I like. Likes and dislikes feed back into what it shows me.

Runs as two processes: a background scraper pipeline that polls Vinted every 45–90 minutes (configurable), and a Flask web UI you open on your phone over the same WiFi.

![scoring](https://img.shields.io/badge/scoring-CLIP_%2B_colour-blue) ![python](https://img.shields.io/badge/python-3.8%2B-green)

---

## How it works

**1. Scraper (`scraper.py`)** — opens each saved search in a Playwright browser using a saved Vinted session (`python scraper.py --login` once), and reads the JSON that Vinted's own page loads from `/api/v2/catalog/items` instead of parsing HTML. That makes it resilient to layout changes.

**2. Scorer (`scorer.py`)** — two complementary signals:
- **Colour pre-filter** — extracts denim pixels from a tight centre crop (ignoring white backgrounds), hard-rejects obvious mismatches (vivid blue, black, brown, beige). Runs first because it's cheap.
- **CLIP** (`clip-ViT-B-32`) — embeds each surviving image and measures cosine similarity to your reference photos, with disliked references applying a penalty. Thresholds auto-calibrate once you have 4+ references.
- Final score: `0.6 × CLIP + 0.4 × colour`, adjustable from the UI.

**3. Pipeline (`pipeline.py`)**: each run scrapes, skips listings it has already seen or that aren't in my sizes, scores the first photo (and up to three more if the first one's colour is plausible), and saves anything above the threshold straight away so it shows up in the feed during the run. A desktop notification fires for very high scores.

**4. Feedback loop**: liking a listing crops the photo to the jeans and adds it as a reference; disliking adds it as a negative reference. Scores recalibrate from the references on the next run.

**5. UI (`app.py`)**: a Flask app at `http://<your-pc-ip>:5000` that I open on my phone on the same WiFi. Feed sorted by score, price or newest; like/dislike; mark as bought; edit searches and settings. It refreshes stats every 30 seconds.

---

## Engineering notes

- **SQLite in WAL mode** so the scraper can write while the web app reads, without locking.
- **Atomic image writes**: images go to a `.tmp` file and are renamed into place, so the app never serves a half-written file.
- **Cheap check first**: the colour filter is plain NumPy and runs before CLIP, so most wrong listings never reach the slow model.
- **Scores calibrate themselves**: once there are 4+ references, the CLIP score range is set from how similar the references are to each other (10th to 80th percentile), rather than hard-coded.

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

## Demo site

`docs/` is a static page for GitHub Pages. `python export_demo.py` runs one real scrape and saves listing thumbnails, colour stats and CLIP embeddings to `docs/data/`. The page then re-implements the scoring from `scorer.py` in JavaScript, so likes and dislikes re-rank the feed live without a server.

## Where this pattern applies

The core architecture — scrape a marketplace's JSON API, embed images/text, rank by similarity to a reference set, learn from feedback — generalises to any "find me things that look/read like this" problem: Depop/Grailed for vintage, Rightmove for property aesthetics, AutoTrader for a specific car spec, arXiv for similar papers (swap CLIP for a text embedding model), and so on.

---

## Note

Built for personal use. Respect Vinted's terms of service and rate limits. The scraper uses conservative delays between requests.
