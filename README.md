# JeansFinder

A personal tool that monitors Vinted for dark/charcoal baggy jeans, scores each listing using CLIP embeddings + colour analysis, and surfaces the best matches in a mobile-friendly feed.

Runs as two processes: a background scraper pipeline that checks Vinted every 45–90 minutes, and a Flask web UI you can open on your phone while on the same WiFi.

![score flow](https://img.shields.io/badge/scoring-CLIP_%2B_colour-blue) ![python](https://img.shields.io/badge/python-3.11%2B-green) ![platform](https://img.shields.io/badge/platform-Windows-lightgrey)

---

## How it works

1. **Scraper** (`scraper.py`) — uses Playwright with real Chrome (non-headless, moved off-screen) to intercept Vinted's internal `/api/v2/catalog/items` API responses. Runs multiple search queries, deduplicates results, and saves a session cookie for subsequent runs.

2. **Scorer** (`scorer.py`) — dual signal:
   - **CLIP** (`clip-ViT-B-32` via sentence-transformers): compares each listing image against a set of positive/negative reference images using cosine similarity. Calibrates score thresholds automatically once you have 4+ references.
   - **Colour**: extracts pixels from a tight centre crop (avoids white backgrounds and phone borders), rejects hard mismatches (vivid blue denim, pure black, beige, etc.), then scores distance to a target colour computed from your references.
   - Final score: `0.6 × CLIP + 0.4 × colour` by default (adjustable in settings).

3. **Feedback loop** — liking/disliking a listing crops it to the jeans region and adds it as a positive/negative reference image, which recalibrates CLIP and the colour target for the next run.

4. **UI** (`app.py`) — Flask app serving a mobile-friendly feed at `http://<your-pc-ip>:5000`. Filter by unseen/liked, sort by score/price/newest, trigger scrapes manually, manage queries and settings.

---

## Setup

**Requirements:** Python 3.11+, Google Chrome installed, Windows (for `start.bat`; Linux/Mac users can run `pipeline.py` and `app.py` directly).

```bash
pip install -r requirements.txt
playwright install chrome
```

Then just run:

```
start.bat
```

On first run it opens a browser window for you to log into a throwaway Vinted account. After that it's fully automatic.

**Folder structure after first run:**

```
JeansFinder/
├── templates/
│   └── index.html          ← UI template (must be here, not root)
├── static/images/          ← downloaded listing images
├── reference_images/       ← your reference jeans photos
├── data/
│   ├── jeansfinder.db
│   ├── vinted_session.json
│   └── status.json
├── logs/
│   └── pipeline.log
├── app.py
├── db.py
├── pipeline.py
├── scorer.py
├── scraper.py
├── start.bat
└── requirements.txt
```

---

## Customising for your item

The default queries target dark/charcoal baggy jeans in sizes 32–34. To repurpose this for anything else:

1. **Queries** — edit the `default_queries` list in `db.py`, or add/remove them live from the UI under the Queries tab.
2. **Size filter** — change `size_filter` in `db.py` defaults, or update it in Settings.
3. **Reference images** — drop photos of what you want (or don't want) into `reference_images/`. Filenames containing `dislike` or `negative` are treated as negatives.
4. **Colour target** — computed automatically from your positive references. Put ~5 photos of your ideal item in `reference_images/` before the first run.
5. **Score threshold** — default 45. Raise it to be more selective, lower it to see more. Adjustable in Settings.

---

## Settings

| Key | Default | Description |
|-----|---------|-------------|
| `score_threshold` | 45 | Minimum score to save a listing |
| `clip_weight` / `colour_weight` | 0.6 / 0.4 | Score blend weights |
| `interval_min` / `interval_max` | 45 / 90 | Minutes between scrape runs (randomised) |
| `notify_threshold` | 75 | Score above which you get an urgent desktop notification |
| `archive_after_days` | 14 | Unseen listings older than this get hidden |
| `size_filter` | `["32","33","34","W32","W33","W34","S","M"]` | JSON array of size strings |
| `multi_photo_score` | true | Score up to 3 extra photos per listing if the first colour score is ≥ 25 |
| `use_real_chrome` | true | Use installed Chrome instead of Playwright's bundled Chromium |
| `run_headless` | false | Run browser headless (less detectable off-screen; headless is more likely to get blocked) |

---

## Anti-detection notes

Vinted uses Datadome. The scraper avoids detection by:
- Using real Chrome (`channel='chrome'`) rather than Playwright's bundled Chromium
- Running non-headless with the window moved off-screen (`--window-position=-32000,-32000`)
- Randomising user-agent Chrome version, viewport, and inter-query delays (4–9s)
- Rotating query order each run
- Saving and reusing session cookies

If you start getting blocked (status shows `session_expired`), delete `data/vinted_session.json` and re-run `start.bat` to reauthenticate. For persistent blocking, switching the cookie fetch to [`curl-cffi`](https://github.com/yifeikong/curl_cffi) with `impersonate="chrome131"` is the next step up.

---

## Running on Linux/Mac

```bash
# terminal 1 — scraper
python pipeline.py

# terminal 2 — UI
python app.py
```

Login flow: `python scraper.py --login`

---

## Limitations

- Vinted UK only (`.co.uk`). Changing the domain in `scraper.py` and `check_session` will get you other markets.
- Session cookies expire — manual re-login required when they do.
- CLIP model is ~400MB and downloads on first run.
- Colour scoring works best when reference images have plain/neutral backgrounds.
