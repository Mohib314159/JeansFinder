import sqlite3, os, json

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'jeansfinder.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=2000')
    return conn

def _col_exists(conn, table, col):
    rows = conn.execute(f'PRAGMA table_info({table})').fetchall()
    return any(r['name'] == col for r in rows)

def _migrate(conn):
    migrations = [
        ('listings',      'brand',               'TEXT DEFAULT ""'),
        ('listings',      'all_photos',           'TEXT DEFAULT "[]"'),
        ('listings',      'best_photo',           'TEXT DEFAULT ""'),
        ('listings',      'purchased',            'INTEGER DEFAULT 0'),
        ('listings',      'clip_score',           'REAL DEFAULT 0'),
        ('listings',      'colour_score',         'REAL DEFAULT 0'),
        ('listings',      'final_score',          'REAL DEFAULT 0'),
        ('listings',      'score_breakdown',      'TEXT DEFAULT ""'),
        ('listings',      'archived',             'INTEGER DEFAULT 0'),
        ('listings',      'rescore_needed',       'INTEGER DEFAULT 0'),
        ('search_queries','query_type',           'TEXT DEFAULT "text"'),
        ('search_queries','use_gender',           'INTEGER DEFAULT 1'),
        ('scrape_log',    'consecutive_zeros',    'INTEGER DEFAULT 0'),
    ]
    for table, col, typedef in migrations:
        try:
            if not _col_exists(conn, table, col):
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typedef}')
                print(f'  Migrated: {table}.{col}')
        except Exception:
            pass
    conn.commit()

def init_db():
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS listings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            vinted_id      TEXT UNIQUE,
            title          TEXT, price TEXT, size TEXT, brand TEXT,
            url            TEXT, image_url TEXT, local_image TEXT,
            all_photos     TEXT DEFAULT '[]',
            clip_score     REAL DEFAULT 0, colour_score REAL DEFAULT 0,
            final_score    REAL DEFAULT 0, score_breakdown TEXT,
            best_photo     TEXT DEFAULT '',
            seen_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_new         INTEGER DEFAULT 1, feedback INTEGER DEFAULT 0,
            purchased      INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
            rescore_needed INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS search_queries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            query       TEXT UNIQUE, enabled INTEGER DEFAULT 1,
            hits_total  INTEGER DEFAULT 0, hits_passed INTEGER DEFAULT 0,
            query_type  TEXT DEFAULT 'text',
            use_gender  INTEGER DEFAULT 1,
            added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS scrape_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ran_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            listings_found    INTEGER DEFAULT 0, listings_passed INTEGER DEFAULT 0,
            duration_s        REAL DEFAULT 0, status TEXT DEFAULT 'ok',
            consecutive_zeros INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        )''')
        _migrate(conn)

        default_queries = [
            ('baggy jeans grey',           'text',  1),
            ('wide leg jeans dark grey',   'text',  1),
            ('y2k baggy jeans',            'text',  1),
            ('rocawear jeans',             'brand', 0),
            ('hip hop jeans baggy',        'text',  1),
            ('jeans wide leg charcoal',    'text',  1),
            ('vintage baggy jeans dark',   'text',  1),
            ('phat farm jeans',            'brand', 0),
            ('fubu jeans baggy',           'brand', 0),
            ('akademiks jeans',            'brand', 0),
            ('jeans baggy dark wash',      'text',  1),
            ('wide leg denim dark',        'text',  1),
            ('southpole jeans dark',       'brand', 0),
            ('ecko unltd jeans',           'brand', 0),
            ('sean john jeans',            'brand', 0),
            ('jnco jeans dark',            'brand', 0),
            ('karl kani jeans dark',       'brand', 0),
            ('girbaud jeans',              'brand', 0),
            ('baggy dark jeans mens 32',   'text',  1),
            ('baggy dark jeans mens 34',   'text',  1),
            ('loose fit jeans charcoal',   'text',  1),
            ('streetwear jeans dark grey', 'text',  1),
        ]
        for q, qt, ug in default_queries:
            c.execute(
                'INSERT OR IGNORE INTO search_queries (query,query_type,use_gender) VALUES (?,?,?)',
                (q, qt, ug)
            )
        for k, v in {
            'score_threshold':    '45',
            'size_filter':        json.dumps(['32','33','34','W32','W33','W34','S','M']),
            'clip_weight':        '0.6',
            'colour_weight':      '0.4',
            'interval_min':       '45',
            'interval_max':       '90',
            'notify_threshold':   '75',
            'archive_after_days': '14',
            'max_feed_size':      '60',
            'multi_photo_score':  'true',
            'use_real_chrome':    'true',
            'run_headless':       'false',
        }.items():
            c.execute('INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)', (k, v))
        conn.commit()
    finally:
        conn.close()
    print("DB ready.")

def get_setting(key, default=None):
    conn = get_conn()
    try:
        r = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return r[0] if r else default
    finally:
        conn.close()

def set_setting(key, value):
    conn = get_conn()
    try:
        conn.execute('INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)', (key, str(value)))
        conn.commit()
    finally:
        conn.close()

def get_enabled_queries():
    conn = get_conn()
    try:
        rows = conn.execute(
            'SELECT query,query_type,use_gender FROM search_queries WHERE enabled=1'
        ).fetchall()
        return [(r['query'], r['query_type'], bool(r['use_gender'])) for r in rows]
    finally:
        conn.close()

def get_consecutive_zeros():
    conn = get_conn()
    try:
        r = conn.execute(
            "SELECT consecutive_zeros FROM scrape_log WHERE status!='error' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return r[0] if r else 0
    finally:
        conn.close()

if __name__ == '__main__':
    init_db()
