#!/usr/bin/env python3
"""ASO Tracker: kaytba3 rank dyal app f App Store kol nhar.

Kaykhdem b Python bo7do (bla libraries).
    python tracker.py                 server + dashboard f http://localhost:8080
    python tracker.py --run           check wa7d (GitHub Actions) o kaykteb data f docs/data
    python tracker.py --run --no-discover
"""
import argparse
import datetime
import gzip
import html
import json
import os
import re
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "aso.db")
CONFIG_PATH = os.path.join(BASE, "config.json")
DASHBOARD_PATH = os.path.join(BASE, "docs", "index.html")
STATIC_DIR = os.path.join(BASE, "docs", "data")
UA = "ASO-Tracker/1.0 (personal rank tracker)"
REQUEST_DELAY = 1.5  # tsenna bin kol request o lakhor bach ma nt9elch 3la Apple
RETEST_DAYS = 7      # keyword li ma ranké fih ghayt3awed ytesta mn ba3d had l iyam

DEFAULT_CONFIG = {
    "app_id": "6447526912",
    "countries": ["us", "gb", "ca", "au", "fr", "de", "ma", "sa"],
    "port": 8080,
    "daily_hour": 9,
    "candidates_per_run": 25,
    "my_keywords": "",
    # keywords li kantba3o: "term" = f kol l bldan, "term|us" = f bled wa7d
    "manual_keywords": [],
    # keywords li 7ayedti mn dashboard: "term|us"
    "removed_keywords": [],
}

STOPWORDS = set("""
the a an and or of for to in on with your you my by at is are be this that from it its as all
new best free game games app apps play fun top lite pro hd edition mobile online offline official
2022 2023 2024 2025 2026 2027 vs our get can will just more most like into out up win now
de la le les des et du en un une el los las y con para por del da do dos das em um uma
der die das und mit für ein eine il lo gli di per con
""".split())

# Marques dyal nas khrin: Apple katrefd app ila 7tetihom f smiya wla keywords
TRADEMARKS = set("""
nba wnba nfl nhl mlb ncaa fifa uefa espn ea 2k 2k24 2k25 2k26 2k27 roblox minecraft fortnite pokemon
disney marvel nike adidas jordan lebron curry kobe messi ronaldo tiktok youtube instagram
""".split())


# ---------------------------------------------------------------- config / db

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["countries"] = [c.strip().lower() for c in cfg["countries"] if c.strip()]
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY,
            term TEXT NOT NULL,
            country TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            added_at TEXT NOT NULL,
            UNIQUE(term, country)
        );
        CREATE TABLE IF NOT EXISTS ranks (
            keyword_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            rank INTEGER,
            depth INTEGER,
            method TEXT,
            PRIMARY KEY (keyword_id, date)
        );
        CREATE TABLE IF NOT EXISTS serp (
            keyword_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            pos INTEGER NOT NULL,
            app_id TEXT NOT NULL,
            name TEXT,
            ratings INTEGER,
            PRIMARY KEY (keyword_id, date, pos)
        );
        CREATE TABLE IF NOT EXISTS candidates (
            term TEXT NOT NULL,
            country TEXT NOT NULL,
            origin TEXT,
            tested_at TEXT,
            rank INTEGER,
            PRIMARY KEY (term, country)
        );
        CREATE TABLE IF NOT EXISTS charts (
            date TEXT NOT NULL,
            country TEXT NOT NULL,
            genre TEXT NOT NULL,
            rank INTEGER,
            PRIMARY KEY (date, country, genre)
        );
        CREATE TABLE IF NOT EXISTS app_info (
            date TEXT NOT NULL,
            country TEXT NOT NULL,
            data TEXT NOT NULL,
            PRIMARY KEY (date, country)
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)


def meta_get(key, default=None):
    with db() as con:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(key, value):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))


def today():
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- apple

_last_request = 0.0
_request_lock = threading.Lock()


