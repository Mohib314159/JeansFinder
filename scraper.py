import os, json, time, random, re
from urllib.parse import quote
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(BASE_DIR, 'data', 'vinted_session.json')
STATUS_FILE  = os.path.join(BASE_DIR, 'data', 'status.json')
IMAGE_DIR    = os.path.join(BASE_DIR, 'static', 'images')
os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)

class SessionExpiredError(Exception): pass

def delay(a=1.2, b=3.5): time.sleep(random.uniform(a, b))

def write_status(d: dict):
    try:
        cur = {}
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE) as f:
                    cur = json.load(f)
            except (json.JSONDecodeError, OSError):
                cur = {}
        cur.update(d)
        with open(STATUS_FILE, 'w') as f:
            json.dump(cur, f)
    except Exception:
        pass

def session_exists(): return os.path.exists(SESSION_FILE)

def save_session(ctx):
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    with open(SESSION_FILE, 'w') as f: json.dump(ctx.storage_state(), f)

def get_browser_args(offscreen=False):
    args = [
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox', '--disable-dev-shm-usage',
        '--disable-web-security', '--disable-features=IsolateOrigins',
        '--disable-infobars', '--ignore-certificate-errors',
    ]
    if offscreen:
        args.append('--window-position=-32000,-32000')
    return args

def launch_browser(p, headless=False, use_real_chrome=True, offscreen=False):
    launch_kwargs = dict(headless=headless, args=get_browser_args(offscreen=offscreen))
    if use_real_chrome:
        try:
            browser = p.chromium.launch(channel='chrome', **launch_kwargs)
        except Exception:
            try:
                browser = p.chromium.launch(channel='msedge', **launch_kwargs)
            except Exception:
                browser = p.chromium.launch(**launch_kwargs)
    else:
        browser = p.chromium.launch(**launch_kwargs)

    ctx = browser.new_context(
        user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            f'Chrome/12{random.randint(0,5)}.0.0.0 Safari/537.36'
        ),
        locale='en-GB', timezone_id='Europe/London',
        viewport={'width': random.randint(1280,1440), 'height': random.randint(768,900)},
        **(({'storage_state': SESSION_FILE}) if session_exists() else {}),
    )
    ctx.add_init_script('''
        Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
        Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
        Object.defineProperty(navigator,'languages',{get:()=>['en-GB','en-US','en']});
        window.chrome={runtime:{},loadTimes:()=>{},csi:()=>{}};
        Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});
    ''')
    return browser, ctx

def do_login(use_real_chrome=True):
    print("\n=== FIRST TIME LOGIN ===")
    print("Browser will open. Log into your throwaway Vinted account.")
    print("Press ENTER here once fully logged in and you can see the homepage.\n")
    with sync_playwright() as p:
        browser, ctx = launch_browser(p, headless=False, use_real_chrome=use_real_chrome, offscreen=False)
        page = ctx.new_page()
        page.goto('https://www.vinted.co.uk', timeout=30000)
        page.wait_for_timeout(2000)
        if page.query_selector('[data-testid="header--login-button"]'):
            page.goto('https://www.vinted.co.uk/login', timeout=30000)
        input(">>> Press ENTER after you're logged in... ")
        save_session(ctx)
        browser.close()
    print("Session saved. Future scrapes are fully automatic.\n")

def check_session(page) -> bool:
    try:
        page.goto('https://www.vinted.co.uk', timeout=25000, wait_until='domcontentloaded')
        page.wait_for_timeout(random.randint(2000, 3500))
        title = page.title().lower()
        if any(x in title for x in [
            '403', 'forbidden', 'just a moment', 'error',
            'cloudflare', 'attention required', 'checking your browser',
            'enable javascript', 'access denied',
        ]): return False
        return page.query_selector('[data-testid="header--login-button"]') is None
    except:
        return False

def dismiss_cookies(page):
    for sel in ['[data-testid="cookie-accept-all"]', '#onetrust-accept-btn-handler',
                'button[id*="accept"]', 'button[class*="cookie"][class*="accept"]']:
        try: page.click(sel, timeout=1500); delay(0.3, 0.7); return
        except: pass

def extract_photos(item: dict) -> list:
    photos = item.get('photos', [])
    urls = []
    for p in photos[:6]:
        url = (p.get('full_size_url') or
               p.get('url') or
               (p.get('high_resolution') or {}).get('url') or
               p.get('dominant_color_opaque') or '')
        if url and url.startswith('http') and url not in urls:
            urls.append(url)
    if not urls:
        thumb = item.get('photo') or {}
        url = (thumb.get('full_size_url') or thumb.get('url') or '')
        if url: urls.append(url)
    return urls

