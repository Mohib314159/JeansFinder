import os, pickle, json, io
import numpy as np
from PIL import Image

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
REFERENCE_DIR = os.path.join(BASE_DIR, 'reference_images')
EMBED_CACHE   = os.path.join(BASE_DIR, 'data', 'ref_embeddings.pkl')
COLOUR_CACHE  = os.path.join(BASE_DIR, 'data', 'colour_target.json')

os.makedirs(REFERENCE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(EMBED_CACHE), exist_ok=True)

_model         = None
_pos_embs      = None
_neg_embs      = None
_ref_mtime     = 0
_target_colour = None
_clip_lo       = 0.17
_clip_hi       = 0.43

# ── COLOUR ────────────────────────────────────────────────────────────────────

def extract_jeans_pixels(img: Image.Image) -> np.ndarray:
    # tight centre crop: avoids white listing backgrounds, phone status bars,
    # and studio grey seamless paper
    w, h = img.size
    crop = img.crop((int(w*0.20), int(h*0.20), int(w*0.80), int(h*0.80)))
    arr  = np.array(crop.resize((60, 80), Image.LANCZOS)).astype(float)
    flat = arr.reshape(-1, 3)

    not_white      = np.all(flat < 210, axis=1)
    not_black      = np.all(flat > 45,  axis=1)   # raised to catch dark phone borders
    not_light_grey = flat.mean(axis=1) < 185
    max_c  = flat.max(axis=1)
    min_c  = flat.min(axis=1)
    sat    = (max_c - min_c) / (max_c + 1)
    not_vivid = sat < 0.45

    mask = not_white & not_black & not_light_grey & not_vivid
    px   = flat[mask]
    return px if len(px) > 40 else flat

def compute_target_colour() -> np.ndarray:
    global _target_colour
    all_px = []
    for fname in os.listdir(REFERENCE_DIR):
        if not fname.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')): continue
        if any(x in fname.lower() for x in ['label', 'tag', 'dislike', 'negative']): continue
        try:
            img = Image.open(os.path.join(REFERENCE_DIR, fname)).convert('RGB')
            all_px.append(extract_jeans_pixels(img))
        except: pass
    if not all_px:
        _target_colour = np.array([108., 112., 118.])
    else:
        _target_colour = np.median(np.vstack(all_px), axis=0)
    rgb = _target_colour
    print(f"  Colour target: RGB({rgb[0]:.0f}, {rgb[1]:.0f}, {rgb[2]:.0f})")
    with open(COLOUR_CACHE, 'w') as f: json.dump(_target_colour.tolist(), f)
    return _target_colour

def load_target_colour() -> np.ndarray:
    global _target_colour
    if _target_colour is not None: return _target_colour
    if os.path.exists(COLOUR_CACHE):
        with open(COLOUR_CACHE) as f: _target_colour = np.array(json.load(f))
    else:
        _target_colour = compute_target_colour()
    return _target_colour

# colours that are definitely not dark/charcoal denim
HARD_REJECTS = {
    'vivid_blue':  (np.array([50,  75, 160]), 38),
    'indigo_blue': (np.array([55,  70, 130]), 35),
    'too_light':   (np.array([195, 195, 195]), 45),
    'warm_brown':  (np.array([155, 105,  65]), 38),
    'pure_black':  (np.array([35,   35,  35]), 22),
    'olive':       (np.array([90,  110,  70]), 32),
    'red_tone':    (np.array([165,  55,  55]), 42),
    'beige':       (np.array([200, 185, 160]), 40),
}

def colour_score(img: Image.Image):
    px       = extract_jeans_pixels(img)
    mean_rgb = px.mean(axis=0)

    for reason, (ref, thresh) in HARD_REJECTS.items():
        if np.linalg.norm(mean_rgb - ref) < thresh:
            return 0.0, f"reject:{reason}({mean_rgb[0]:.0f},{mean_rgb[1]:.0f},{mean_rgb[2]:.0f})"

    target    = load_target_colour()
    dist      = np.linalg.norm(mean_rgb - target)
    std       = px.std(axis=0).mean()
    tex_bonus = min(12.0, std / 2.5)   # charcoal denim has visible grain
    score     = max(0.0, (95.0 - dist) / 95.0 * 100.0) + tex_bonus
    return round(min(100.0, score), 1), f"dist:{dist:.1f} tex:{std:.1f} ({mean_rgb[0]:.0f},{mean_rgb[1]:.0f},{mean_rgb[2]:.0f})"

