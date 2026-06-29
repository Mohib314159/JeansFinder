import os, time, random, json, io, threading
from datetime import datetime, timedelta
import requests
from PIL import Image

from db import (get_conn, init_db, get_setting, set_setting,
                get_enabled_queries, get_consecutive_zeros)
from scraper import run_scrape, download_image, write_status
from scorer import (score_pil, score_multi_photo, load_embeddings,
                    compute_target_colour)

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE  = os.path.join(BASE_DIR, 'data', 'status.json')
TRIGGER_FILE = os.path.join(BASE_DIR, 'data', 'trigger_scrape')
HB_FILE      = os.path.join(BASE_DIR, 'data', 'heartbeat')
RESCORE_FILE = os.path.join(BASE_DIR, 'data', 'trigger_rescore')

# ── HEARTBEAT ─────────────────────────────────────────────────────────────────

_hb_running = True

def _heartbeat_thread():
    while _hb_running:
        try:
            with open(HB_FILE, 'w') as f: f.write(datetime.now().isoformat())
        except: pass
        time.sleep(30)

def start_heartbeat():
    t = threading.Thread(target=_heartbeat_thread, daemon=True)
    t.start()

# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────

def notify(title: str, msg: str):
    try:
        from plyer import notification
        notification.notify(title=title, message=msg, app_name='JeansFinder', timeout=10)
    except: pass

# ── HELPERS ───────────────────────────────────────────────────────────────────

import re as _re

def parse_size(s: str) -> str:
    if not s: return ''
    u = s.upper().strip()
    m = _re.search(r'W(\d{2})', u)
    if m: return f"W{m.group(1)}"
    m = _re.search(r'\b(28|29|30|31|32|33|34|35|36|38|40)\b', u)
    if m: return m.group(1)
    for tag in ['XS', 'S', 'M', 'L', 'XL', 'XXL']:
        if _re.search(rf'\b{tag}\b', u): return tag
    return s[:12]

def size_ok(raw: str) -> bool:
    try:    allowed = json.loads(get_setting('size_filter', '[]'))
    except: allowed = []
    if not allowed or not raw: return True
    norm = parse_size(raw).upper()
    return any(a.upper() in norm or norm in a.upper() for a in allowed)

def fetch_image(url: str):
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert('RGB')
    except: pass
    return None

def archive_old():
    try:
        days   = int(get_setting('archive_after_days', 14))
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        conn   = get_conn()
        try:
            n = conn.execute(
                "UPDATE listings SET archived=1 WHERE feedback=0 AND purchased=0 "
                "AND seen_at<? AND archived=0", (cutoff,)
            ).rowcount
            conn.commit()
        finally:
            conn.close()
        if n: print(f"  Archived {n} old unseen listings.")
    except Exception as e:
        print(f"  Archive error: {e}")

# ── MAIN RUN ──────────────────────────────────────────────────────────────────