def scrape_query(page, query: str, use_gender: bool) -> list:
    captured = []
    seen_ids = set()
    session_problem = [False]

    def handle_response(response):
        url = response.url
        if '/api/v2/catalog/items' not in url: return
        if response.status in (401, 403):
            session_problem[0] = True; return
        if response.status != 200: return
        try:
            data = response.json()
            for item in data.get('items', []):
                iid = str(item.get('id', ''))
                if not iid or iid in seen_ids: continue
                seen_ids.add(iid)
                photos = extract_photos(item)
                try:
                    price_val = item.get('price_numeric', item.get('price', ''))
                    price_str = f"£{float(price_val):.2f}" if price_val else ''
                except (ValueError, TypeError):
                    price_str = str(item.get('price', ''))
                captured.append({
                    'id':         iid,
                    'url':        item.get('url', f'https://www.vinted.co.uk/items/{iid}'),
                    'title':      item.get('title', ''),
                    'price':      price_str,
                    'size':       item.get('size_title', ''),
                    'brand':      item.get('brand_title', ''),
                    'image_url':  photos[0] if photos else '',
                    'all_photos': json.dumps(photos),
                })
        except Exception as e:
            print(f"      JSON error: {e}")

    # listener must be attached BEFORE goto or you miss the first response
    page.on('response', handle_response)

    try:
        url_parts = [f'search_text={quote(query)}', 'order=newest_first']
        if use_gender: url_parts.append('gender_ids[]=1')
        url = f"https://www.vinted.co.uk/catalog?{'&'.join(url_parts)}"
        print(f"    [{query}]{' (no gender filter)' if not use_gender else ''}")

        page.goto(url, timeout=35000, wait_until='domcontentloaded')
        dismiss_cookies(page)

        deadline = time.time() + 10
        while time.time() < deadline:
            if session_problem[0]: raise SessionExpiredError("Session expired")
            if len(captured) >= 20: break
            time.sleep(0.4)

        page1_count = len(captured)
        print(f"      Page 1: {page1_count}")
        if session_problem[0]: raise SessionExpiredError("Session expired")

        if page1_count > 0:
            page.evaluate('window.scrollTo(0, document.body.scrollHeight * 0.7)')
            deadline2 = time.time() + 6
            prev = len(captured)
            while time.time() < deadline2:
                time.sleep(0.4)
                if len(captured) > prev + 5: break

            page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            time.sleep(3)

        print(f"      Total: {len(captured)}")

    except SessionExpiredError: raise
    except PWTimeout: print(f"      Timeout on '{query}'")
    except Exception as e: print(f"      Error: {e}")
    finally:
        page.remove_listener('response', handle_response)

    return captured

def download_image(url: str, listing_id: str):
    import requests as req
    try:
        fname = f"{listing_id}.jpg"
        fpath = os.path.join(IMAGE_DIR, fname)
        if os.path.exists(fpath): return fname
        r = req.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200:
            tmp = fpath + '.tmp'
            with open(tmp, 'wb') as f: f.write(r.content)
            os.rename(tmp, fpath)
            return fname
    except: pass
    return None

def run_scrape(queries: list) -> list:
    if not session_exists():
        write_status({'scraper_state': 'no_session'}); return []

    write_status({'scraper_state': 'running'})

    from db import get_setting
    use_real_chrome = get_setting('use_real_chrome', 'true').lower() == 'true'
    run_headless    = get_setting('run_headless', 'false').lower() == 'true'

    all_listings = []; seen_ids = set()

    with sync_playwright() as p:
        browser, ctx = launch_browser(p, headless=run_headless,
                                      use_real_chrome=use_real_chrome,
                                      offscreen=True)
        page = ctx.new_page()

        if not check_session(page):
            print("Session expired or blocked. Re-run login.")
            write_status({'scraper_state': 'session_expired'})
            browser.close(); return []

        print(f"  Session OK. Running {len(queries)} queries...")

        shuffled = list(queries)
        random.shuffle(shuffled)

        for i, (query, qtype, use_gender) in enumerate(shuffled):
            try:
                results = scrape_query(page, query, use_gender)
                for item in results:
                    if item['id'] not in seen_ids:
                        seen_ids.add(item['id']); all_listings.append(item)
            except SessionExpiredError:
                write_status({'scraper_state': 'session_expired'}); break
            if i < len(shuffled) - 1:
                delay(4, 9)

        save_session(ctx)
        browser.close()

    write_status({'scraper_state': 'idle', 'last_count': len(all_listings)})
    print(f"  Total unique: {len(all_listings)}")
    return all_listings

if __name__ == '__main__':
    import sys
    if '--login' in sys.argv: do_login()
    else:
        from db import get_enabled_queries
        print(run_scrape(get_enabled_queries()))
