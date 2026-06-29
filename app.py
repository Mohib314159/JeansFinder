import os, json, socket, time
from flask import Flask, render_template, jsonify, request, send_from_directory
from db import get_conn, init_db, get_setting, set_setting
from scorer import add_reference_image, list_references, REFERENCE_DIR

app = Flask(__name__)
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
IMAGE_DIR    = os.path.join(BASE_DIR, 'static', 'images')
STATUS_FILE  = os.path.join(BASE_DIR, 'data', 'status.json')
TRIGGER_FILE = os.path.join(BASE_DIR, 'data', 'trigger_scrape')
RESCORE_FILE = os.path.join(BASE_DIR, 'data', 'trigger_rescore')
HB_FILE      = os.path.join(BASE_DIR, 'data', 'heartbeat')

@app.route('/')
def index(): return render_template('index.html')

# ── LISTINGS ──────────────────────────────────────────────────────────────────

@app.route('/api/listings')
def get_listings():
    show = request.args.get('show', 'unseen')
    sort = request.args.get('sort', 'score')
    try:    min_score = float(request.args.get('min_score', 0))
    except: min_score = 0.0
    try:    page = max(1, int(request.args.get('page', 1)))
    except: page = 1
    per_page = int(get_setting('max_feed_size', 60))

    order = {
        'score':     'final_score DESC, seen_at DESC',
        'newest':    'seen_at DESC',
        'price_asc': "CAST(TRIM(REPLACE(REPLACE(price,'£',''),'€','')) AS REAL) ASC",
    }.get(sort, 'final_score DESC')

    where = ['archived=0', 'purchased=0']
    if show == 'liked':    where.append('feedback=1')
    elif show == 'unseen': where.append('feedback=0')
    if min_score > 0:      where.append(f'final_score>={min_score}')

    w    = ' AND '.join(where)
    conn = get_conn()
    try:
        rows  = conn.execute(
            f'SELECT * FROM listings WHERE {w} ORDER BY {order} '
            f'LIMIT {per_page} OFFSET {(page-1)*per_page}'
        ).fetchall()
        total = conn.execute(f'SELECT COUNT(*) FROM listings WHERE {w}').fetchone()[0]
    finally:
        conn.close()
    return jsonify({'items': [dict(r) for r in rows], 'total': total, 'page': page})

@app.route('/api/feedback', methods=['POST'])
def feedback():
    d    = request.json or {}
    lid  = d.get('id')
    vote = d.get('vote')
    if lid is None:
        return jsonify({'ok': False, 'error': 'missing id'}), 400
    conn = get_conn()
    try:
        conn.execute('UPDATE listings SET feedback=?,is_new=0 WHERE id=?', (vote, lid))
        conn.commit()
        if vote in (1, -1):
            row = conn.execute('SELECT local_image FROM listings WHERE id=?', (lid,)).fetchone()
            if row and row['local_image']:
                p = os.path.join(IMAGE_DIR, row['local_image'])
                if os.path.exists(p):
                    try:
                        add_reference_image(p, is_negative=(vote == -1))
                    except Exception as e:
                        print(f'add_reference_image error: {e}')
    finally:
        conn.close()
    return jsonify({'ok': True})

@app.route('/api/purchased', methods=['POST'])
def mark_purchased():
    d   = request.json or {}
    lid = d.get('id')
    if lid is None:
        return jsonify({'ok': False, 'error': 'missing id'}), 400
    conn = get_conn()
    try:
        conn.execute('UPDATE listings SET purchased=1,feedback=1,is_new=0 WHERE id=?', (lid,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True})

@app.route('/api/bulk_dismiss', methods=['POST'])
def bulk_dismiss():
    conn = get_conn()
    try:
        n = conn.execute(
            "UPDATE listings SET feedback=-1,is_new=0 "
            "WHERE feedback=0 AND archived=0 AND purchased=0"
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True, 'dismissed': n})

