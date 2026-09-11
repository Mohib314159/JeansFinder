"""Export a snapshot for the public web demo (docs/).

The live tool can't run on a public website: it needs a Vinted session and a machine
running CLIP. So this script does one real run and saves everything the browser needs to
redo the ranking itself: listing thumbnails, colour stats, and CLIP embeddings for each
listing and each reference photo. The page in docs/ then re-scores everything live in
JavaScript with the same formula as scorer.py whenever you like or dislike something.

    python scraper.py --login          # once, if you don't have a session yet
    python export_demo.py              # -> docs/data/feed.json + docs/data/img/
    python export_demo.py --queries 4 --max 120

Needs a few photos of the jeans you want in reference_images/ (same as the main app).
"""
from __future__ import annotations

import argparse, base64, io, json, os, sys, time
from datetime import datetime, timezone

import numpy as np
import requests
from PIL import Image

import scorer
from scorer import (extract_jeans_pixels, colour_score, crop_jeans_region, get_model,
                    load_embeddings, compute_target_colour, REFERENCE_DIR, HARD_REJECTS)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "docs", "data")
IMG = os.path.join(OUT, "img")
THUMB_W = 360


def q8(v: np.ndarray) -> str:
    """Unit vector -> int8 -> base64. Cosine similarity survives this to ~0.005."""
    v = v / (np.linalg.norm(v) + 1e-9)
    return base64.b64encode(np.clip(np.round(v * 127), -127, 127).astype(np.int8).tobytes()).decode()


def embed(model, imgs):
    e = model.encode(imgs, convert_to_numpy=True, show_progress_bar=False)
    return e / np.linalg.norm(e, axis=1, keepdims=True)


def colour_stats(img):
    px = extract_jeans_pixels(img)
    return px.mean(axis=0), float(px.std(axis=0).mean()), np.median(px, axis=0)


def save_thumb(img, name):
    w, h = img.size
    t = img.resize((THUMB_W, int(h * THUMB_W / w)), Image.LANCZOS) if w > THUMB_W else img
    t.save(os.path.join(IMG, name), "JPEG", quality=78, optimize=True)
    return f"data/img/{name}"


def fetch(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=5, help="how many saved searches to run")
    ap.add_argument("--max", type=int, default=120, help="max listings in the snapshot")
    ap.add_argument("--max-rejects", type=int, default=24, help="colour-rejected listings to keep")
    args = ap.parse_args()

    os.makedirs(IMG, exist_ok=True)
    model = get_model()
    if model is None:
        sys.exit("CLIP didn't load: pip install sentence-transformers")
    compute_target_colour()
    pos, neg = load_embeddings(force=True)
    if pos is None:
        sys.exit("Add a few photos of the jeans you want to reference_images/ first.")

    # reference photos (the taste profile the page starts from)
    refs = []
    for f in sorted(os.listdir(REFERENCE_DIR)):
        if not f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) or any(x in f.lower() for x in ("label", "tag")):
            continue
        img = Image.open(os.path.join(REFERENCE_DIR, f)).convert("RGB")
        _, _, med = colour_stats(img)
        refs.append({"id": f"ref{len(refs)}", "img": save_thumb(img, f"ref{len(refs)}.jpg"),
                     "neg": any(x in f.lower() for x in ("dislike", "negative")),
                     "emb": q8(embed(model, [img])[0]), "median_rgb": [round(float(c), 1) for c in med]})

    from db import init_db, get_enabled_queries
    from scraper import run_scrape
    init_db()
    listings = run_scrape(get_enabled_queries()[: args.queries])
    if not listings:
        sys.exit("Scrape returned nothing. Run: python scraper.py --login")

    out, rejects = [], 0
    for item in listings:
        if len(out) >= args.max:
            break
        photos = []
        try:
            photos = json.loads(item.get("all_photos", "[]"))
        except Exception:
            pass
        url = photos[0] if photos else item.get("image_url")
        img = fetch(url) if url else None
        if img is None:
            continue
        c_score, c_reason = colour_score(img)
        is_reject = c_score == 0.0 and "reject" in c_reason
        if is_reject:
            if rejects >= args.max_rejects:
                continue
            rejects += 1
        mean, tex, med = colour_stats(img)
        e_full, e_crop = embed(model, [img, crop_jeans_region(img)])
        vid = str(item.get("id"))
        out.append({"id": vid, "title": item.get("title", ""), "brand": item.get("brand", ""),
                    "price": item.get("price", ""), "size": item.get("size", ""), "url": item.get("url", ""),
                    "img": save_thumb(img, f"{vid}.jpg"),
                    "mean_rgb": [round(float(c), 1) for c in mean], "median_rgb": [round(float(c), 1) for c in med],
                    "texture": round(tex, 2), "colour_reason": c_reason,
                    "emb": q8(e_full), "emb_crop": q8(e_crop)})
        time.sleep(0.2)

    feed = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "queries": [q for q, _, _ in get_enabled_queries()[: args.queries]],
            "weights": {"clip": 0.6, "colour": 0.4},
            "hard_rejects": {k: [v[0].tolist(), v[1]] for k, v in HARD_REJECTS.items()},
            "refs": refs, "listings": out}
    with open(os.path.join(OUT, "feed.json"), "w") as f:
        json.dump(feed, f)
    print(f"Exported {len(out)} listings ({rejects} colour rejects) and {len(refs)} references -> docs/data/")
    print("Open docs/index.html via a local server (python -m http.server -d docs) to check it.")


if __name__ == "__main__":
    main()