# ── CLIP ──────────────────────────────────────────────────────────────────────

def get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            print("  Loading CLIP (~400MB first time)...")
            _model = SentenceTransformer('clip-ViT-B-32')
            print("  CLIP ready.")
        except Exception as e:
            print(f"  CLIP unavailable ({e}). Colour-only mode active.")
            _model = False
    return _model if _model is not False else None

def refs_changed() -> bool:
    global _ref_mtime
    try:
        files = [os.path.join(REFERENCE_DIR, f) for f in os.listdir(REFERENCE_DIR)
                 if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if not files: return False
        mt = max(os.path.getmtime(f) for f in files)
        if mt > _ref_mtime: _ref_mtime = mt; return True
    except: pass
    return False

def load_embeddings(force=False):
    global _pos_embs, _neg_embs, _clip_lo, _clip_hi
    if _pos_embs is not None and not force and not refs_changed():
        return _pos_embs, _neg_embs

    model = get_model()
    if model is None: return None, None

    pos_imgs, neg_imgs = [], []

    for fname in sorted(os.listdir(REFERENCE_DIR)):
        if not fname.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')): continue
        if any(x in fname.lower() for x in ['label', 'tag']): continue
        try:
            img    = Image.open(os.path.join(REFERENCE_DIR, fname)).convert('RGB')
            is_neg = any(x in fname.lower() for x in ['dislike', 'negative'])
            if is_neg: neg_imgs.append(img)
            else:      pos_imgs.append(img)
        except: pass

    if not pos_imgs: print("  No positive refs."); return None, None

    print(f"  Embedding {len(pos_imgs)} pos + {len(neg_imgs)} neg refs...")
    def embed(imgs):
        e = model.encode(imgs, convert_to_numpy=True, show_progress_bar=False)
        return e / np.linalg.norm(e, axis=1, keepdims=True)

    _pos_embs = embed(pos_imgs)
    _neg_embs = embed(neg_imgs) if neg_imgs else None

    if len(_pos_embs) >= 4:
        sims = _pos_embs @ _pos_embs.T
        np.fill_diagonal(sims, 0)
        all_sims = sims[sims > 0].flatten()
        _clip_lo = float(np.percentile(all_sims, 10))
        _clip_hi = float(np.percentile(all_sims, 80))
        print(f"  CLIP calibrated: lo={_clip_lo:.3f} hi={_clip_hi:.3f}")
    else:
        _clip_lo, _clip_hi = 0.17, 0.43
        print(f"  CLIP using defaults ({len(_pos_embs)} refs — need 4+ to calibrate)")

    with open(EMBED_CACHE, 'wb') as f:
        pickle.dump({'pos': _pos_embs, 'neg': _neg_embs}, f)
    return _pos_embs, _neg_embs

def clip_score(img: Image.Image):
    model = get_model()
    pos_embs, neg_embs = load_embeddings()
    if model is None or pos_embs is None: return None, 'clip:unavailable'

    emb = model.encode([img], convert_to_numpy=True, show_progress_bar=False)[0]
    emb = emb / np.linalg.norm(emb)

    top_k    = min(3, len(pos_embs))
    pos_sims = pos_embs @ emb
    top_avg  = float(np.mean(np.sort(pos_sims)[-top_k:]))

    neg_pen = 0.0
    if neg_embs is not None:
        neg_pen = float(np.max(neg_embs @ emb)) * 0.35

    raw   = top_avg - neg_pen
    score = (raw - _clip_lo) / max(0.001, _clip_hi - _clip_lo) * 100
    return round(max(0.0, min(100.0, score)), 1), f"sim:{top_avg:.3f} pen:{neg_pen:.3f}"

# ── SCORING ───────────────────────────────────────────────────────────────────

def score_pil(img: Image.Image, clip_w=0.6, col_w=0.4) -> dict:
    c_sc, c_r = colour_score(img)
    if c_sc == 0.0 and 'reject' in c_r:
        return {'clip': 0, 'colour': 0, 'final': 0, 'breakdown': c_r, 'best_url': ''}
    cl_sc, cl_r = clip_score(img)
    if cl_sc is None:
        return {'clip': 0, 'colour': c_sc, 'final': c_sc, 'breakdown': f'colour_only|{c_r}', 'best_url': ''}
    final = round(min(100.0, clip_w * cl_sc + col_w * c_sc), 1)
    return {'clip': cl_sc, 'colour': c_sc, 'final': final, 'breakdown': f"{cl_r}|{c_r}", 'best_url': ''}

def score_image(img_or_path, clip_w=0.6, col_w=0.4) -> dict:
    if isinstance(img_or_path, str):
        try: img = Image.open(img_or_path).convert('RGB')
        except: return {'clip': 0, 'colour': 0, 'final': 0, 'breakdown': 'load_error', 'best_url': ''}
    else:
        img = img_or_path.convert('RGB')
    return score_pil(img, clip_w, col_w)

def score_multi_photo(photo_urls: list, clip_w: float, col_w: float,
                      initial_colour_score: float) -> dict:
    import requests as req
    best = {'clip': 0, 'colour': 0, 'final': 0, 'breakdown': '', 'best_url': ''}
    # skip if first photo was already clearly wrong colour — avoids bulk HTTP reqs
    if initial_colour_score < 25:
        return best
    for url in photo_urls[1:4]:
        try:
            r = req.get(url, timeout=7, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code != 200: continue
            img    = Image.open(io.BytesIO(r.content)).convert('RGB')
            result = score_pil(img, clip_w, col_w)
            if result['final'] > best['final']:
                best = {**result, 'best_url': url}
        except: continue
    return best

def crop_jeans_region(img: Image.Image) -> Image.Image:
    w, h = img.size
    return img.crop((int(w*0.1), int(h*0.15), int(w*0.9), int(h*0.87)))

def add_reference_image(img_path: str, is_negative=False):
    fname  = os.path.basename(img_path)
    prefix = 'feedback_dislike_' if is_negative else 'feedback_like_'
    dest   = os.path.join(REFERENCE_DIR, f"{prefix}{fname}")
    if not os.path.exists(dest):
        try:
            img     = Image.open(img_path).convert('RGB')
            cropped = crop_jeans_region(img)
            tmp     = dest + '.tmp'
            cropped.save(tmp, 'PNG')
            os.rename(tmp, dest)
        except:
            import shutil; shutil.copy(img_path, dest)
    global _target_colour
    _target_colour = None
    compute_target_colour()
    load_embeddings(force=True)
    print(f"  Ref {'(neg)' if is_negative else '(pos)'} added: {fname}")

def rescore_all(clip_w=0.6, col_w=0.4):
    from db import get_conn
    import requests as req
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id,image_url,local_image FROM listings "
            "WHERE rescore_needed=1 OR final_score=0"
        ).fetchall()
        print(f"Rescoring {len(rows)} listings...")
        IMAGE_DIR_local = os.path.join(BASE_DIR, 'static', 'images')
        for row in rows:
            img = None
            if row['local_image']:
                p = os.path.join(IMAGE_DIR_local, row['local_image'])
                if os.path.exists(p):
                    try: img = Image.open(p).convert('RGB')
                    except: pass
            if img is None and row['image_url']:
                try:
                    r = req.get(row['image_url'], timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
                    if r.status_code == 200:
                        img = Image.open(io.BytesIO(r.content)).convert('RGB')
                except: pass
            if img:
                scores = score_pil(img, clip_w, col_w)
                conn.execute(
                    'UPDATE listings SET clip_score=?,colour_score=?,final_score=?,'
                    'score_breakdown=?,rescore_needed=0 WHERE id=?',
                    (scores['clip'], scores['colour'], scores['final'],
                     scores['breakdown'], row['id'])
                )
        conn.commit()
    finally:
        conn.close()
    print("Rescore complete.")

def list_references():
    refs = []
    for fname in sorted(os.listdir(REFERENCE_DIR)):
        if not fname.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')): continue
        if any(x in fname.lower() for x in ['label', 'tag']): continue
        refs.append({
            'name':          fname,
            'is_negative':   any(x in fname.lower() for x in ['dislike', 'negative']),
            'from_feedback': 'feedback' in fname.lower(),
        })
    return refs

if __name__ == '__main__':
    compute_target_colour()
    load_embeddings(force=True)
    print("Scorer ready.")