def fetch(url):
    global _last_request
    for attempt in range(4):
        with _request_lock:
            wait = REQUEST_DELAY - (time.time() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
            return data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # 429 = Apple katgoul "bzzaf d requests": ntsennaw o n3awdo
            if e.code not in (429, 503) or attempt == 3:
                raise
            time.sleep(15 * (attempt + 1))


def lookup(ids, country):
    out = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        d = json.loads(fetch(f"https://itunes.apple.com/lookup?country={country}&id={','.join(chunk)}"))
        for r in d.get("results", []):
            out[str(r["trackId"])] = r
    return out


def search_page(term, country):
    """Tartib l7a9i9i dyal App Store (ghir awel ~10-20 app)."""
    h = fetch(f"https://apps.apple.com/{country}/iphone/search?term={urllib.parse.quote(term)}")
    ids = []
    for m in re.finditer(r'/app/[^"/]+/id(\d+)', h):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    subtitles = [html.unescape(json.loads('"' + s + '"')) for s in re.findall(r'"subtitle":"((?:[^"\\]|\\.)*)"', h)]
    return ids, subtitles


_api_blocked = False


def search_api(term, country):
    """Search API (7ta 200 app). Apple kat bloki-ha mn ba3d serveurs d cloud, o katkhdem mn PC d dar."""
    global _api_blocked
    if _api_blocked:
        return None
    try:
        d = json.loads(fetch("https://itunes.apple.com/search?" + urllib.parse.urlencode(
            {"term": term, "country": country, "entity": "software", "limit": 200})))
        return [str(r["trackId"]) for r in d.get("results", [])]
    except urllib.error.HTTPError as e:
        if e.code == 403:
            _api_blocked = True
        return None


def check_rank(term, country, app_id):
    """Kayrj3 (rank, depth, method, top_ids, subtitles)."""
    ids, subtitles = search_page(term, country)
    if app_id in ids:
        return ids.index(app_id) + 1, len(ids), "store", ids, subtitles
    api = search_api(term, country)
    if api is None:
        return None, len(ids), "store", ids, subtitles
    if app_id in api:
        # l page hiya l mrja3 f lwlin; ila API galt rank sghir walakin page ma l9atoch, nkhliwh mor l page
        return max(api.index(app_id) + 1, len(ids) + 1), len(api), "api", ids, subtitles
    return None, max(len(api), len(ids)), "api", ids, subtitles


def chart(country, genre):
    d = json.loads(fetch(f"https://itunes.apple.com/{country}/rss/topfreeapplications/limit=100/genre={genre}/json"))
    entries = d.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    return [e["id"]["attributes"]["im:id"] for e in entries]


def app_subtitle(app_id, country):
    try:
        h = fetch(f"https://apps.apple.com/{country}/app/id{app_id}")
    except Exception:
        return ""
    m = re.search(r'</h1>\s*<(?:p|h2) class="subtitle[^"]*"[^>]*>([^<]*)', h)
    return html.unescape(m.group(1)).strip() if m else ""


GENRE_NAMES = {
    "6014": "All Games", "7001": "Action", "7002": "Adventure", "7003": "Casual", "7004": "Board",
    "7005": "Card", "7006": "Casino", "7009": "Family", "7011": "Music", "7012": "Puzzle",
    "7013": "Racing", "7014": "Role Playing", "7015": "Simulation", "7016": "Sports",
    "7017": "Strategy", "7018": "Trivia", "7019": "Word",
}


# ---------------------------------------------------------------- keywords

def tokens(text, split_dots=True):
    out = []
    for w in re.findall(r"[^\W_][\w.'+-]*", text.lower()):
        w = w.strip(".'-+")
        # "basketball.io" kat3ti "basketball.io" o 7ta "basketball"
        for part in [w] + (w.split(".") if "." in w and split_dots else []):
            if not part or part in STOPWORDS:
                continue
            if len(part) >= 3 or (len(part) == 2 and any(ch.isdigit() for ch in part)):
                out.append(part)
    return out


def phrases(text, n):
    t = tokens(text, split_dots=False)
    return [" ".join(t[i:i + n]) for i in range(len(t) - n + 1) if not any("." in w for w in t[i:i + n])]


def own_text(country):
    with db() as con:
        row = con.execute("SELECT data FROM app_info WHERE country=? ORDER BY date DESC LIMIT 1", (country,)).fetchone()
    if not row:
        return "", ""
    info = json.loads(row["data"])
    return info.get("name", "") + " " + info.get("subtitle", ""), info.get("description", "")


def market_titles(country, days=14):
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    with db() as con:
        rows = con.execute("""SELECT DISTINCT s.app_id, s.name FROM serp s JOIN keywords k ON k.id=s.keyword_id
                              WHERE k.country=? AND s.date>=?""", (country, since)).fetchall()
    return [r["name"] or "" for r in rows]


def market_subtitles(country):
    raw = meta_get("subtitles_" + country, "[]")
    return json.loads(raw)


def generate_candidates(country, cfg):
    title_text, description = own_text(country)
    my_kw = [k.strip().lower() for k in re.split(r"[,\n]", cfg.get("my_keywords", "")) if k.strip()]
    titles = market_titles(country) + market_subtitles(country)

    market_uni = Counter(w for t in titles for w in set(tokens(t)))
    market_bi = Counter(p for t in titles for p in set(phrases(t, 2)))
    own_words = set(tokens(title_text)) | {w for k in my_kw for w in tokens(k)}
    desc_words = Counter(tokens(description))

    scores = Counter()
    origins = {}

    def add(term, score, origin):
        term = " ".join(term.split())
        if len(term) < 3:
            return
        if score > scores.get(term, 0):
            origins[term] = origin
        scores[term] = max(scores[term], score)

    for k in my_kw:
        add(k, 100, "keywords dyalk")
    for w in own_words:
        add(w, 60 + market_uni[w] * 5, "smiya/subtitle dyalk")
    for p in phrases(title_text, 2):
        add(p, 55 + market_bi[p] * 5, "smiya/subtitle dyalk")
    for w, c in market_uni.most_common(60):
        if c >= 2:
            add(w, 20 + c * 5 + (10 if w in own_words else 0), f"concurrents ({c}x)")
    for p, c in market_bi.most_common(40):
        if c >= 2:
            add(p, 25 + c * 6, f"concurrents ({c}x)")
    for w, c in desc_words.most_common(25):
        if c >= 2:
            add(w, 15 + c * 2, "description dyalk")
    core = sorted((w for w in own_words if "." not in w), key=lambda w: -market_uni[w])[:6]
    for a in core:
        for b, c in market_uni.most_common(15):
            if c < 2 or "." in b or a in b or b in a:
                continue
            # kalma li kayna kter 3nd concurrents (b7al "basketball") kat ji lekher: "street basketball"
            first, last = (a, b) if market_uni[a] <= market_uni[b] else (b, a)
            add(f"{first} {last}", 18 + min(c, 10) * 2, "combinaison")

    with db() as con:
        tracked = {r["term"] for r in con.execute("SELECT term FROM keywords WHERE country=?", (country,))}
        tracked |= {r.partition("|")[0] for r in cfg.get("removed_keywords", []) if r.endswith("|" + country)}
        cutoff = (datetime.date.today() - datetime.timedelta(days=RETEST_DAYS)).isoformat()
        recent = {r["term"] for r in con.execute(
            "SELECT term FROM candidates WHERE country=? AND tested_at>=?", (country, cutoff))}
    ranked = [t for t, _ in scores.most_common()
              if t not in tracked and t not in recent and not TRADEMARKS & set(t.split())]
    return [(t, origins[t]) for t in ranked]


# ---------------------------------------------------------------- job

STATUS = {"running": False, "step": "", "done": 0, "total": 0, "log": [], "error": ""}
_job_lock = threading.Lock()


def log(msg):
    STATUS["log"] = (STATUS["log"] + [time.strftime("%H:%M:%S ") + msg])[-200:]
    print(msg, flush=True)


def save_serp(con, keyword_id, date, top_ids, names):
    con.execute("DELETE FROM serp WHERE keyword_id=? AND date=?", (keyword_id, date))
    for pos, aid in enumerate(top_ids[:10], 1):
        r = names.get(aid, {})
        con.execute("INSERT INTO serp VALUES (?,?,?,?,?,?)",
                    (keyword_id, date, pos, aid, r.get("trackName"), r.get("userRatingCount")))


def run_job(discover=True, only_country=None):
    if not _job_lock.acquire(blocking=False):
        return False
    try:
        STATUS.update(running=True, error="", done=0, total=0, log=[])
        cfg = load_config()
        app_id = str(cfg["app_id"])
        countries = [only_country] if only_country else cfg["countries"]
        d = today()

        with db() as con:
            kws = con.execute("SELECT * FROM keywords WHERE country IN (%s)" % ",".join("?" * len(countries)),
                              countries).fetchall()
        n_cand = cfg["candidates_per_run"] if discover else 0
        STATUS["total"] = len(countries) * (2 + 3 + n_cand) + len(kws)

        # 1. ma3lomat 3la app
        genres = ["6014"]
        for cc in countries:
            STATUS["step"] = f"Ma3lomat dyal app ({cc.upper()})"
            try:
                r = lookup([app_id], cc).get(app_id)
                if r:
                    info = {
                        "name": r.get("trackName"), "subtitle": app_subtitle(app_id, cc),
                        "seller": r.get("sellerName"), "icon": r.get("artworkUrl100"),
                        "url": r.get("trackViewUrl"), "rating": r.get("averageUserRating"),
                        "ratings": r.get("userRatingCount"), "version": r.get("version"),
                        "updated": r.get("currentVersionReleaseDate"), "released": r.get("releaseDate"),
                        "genres": r.get("genres"), "genre_ids": r.get("genreIds"),
                        "size_mb": round(int(r.get("fileSizeBytes", 0)) / 1e6, 1),
                        "languages": r.get("languageCodesISO2A"), "min_ios": r.get("minimumOsVersion"),
                        "age": r.get("contentAdvisoryRating"), "price": r.get("formattedPrice"),
                        "description": r.get("description", ""), "notes": r.get("releaseNotes", ""),
                    }
                    for g in r.get("genreIds", []):
                        if g.startswith("70") and g not in genres:
                            genres.append(g)
                    with db() as con:
                        con.execute("INSERT OR REPLACE INTO app_info VALUES (?,?,?)", (d, cc, json.dumps(info)))
                else:
                    log(f"{cc.upper()}: app ma kaynach f store dyal had l bled")
            except Exception as e:
                log(f"{cc.upper()}: error f lookup: {e}")
            STATUS["done"] += 2

        # 2. top charts
        for cc in countries:
            STATUS["step"] = f"Top charts ({cc.upper()})"
            for g in genres[:3]:
                try:
                    ids = chart(cc, g)
                    rank = ids.index(app_id) + 1 if app_id in ids else None
                    with db() as con:
                        con.execute("INSERT OR REPLACE INTO charts VALUES (?,?,?,?)", (d, cc, g, rank))
                except Exception as e:
                    log(f"{cc.upper()} chart {g}: {e}")
            STATUS["done"] += 3

        # 3. keywords li kantba3o
        for k in kws:
            STATUS["step"] = f"Keyword '{k['term']}' ({k['country'].upper()})"
            try:
                rank, depth, method, top_ids, subs = check_rank(k["term"], k["country"], app_id)
                names = lookup(top_ids[:10], k["country"]) if top_ids else {}
                with db() as con:
                    con.execute("INSERT OR REPLACE INTO ranks VALUES (?,?,?,?,?)", (k["id"], d, rank, depth, method))
                    save_serp(con, k["id"], d, top_ids, names)
                old = json.loads(meta_get("subtitles_" + k["country"], "[]"))
                meta_set("subtitles_" + k["country"], json.dumps(list(dict.fromkeys(subs + old))[:300]))
            except Exception as e:
                log(f"'{k['term']}' {k['country']}: {e}")
            STATUS["done"] += 1

        # 4. discovery: keywords jdad
        found = 0
        for cc in countries:
            if not n_cand:
                break
            cands = generate_candidates(cc, cfg)[:n_cand]
            for term, origin in cands:
                STATUS["step"] = f"Discovery: '{term}' ({cc.upper()})"
                try:
                    rank, depth, method, top_ids, subs = check_rank(term, cc, app_id)
                    with db() as con:
                        con.execute("INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?)", (term, cc, origin, d, rank))
                    if rank:
                        names = lookup(top_ids[:10], cc) if top_ids else {}
                        with db() as con:
                            con.execute("INSERT OR IGNORE INTO keywords (term, country, source, added_at) VALUES (?,?,?,?)",
                                        (term, cc, "auto", d))
                            kid = con.execute("SELECT id FROM keywords WHERE term=? AND country=?", (term, cc)).fetchone()["id"]
                            con.execute("INSERT OR REPLACE INTO ranks VALUES (?,?,?,?,?)", (kid, d, rank, depth, method))
                            save_serp(con, kid, d, top_ids, names)
                        found += 1
                        log(f"✅ Keyword jdid: '{term}' ({cc.upper()}) rank #{rank}")
                except Exception as e:
                    log(f"discovery '{term}' {cc}: {e}")
                STATUS["done"] += 1
            STATUS["done"] += n_cand - len(cands)

        if not only_country:
            meta_set("last_run", d)
        meta_set("last_run_at", datetime.datetime.now().isoformat(timespec="minutes"))
        meta_set("api_mode", "blocked" if _api_blocked else "ok")
        log(f"Salina. {len(kws)} keyword t3awed tchekaw, {found} keyword jdid tl9aw.")
        STATUS["step"] = "Salina"
    except Exception as e:
        STATUS["error"] = str(e)
        traceback.print_exc()
    finally:
        STATUS["running"] = False
        _job_lock.release()
    return True


def scheduler():
    while True:
        try:
            cfg = load_config()
            if meta_get("last_run") != today() and datetime.datetime.now().hour >= int(cfg["daily_hour"]):
                run_job(discover=True)
        except Exception:
            traceback.print_exc()
        time.sleep(60)


# ---------------------------------------------------------------- api

def api_state():
    cfg = load_config()
    with db() as con:
        info = {r["country"]: json.loads(r["data"]) for r in con.execute(
            "SELECT country, data FROM app_info a WHERE date=(SELECT MAX(date) FROM app_info b WHERE b.country=a.country)")}
        charts = [dict(r) for r in con.execute("SELECT * FROM charts ORDER BY date")]
    return {
        "config": cfg, "info": info, "charts": charts, "genre_names": GENRE_NAMES,
        "status": STATUS, "last_run_at": meta_get("last_run_at"), "api_mode": meta_get("api_mode"),
    }


def api_keywords():
    with db() as con:
        kws = [dict(r) for r in con.execute("SELECT * FROM keywords ORDER BY country, term")]
        ranks = {}
        for r in con.execute("SELECT * FROM ranks ORDER BY date"):
            ranks.setdefault(r["keyword_id"], []).append({"date": r["date"], "rank": r["rank"], "depth": r["depth"], "method": r["method"]})
    for k in kws:
        h = ranks.get(k["id"], [])
        k["history"] = h[-60:]
        k["rank"] = h[-1]["rank"] if h else None
        k["depth"] = h[-1]["depth"] if h else None
        k["checked"] = h[-1]["date"] if h else None
        k["prev"] = h[-2]["rank"] if len(h) > 1 else None
        best = [x["rank"] for x in h if x["rank"]]
        k["best"] = min(best) if best else None
    return kws


def api_serp(keyword_id):
    with db() as con:
        row = con.execute("SELECT MAX(date) d FROM serp WHERE keyword_id=?", (keyword_id,)).fetchone()
        rows = con.execute("SELECT pos, app_id, name, ratings FROM serp WHERE keyword_id=? AND date=? ORDER BY pos",
                           (keyword_id, row["d"])).fetchall()
    return {"date": row["d"], "apps": [dict(r) for r in rows]}


def api_candidates():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM candidates ORDER BY tested_at DESC, rank IS NULL, rank LIMIT 500")]


def api_analysis(country):
    cfg = load_config()
    title_text, description = own_text(country)
    my_kw = " ".join(re.split(r"[,\n]", cfg.get("my_keywords", "")))
    titles = market_titles(country) + market_subtitles(country)
    freq = Counter(w for t in titles for w in set(tokens(t)))
    name_words, kw_words, desc_words = set(tokens(title_text)), set(tokens(my_kw)), set(tokens(description))
    with db() as con:
        best = {}
        for r in con.execute("""SELECT k.term, MIN(r.rank) rk FROM keywords k JOIN ranks r ON r.keyword_id=k.id
                                WHERE k.country=? AND r.date=(SELECT MAX(date) FROM ranks WHERE keyword_id=k.id)
                                GROUP BY k.term""", (country,)):
            best[r["term"]] = r["rk"]
    rows = []
    for w, c in freq.most_common(40):
        rows.append({
            "word": w, "count": c, "in_title": w in name_words, "in_keywords": w in kw_words,
            "in_description": w in desc_words, "rank": best.get(w), "trademark": w in TRADEMARKS,
        })
    return {"words": rows, "sample": len(titles)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            with open(DASHBOARD_PATH, "rb") as f:
                return self.send(f.read(), ctype="text/html")
        if u.path == "/api/state":
            return self.send(api_state())
        if u.path == "/api/keywords":
            return self.send(api_keywords())
        if u.path == "/api/serp":
            return self.send(api_serp(int(q["id"][0])))
        if u.path == "/api/candidates":
            return self.send(api_candidates())
        if u.path == "/api/analysis":
            return self.send(api_analysis(q.get("country", ["us"])[0]))
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        b = self.body()
        if u.path == "/api/keywords":
            cfg = load_config()
            terms = [t.strip().lower() for t in re.split(r"[,\n]", b.get("terms", "")) if t.strip()]
            countries = b.get("countries") or cfg["countries"]
            config_add_keywords(cfg, terms, countries)
            save_config(cfg)
            sync_config_keywords(cfg)
            return self.send({"added": len(terms) * len(countries)})
        if u.path == "/api/keywords/delete":
            cfg = load_config()
            with db() as con:
                rows = [con.execute("SELECT term, country FROM keywords WHERE id=?", (kid,)).fetchone() for kid in b.get("ids", [])]
            for r in filter(None, rows):
                config_remove_keyword(cfg, r["term"], r["country"])
            save_config(cfg)
            sync_config_keywords(cfg)
            return self.send({"ok": True})
        if u.path == "/api/run":
            if STATUS["running"]:
                return self.send({"ok": False, "msg": "deja khdam"})
            threading.Thread(target=run_job, kwargs={"discover": b.get("discover", True)}, daemon=True).start()
            return self.send({"ok": True})
        if u.path == "/api/config":
            cfg = load_config()
            for k in ("app_id", "countries", "daily_hour", "candidates_per_run", "my_keywords"):
                if k in b:
                    cfg[k] = b[k]
            if isinstance(cfg["countries"], str):
                cfg["countries"] = [c.strip().lower() for c in cfg["countries"].split(",") if c.strip()]
            cfg["daily_hour"] = int(cfg["daily_hour"])
            cfg["candidates_per_run"] = int(cfg["candidates_per_run"])
            save_config(cfg)
            return self.send({"ok": True, "config": cfg})
        self.send({"error": "not found"}, 404)


def config_add_keywords(cfg, terms, countries):
    """Nefs l logique li f dashboard (GitHub mode): kaybdel config bo7do."""
    every = set(countries) >= set(cfg["countries"])
    entries = [t for t in terms] if every else [f"{t}|{cc}" for t in terms for cc in countries]
    cfg["manual_keywords"] = list(dict.fromkeys(cfg.get("manual_keywords", []) + entries))
    added = {f"{t}|{cc}" for t in terms for cc in countries}
    cfg["removed_keywords"] = [r for r in cfg.get("removed_keywords", []) if r not in added]


def config_remove_keyword(cfg, term, country):
    cfg["manual_keywords"] = [m for m in cfg.get("manual_keywords", []) if m != f"{term}|{country}"]
    cfg["removed_keywords"] = list(dict.fromkeys(cfg.get("removed_keywords", []) + [f"{term}|{country}"]))


def sync_config_keywords(cfg):
    """Kayzid manual_keywords mn config l database o kay7ayed removed_keywords."""
    removed = {r.lower() for r in cfg.get("removed_keywords", [])}
    with db() as con:
        for entry in cfg.get("manual_keywords", []):
            term, _, only = entry.lower().partition("|")
            for cc in ([only] if only else cfg["countries"]):
                if f"{term.strip()}|{cc}" not in removed:
                    con.execute("INSERT OR IGNORE INTO keywords (term, country, source, added_at) VALUES (?,?,?,?)",
                                (term.strip(), cc, "manual", today()))
        for r in removed:
            term, _, cc = r.partition("|")
            for row in con.execute("SELECT id FROM keywords WHERE term=? AND country=?", (term, cc)).fetchall():
                con.execute("DELETE FROM keywords WHERE id=?", (row["id"],))
                con.execute("DELETE FROM ranks WHERE keyword_id=?", (row["id"],))
                con.execute("DELETE FROM serp WHERE keyword_id=?", (row["id"],))


def export_static():
    """Kaykteb nefs data dyal /api/* f fichiers JSON l GitHub Pages."""
    os.makedirs(STATIC_DIR, exist_ok=True)
    state = api_state()
    state["status"] = {"running": False, "step": "", "done": 0, "total": 0, "log": STATUS["log"][-50:], "error": STATUS["error"]}
    keywords = api_keywords()
    files = {
        "state.json": state,
        "keywords.json": keywords,
        "candidates.json": api_candidates(),
        "serp.json": {str(k["id"]): api_serp(k["id"]) for k in keywords},
        "analysis.json": {cc: api_analysis(cc) for cc in state["config"]["countries"]},
    }
    for name, obj in files.items():
        with open(os.path.join(STATIC_DIR, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="dir check wa7d o khrej (l GitHub Actions)")
    ap.add_argument("--no-discover", action="store_true", help="bla discovery dyal keywords jdad")
    args = ap.parse_args()
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
    init_db()
    cfg = load_config()
    sync_config_keywords(cfg)
    if args.run:
        run_job(discover=not args.no_discover)
        export_static()
        if STATUS["error"]:
            raise SystemExit("Error: " + STATUS["error"])
        return
    threading.Thread(target=scheduler, daemon=True).start()
    port = int(os.environ.get("PORT", cfg["port"]))
    print(f"ASO Tracker khdam: 7ell http://localhost:{port} f browser dyalk", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