@app.route('/api/stats')
def stats():
    conn = get_conn()
    try:
        unseen    = conn.execute("SELECT COUNT(*) FROM listings WHERE feedback=0 AND archived=0 AND purchased=0").fetchone()[0]
        liked     = conn.execute("SELECT COUNT(*) FROM listings WHERE feedback=1 AND purchased=0").fetchone()[0]
        purchased = conn.execute("SELECT COUNT(*) FROM listings WHERE purchased=1").fetchone()[0]
    finally:
        conn.close()

    status = {}
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE) as f:
                status = json.load(f)
    except (json.JSONDecodeError, OSError):
        pass

    alive = False
    if os.path.exists(HB_FILE):
        try:
            alive = (time.time() - os.path.getmtime(HB_FILE)) < 180
        except: pass
    if not alive and status.get('scraper_state') == 'running':
        status['scraper_state'] = 'idle'

    return jsonify({'unseen': unseen, 'liked': liked, 'purchased': purchased,
                    **status, 'pipeline_alive': alive})

@app.route('/api/log')
def log():
    conn = get_conn()
    try:
        rows = conn.execute('SELECT * FROM scrape_log ORDER BY id DESC LIMIT 50').fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])

# ── ACTIONS ───────────────────────────────────────────────────────────────────

@app.route('/api/scrape/trigger', methods=['POST'])
def trigger_scrape():
    try:
        open(TRIGGER_FILE, 'w').close()
    except OSError as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': True})

@app.route('/api/rescore/trigger', methods=['POST'])
def trigger_rescore():
    conn = get_conn()
    try:
        conn.execute('UPDATE listings SET rescore_needed=1')
        conn.commit()
    finally:
        conn.close()
    try:
        open(RESCORE_FILE, 'w').close()
    except OSError:
        pass
    return jsonify({'ok': True, 'message': 'Rescore triggered.'})

# ── QUERIES ───────────────────────────────────────────────────────────────────

@app.route('/api/queries')
def get_queries():
    conn = get_conn()
    try:
        rows = conn.execute(
            'SELECT * FROM search_queries ORDER BY enabled DESC,hits_passed DESC,id ASC'
        ).fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/queries/toggle', methods=['POST'])
def toggle_q():
    d       = request.json or {}
    qid     = d.get('id')
    enabled = d.get('enabled')
    if qid is None or enabled is None:
        return jsonify({'ok': False, 'error': 'missing id or enabled'}), 400
    conn = get_conn()
    try:
        conn.execute('UPDATE search_queries SET enabled=? WHERE id=?', (enabled, qid))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True})

@app.route('/api/queries/delete', methods=['POST'])
def delete_q():
    d   = request.json or {}
    qid = d.get('id')
    if qid is None:
        return jsonify({'ok': False, 'error': 'missing id'}), 400
    conn = get_conn()
    try:
        conn.execute('DELETE FROM search_queries WHERE id=?', (qid,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True})

@app.route('/api/queries/add', methods=['POST'])
def add_q():
    d = request.json or {}
    q = d.get('query', '').strip()
    if not q:
        return jsonify({'ok': False, 'error': 'empty query'})
    conn = get_conn()
    try:
        conn.execute('INSERT INTO search_queries (query) VALUES (?)', (q,))
        conn.commit()
        return jsonify({'ok': True})
    except:
        return jsonify({'ok': False, 'error': 'duplicate'})
    finally:
        conn.close()

# ── SETTINGS ──────────────────────────────────────────────────────────────────

@app.route('/api/settings')
def get_settings():
    conn = get_conn()
    try:
        rows = conn.execute('SELECT key,value FROM settings').fetchall()
    finally:
        conn.close()
    return jsonify({r['key']: r['value'] for r in rows})

@app.route('/api/settings/save', methods=['POST'])
def save_setting():
    d   = request.json or {}
    key = d.get('key', '').strip()
    val = d.get('value', '')
    if not key:
        return jsonify({'ok': False, 'error': 'missing key'}), 400
    set_setting(key, val)
    return jsonify({'ok': True})

# ── REFERENCES ────────────────────────────────────────────────────────────────

@app.route('/api/references')
def get_refs():
    return jsonify(list_references())

@app.route('/ref_img/<path:fn>')
def ref_img(fn):
    return send_from_directory(REFERENCE_DIR, fn)

@app.route('/static/images/<path:fn>')
def static_img(fn):
    return send_from_directory(IMAGE_DIR, fn)

if __name__ == '__main__':
    init_db()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
    except:
        ip = 'localhost'
    print(f"\n{'═'*45}")
    print(f"  JeansFinder UI")
    print(f"  PC:    http://localhost:5000")
    print(f"  Phone: http://{ip}:5000")
    print(f"  (Both must be on same WiFi)")
    print(f"{'═'*45}\n")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