def run_once() -> int:
    t0 = datetime.now()
    print(f"\n{'═'*55}")
    print(f"  JeansFinder  {t0.strftime('%d %b %Y  %H:%M:%S')}")
    print(f"{'═'*55}")
    write_status({'scraper_state': 'running', 'last_run_start': t0.isoformat()})

    init_db(); archive_old()

    threshold   = float(get_setting('score_threshold', 45))
    clip_w      = float(get_setting('clip_weight', 0.6))
    col_w       = float(get_setting('colour_weight', 0.4))
    multi_photo = get_setting('multi_photo_score', 'true').lower() == 'true'
    notify_thr  = float(get_setting('notify_threshold', 75))

    print("  Calibrating scorer...")
    compute_target_colour()
    load_embeddings()

    queries  = get_enabled_queries()
    listings = run_scrape(queries)

    if not listings:
        zeros = get_consecutive_zeros() + 1
        _log(t0, 0, 0, (datetime.now()-t0).total_seconds(), 'no_results', zeros)
        write_status({'scraper_state': 'idle', 'consecutive_zeros': zeros,
                      'last_run_end': datetime.now().isoformat()})
        if zeros >= 3:
            notify('JeansFinder ⚠️', f'{zeros} runs returned 0 results. Check session.')
        return 0

    print(f"\n  Scoring {len(listings)} listings...")
    passed = 0; urgent = 0
    conn   = get_conn()
    try:
        for i, item in enumerate(listings):
            vid = str(item.get('id', ''))
            if not vid: continue
            if conn.execute('SELECT id FROM listings WHERE vinted_id=?', (vid,)).fetchone(): continue
            if not size_ok(item.get('size', '')): continue

            all_photos = []
            try: all_photos = json.loads(item.get('all_photos', '[]'))
            except: pass
            if not all_photos and item.get('image_url'): all_photos = [item['image_url']]

            scores = {'clip': 0, 'colour': 0, 'final': 0, 'breakdown': 'no_image', 'best_url': ''}

            if all_photos:
                img = fetch_image(all_photos[0])
                if img:
                    scores = score_pil(img, clip_w, col_w)
                    scores['best_url'] = all_photos[0]

                    if multi_photo and len(all_photos) > 1 and scores['colour'] >= 25:
                        extra = score_multi_photo(all_photos, clip_w, col_w, scores['colour'])
                        if extra['final'] > scores['final']:
                            scores = {**extra}

            final = scores['final']
            if i % 15 == 0 or final >= threshold:
                mark = '✓' if final >= threshold else '·'
                brand = item.get('brand', '')
                print(f"  {mark} {final:>5.1f}%  {(brand+' ') if brand else ''}{item.get('title','')[:45]}")

            if final >= threshold:
                best_url = scores.get('best_url', '') or (all_photos[0] if all_photos else '')
                local    = download_image(best_url, vid) if best_url else None

                conn.execute('''
                    INSERT OR IGNORE INTO listings
                    (vinted_id,title,price,size,brand,url,image_url,local_image,
                     all_photos,clip_score,colour_score,final_score,score_breakdown,best_photo,is_new)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                ''', (
                    vid, item.get('title', ''), item.get('price', ''),
                    parse_size(item.get('size', '')), item.get('brand', ''),
                    item.get('url', ''), all_photos[0] if all_photos else '',
                    local, json.dumps(all_photos[:6]),
                    scores['clip'], scores['colour'], final,
                    scores['breakdown'], scores.get('best_url', ''),
                ))
                conn.commit()
                passed += 1

                if final >= notify_thr:
                    urgent += 1
                    notify(f'JeansFinder 🔥 {final:.0f}%!',
                           f"{item.get('brand','')} {item.get('title','')} — {item.get('price','')} {item.get('size','')}")
    finally:
        conn.close()

    duration = (datetime.now()-t0).total_seconds()
    _log(t0, len(listings), passed, duration, 'ok', 0)
    write_status({'scraper_state': 'idle', 'last_run_end': datetime.now().isoformat(),
                  'last_run_found': len(listings), 'last_run_passed': passed, 'consecutive_zeros': 0})

    print(f"\n  ✓ {passed} saved ({urgent} urgent) in {duration:.0f}s")
    if passed > 0 and urgent == 0:
        notify('JeansFinder 👖', f"{passed} new match{'es' if passed>1 else ''} found. Open the app.")
    return passed

def _log(t0, found, passed, dur, status, zeros=0):
    conn = get_conn()
    try:
        conn.execute(
            'INSERT INTO scrape_log (ran_at,listings_found,listings_passed,duration_s,status,consecutive_zeros) '
            'VALUES (?,?,?,?,?,?)',
            (t0.strftime('%Y-%m-%d %H:%M:%S'), found, passed, round(dur, 1), status, zeros)
        )
        conn.commit()
    finally:
        conn.close()

# ── FOREVER LOOP ──────────────────────────────────────────────────────────────

def run_forever():
    start_heartbeat()
    print("\n  JeansFinder running. Open http://localhost:5000 on your phone.")

    while True:
        skip_scrape = False

        if os.path.exists(RESCORE_FILE):
            try: os.remove(RESCORE_FILE)
            except: pass
            print("  Rescore triggered...")
            try:
                from scorer import rescore_all
                rescore_all(float(get_setting('clip_weight', 0.6)),
                            float(get_setting('colour_weight', 0.4)))
            except Exception as e:
                print(f"  Rescore error: {e}")
            skip_scrape = True

        if os.path.exists(TRIGGER_FILE):
            try: os.remove(TRIGGER_FILE)
            except: pass
            skip_scrape = False  # explicit scrape trigger overrides rescore-only skip

        if skip_scrape:
            time.sleep(60)
            continue

        try:
            run_once()
        except Exception as e:
            print(f"\n  Error: {e}")
            import traceback; traceback.print_exc()
            _log(datetime.now(), 0, 0, 0, f'error:{str(e)[:80]}', 0)
            write_status({'scraper_state': 'error', 'last_error': str(e)})
            print("  Retrying in 10m...")
            time.sleep(600); continue

        imin = int(get_setting('interval_min', 45))
        imax = int(get_setting('interval_max', 90))
        if imax <= imin: imax = imin + 15
        wait = random.randint(imin*60, imax*60)
        nxt  = datetime.now() + timedelta(seconds=wait)
        write_status({'next_run': nxt.strftime('%H:%M'), 'next_run_ts': nxt.isoformat()})
        print(f"\n  Next run in {wait//60}m {wait%60}s (~{nxt.strftime('%H:%M')})")

        slept = 0
        while slept < wait:
            chunk = min(30, wait - slept)
            time.sleep(chunk); slept += chunk
            if os.path.exists(TRIGGER_FILE) or os.path.exists(RESCORE_FILE): break

if __name__ == '__main__':
    import sys
    if '--once' in sys.argv: run_once()
    else: run_forever()
