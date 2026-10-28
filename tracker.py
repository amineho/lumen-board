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
import math
import os
import re
import sqlite3
import subprocess
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
REQUEST_DELAY = 1.2  # tsenna bin kol request o lakhor bach ma nt9elch 3la Apple
MAX_DELAY = 4.0      # ila Apple galt 429, delay kaytzad 7ta had l7ed
RETEST_DAYS = 7      # keyword li ma ranké fih ghayt3awed ytesta mn ba3d had l iyam
HARD_LIMIT_MIN = 75  # 7ed a9sa l run wa7d (GitHub kay7bes job f 90 d9i9a)
CHECKPOINT_MIN = 10  # sauvegarde f nos run (ila t7bes run, li jay kaykmmel mn hna)


def checkpoint():
    """Kaysjjel data f nos run: export + script li f ASO_CHECKPOINT (f GitHub: push l branch data)."""
    script = os.environ.get("ASO_CHECKPOINT")
    if not script:
        return
    try:
        export_static()
        subprocess.run(["bash", script], check=True, timeout=300)
        log("Checkpoint saved.")
    except Exception as e:
        log(f"Checkpoint failed: {e}")


_last_checkpoint = 0.0


def maybe_checkpoint():
    global _last_checkpoint
    if time.time() - _last_checkpoint > CHECKPOINT_MIN * 60:
        checkpoint()
        _last_checkpoint = time.time()

DEFAULT_CONFIG = {
    "app_id": "6447526912",
    "countries": ["us", "gb", "ca", "au", "fr", "de", "ma", "sa"],
    "port": 8080,
    "daily_hour": 9,
    "candidates_per_run": 60,
    # ch7al mn keyword mn pool (keywords dyal apps li f top 10 m3ak) ytesta f kol bled f kol run
    "pool_per_run": 0,
    # bldan li kaykmlo discovery dyalhom 9bel lakhrin (b tartib)
    "priority_countries": ["us"],
    # ch7al mn recherche f autocomplete dyal App Store f kol bled f kol run (bled prioritaire: x2)
    "suggest_per_run": 60,
    # sa3a (UTC) dyal check kamel f GitHub: 3 = 4 d sba7 f lmaghrib
    "daily_hour_utc": 3,
    # ch7al mn d9i9a y9der ydir run kamel (0 = bla 7ad). Discovery katwe99ef mli ykmel l w9t.
    "time_budget_minutes": 0,
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
pour avec sans mais aussi très au aux ce cette ces est sont vous votre vos nous notre ton ta tes ses son sa dans sur par pas plus jeu jeux tout tous
den dem des zu von ist auf sie du dein deine dich mehr spiel spiele spielen auch nicht noch wie
tu tus es su sus juego juegos más muy como pero sin sobre jugar
que una uno unos unas al lo se te me mi cada todo todos este esta estos hay ser puedes tienes
qui quoi dont où leurs leur chaque deux nos vos ton toute toutes peut faire être avoir
nicht kein keine oder aber sein ihre ihr wir uns einen einem einer jeden jede
not none
seu sua seus suas jogo jogos mais como sem jogar você
gli le del della dei gioco giochi più tuo tua
ve ile bir bu için oyun oyunu oyunlar daha çok sen
في من على إلى الى عن مع هذا هذه ذلك تلك التي الذي الذين أن إن ان كان كانت كل لا ما هو هي أو او ثم قد لقد عند بين
حتى بعد قبل كما أيضا ايضا أكثر لك لكم يمكنك يمكن لعبة ألعاب العاب اللعبة الآن جدا مثل كيف أي اي نحن أنت انت
""".split())

# lughat dyal kol bled (mzyada 3la lugha par défaut dyal store). Apple kat3ti metadata b had lughat b ?lang=
COUNTRY_LANGS = {
    "ma": ["fr_fr", "ar"], "dz": ["fr_fr", "ar"], "tn": ["fr_fr", "ar"], "sa": ["ar"], "ae": ["ar"], "eg": ["ar"],
    "qa": ["ar"], "kw": ["ar"], "bh": ["ar"], "om": ["ar"], "jo": ["ar"], "lb": ["ar", "fr_fr"], "iq": ["ar"],
    "ca": ["fr_ca"], "be": ["fr_fr"], "ch": ["de_de", "fr_fr"], "us": ["es_mx"], "il": ["he"], "in": ["hi"],
}
# Lughat li kay9elbo bihom nas f kol bled (machi ghir lugha dyal store): MA b l3arabiya, lfransawiya o l'anglais...
COUNTRY_SEARCH_LANGS = {
    "us": ["en", "es"], "gb": ["en"], "ca": ["en", "fr"], "au": ["en"], "nz": ["en"], "ie": ["en"], "in": ["en"],
    "fr": ["fr", "en"], "be": ["fr", "nl", "en"], "ch": ["de", "fr", "it", "en"], "lu": ["fr", "de", "en"],
    "de": ["de", "en"], "at": ["de", "en"], "nl": ["nl", "en"], "pl": ["pl", "en"],
    "es": ["es", "en"], "mx": ["es", "en"], "ar": ["es", "en"], "co": ["es", "en"], "cl": ["es", "en"], "pe": ["es", "en"],
    "br": ["pt", "en"], "pt": ["pt", "en"], "it": ["it", "en"], "tr": ["tr", "en"], "ru": ["ru", "en"],
    "jp": ["ja", "en"], "kr": ["ko", "en"], "cn": ["zh"], "tw": ["zh", "en"], "hk": ["zh", "en"],
    "id": ["id", "en"], "my": ["en", "id"], "ph": ["en"], "sg": ["en", "zh"],
    "ma": ["ar", "fr", "en"], "dz": ["ar", "fr", "en"], "tn": ["ar", "fr", "en"], "lb": ["ar", "fr", "en"],
    "sa": ["ar", "en"], "ae": ["ar", "en"], "eg": ["ar", "en"], "qa": ["ar", "en"], "kw": ["ar", "en"],
    "bh": ["ar", "en"], "om": ["ar", "en"], "jo": ["ar", "en"], "iq": ["ar", "en"],
}
SEEDS_PATH = os.path.join(BASE, "seeds.json")


def load_seeds():
    """seeds.json: keywords momkinin b kol lugha (katbdelhom b yddek)."""
    try:
        with open(SEEDS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")

# Marques dyal nas khrin: Apple katrefd app ila 7tetihom f smiya wla keywords
TRADEMARKS = set("""
nba wnba nfl nhl mlb ncaa fifa uefa espn ea 2k 2k24 2k25 2k26 2k27 roblox minecraft fortnite pokemon
disney marvel nike adidas jordan lebron curry kobe messi ronaldo tiktok youtube instagram
nbpa durant harden doncic dončić luka giannis antetokounmpo embiid jokic tatum wembanyama stephen kevin james
miniclip supercell rovio voodoo
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
        CREATE TABLE IF NOT EXISTS pool_apps (
            country TEXT NOT NULL,
            app_id TEXT NOT NULL,
            name TEXT,
            ratings INTEGER,
            from_term TEXT,
            added_at TEXT,
            refreshed_at TEXT,
            n_terms INTEGER,
            PRIMARY KEY (country, app_id)
        );
        CREATE TABLE IF NOT EXISTS pool_terms (
            country TEXT NOT NULL,
            term TEXT NOT NULL,
            app_id TEXT,
            added_at TEXT,
            n_apps INTEGER DEFAULT 1,
            lang TEXT,
            PRIMARY KEY (country, term)
        );
        CREATE TABLE IF NOT EXISTS suggestions (
            country TEXT NOT NULL,
            term TEXT NOT NULL,
            score INTEGER,
            query TEXT,
            first_seen TEXT,
            last_seen TEXT,
            PRIMARY KEY (country, term)
        );
        CREATE TABLE IF NOT EXISTS suggest_queries (
            country TEXT NOT NULL,
            query TEXT NOT NULL,
            done_at TEXT,
            results INTEGER,
            PRIMARY KEY (country, query)
        );
        CREATE TABLE IF NOT EXISTS competitors (
            date TEXT NOT NULL,
            country TEXT NOT NULL,
            app_id TEXT NOT NULL,
            name TEXT,
            ratings INTEGER,
            appearances INTEGER,
            avg_pos REAL,
            url TEXT,
            terms TEXT,
            PRIMARY KEY (date, country, app_id)
        );
        """)


def migrate_db():
    with db() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(pool_terms)")}
        if "lang" not in cols:
            con.execute("ALTER TABLE pool_terms ADD COLUMN lang TEXT")


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
_delay = REQUEST_DELAY
_request_lock = threading.Lock()


def fetch(url, headers=None):
    global _last_request, _delay
    for attempt in range(4):
        with _request_lock:
            wait = _delay - (time.time() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", **(headers or {})})
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
            _delay = min(MAX_DELAY, _delay + 0.5)
            time.sleep(15 * (attempt + 1))


def lookup(ids, country, lang=None):
    out = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        d = json.loads(fetch(f"https://itunes.apple.com/lookup?country={country}&id={','.join(chunk)}" + (f"&lang={lang}" if lang else "")))
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


def check_rank(term, country, app_id, fast=False):
    """Kayrj3 (rank, depth, method, top_ids, subtitles).
    fast=True (discovery): search API bo7dha (request wa7d, top 200), o page dyal App Store
    ghir ila app f top 25 bach rank ykon b dbt. Ila API mbloka, kayrje3 l tari9a l3adiya."""
    if fast:
        api = search_api(term, country)
        if api is not None:
            if app_id not in api:
                return None, len(api), "api", api[:10], []
            if api.index(app_id) >= 25:
                return api.index(app_id) + 1, len(api), "api", api[:10], []
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
            # kalma 3arabiya ma katbdach b "ة" wla "ى": ila bdat bihom ya3ni ktaba m9louba (RTL ghalta)
            if not part or part in STOPWORDS or part[0] in "ةى":
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
    loc = info.get("localized", {}).values()
    title = " ".join([info.get("name", ""), info.get("subtitle", "")] + [x.get("name", "") for x in loc])
    desc = "\n".join([info.get("description", "")] + [x.get("description", "") for x in loc])
    return title, desc


def market_titles(country, days=14):
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    with db() as con:
        rows = con.execute("""SELECT DISTINCT s.app_id, s.name FROM serp s JOIN keywords k ON k.id=s.keyword_id
                              WHERE k.country=? AND s.date>=?""", (country, since)).fetchall()
    return [r["name"] or "" for r in rows]


def market_subtitles(country):
    raw = meta_get("subtitles_" + country, "[]")
    return json.loads(raw)


COMPETITORS_PER_COUNTRY = 12
# kalimat 3amma bzzaf f descriptions dyal games, ma kayswawch ka keywords
DESC_STOP = set("""
player players level levels time world way make need also each every feature features download experience enjoy
easy simple amazing awesome addictive graphics hours friends start keep right first next take give become much many
better great ever like love only own real really very want will with without game's app's please support email contact
terms privacy policy subscription purchase purchases price account itunes period renewal automatically charged
http https www com net payment payments them they their these those this that what when where which while from into
about more most other such than then there here anytime everyone anyone something available required includes include
upon still feel able through against around forward who down high end cancel service cycle apple template api
licensed membership information continue stay different unique special bigger impressive extended successful
after again always alone ads adjust allow allows across along already another anything around back being both
can't could don't does doing done during enough even ever few found full get gets got going good great had has
have how its just know last least let lets lot made makes making may might must new now off once one ones out over
put same see should since some soon sure tell things think those today too try two under until use used using want
well were what's while who's why within won't would yet you're you'll
""".split())


def competitor_terms(name, description, limit=25):
    """Keywords li kaybano f smiya o description dyal concurrent (keywords dyal App Store Connect makhfiyin)."""
    c = Counter()
    for w in tokens(name, split_dots=False):
        c[w] += 3
    for ph in phrases(name, 2):
        c[ph] += 3
    desc = "" if CJK.search(description or "") else (description or "")[:4000]
    for w, n in Counter(tokens(desc, split_dots=False)).items():
        if n >= 2:
            c[w] += min(n, 6) * 0.5
    for ph, n in Counter(phrases(desc, 2)).items():
        if n >= 2:
            c[ph] += n
    out = []
    for term, _ in c.most_common():
        words = term.split()
        if "." in term or "/" in term or any(w in DESC_STOP or w in TRADEMARKS or w.isdigit() for w in words):
            continue
        out.append(term)
        if len(out) == limit:
            break
    return out


# ---------------------------------------------------------------- suggestions dyal App Store (VIP)
# ID dyal store (storefront) l kol bled: autocomplete dyal App Store kay7taj-h
STOREFRONTS = {
    "us": 143441, "gb": 143444, "ca": 143455, "au": 143460, "nz": 143461, "ie": 143449, "fr": 143442, "de": 143443,
    "it": 143450, "es": 143454, "nl": 143452, "be": 143446, "ch": 143459, "at": 143445, "pt": 143453, "br": 143503,
    "mx": 143468, "ar": 143505, "co": 143501, "cl": 143483, "pe": 143507, "jp": 143462, "kr": 143466, "cn": 143465,
    "tw": 143470, "hk": 143463, "sg": 143464, "my": 143473, "id": 143476, "ph": 143474, "th": 143475, "vn": 143471,
    "in": 143467, "tr": 143480, "ru": 143469, "pl": 143478, "se": 143456, "no": 143457, "dk": 143458, "fi": 143447,
    "sa": 143479, "ae": 143481, "eg": 143516, "ma": 143620, "dz": 143563, "tn": 143536, "qa": 143498, "kw": 143493,
    "bh": 143559, "om": 143562, "jo": 143528, "lb": 143497, "il": 143491, "za": 143472, "ng": 143561,
}
ALPHABETS = {
    "ar": "ابتثجحخدذرزسشصضطظعغفقكلمنهوي",
    "ru": "абвгдежзиклмнопрстуфхцчшэюя",
}
LATIN = "abcdefghijklmnopqrstuvwxyz"
VIP_RETEST_DAYS = 3


def suggest(term, country):
    """Autocomplete dyal App Store: nefs kalimat li kaybano mli nas kayktbo f search (b tartib dyal popularity)."""
    sf = STOREFRONTS.get(country)
    if not sf:
        return []
    h = fetch("https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints?clientApplication=Software&term="
              + urllib.parse.quote(term), headers={"X-Apple-Store-Front": f"{sf},29"})
    return [html.unescape(m).strip().lower() for m in re.findall(r"<key>term</key>\s*<string>([^<]*)</string>", h)]


def domain_vocab(country):
    """Kalimat dyal game: seeds dyal lughat dyal bled + smiya/subtitle + keywords li ranké fihom."""
    seeds = load_seeds()
    vocab = set()
    for lang in COUNTRY_SEARCH_LANGS.get(country, ["en"]):
        for w in seeds.get(lang, []):
            vocab |= set(tokens(w, split_dots=False))
    vocab |= set(tokens(own_text(country)[0]))
    with db() as con:
        for r in con.execute("""SELECT k.term FROM keywords k JOIN ranks r ON r.keyword_id = k.id
                AND r.date = (SELECT MAX(date) FROM ranks WHERE keyword_id = k.id) WHERE k.country = ? AND r.rank IS NOT NULL""", (country,)):
            vocab |= set(tokens(r["term"], split_dots=False))
    return vocab - DESC_STOP


def is_relevant(term, vocab):
    # kalma kif kif, wla b "s" (dunk/dunks), wla kalma twila b nefs bidaya (basketball/basketballs)
    long5 = {w[:5] for w in vocab if len(w) >= 6}
    for tok in tokens(term, split_dots=False):
        if tok in vocab or tok.rstrip("s") in vocab or tok + "s" in vocab or (len(tok) >= 6 and tok[:5] in long5):
            return True
    return False


def suggestion_score(query, position):
    # suggestion li katban b recherche 9siira o f lfo9 = nas kay9elbo 3liha bzzaf
    return max(1, round(100 - position * 4 - max(0, len(query) - 3) * 1.5))


def ensure_suggest_queries(country, cfg):
    """Kayzid recherches l queue: seeds, bidayat kalimat (bas, bask...), keyword + 7arf (basketball a...)."""
    seeds = load_seeds()
    queries = []
    for lang in COUNTRY_SEARCH_LANGS.get(country, ["en"]):
        words = seeds.get(lang, [])
        alphabet = ALPHABETS.get(lang, LATIN if lang in ("en", "fr", "de", "es", "pt", "it", "tr", "id", "nl", "pl") else "")
        for w in words[:5]:
            queries += [w[:i] for i in range(3, len(w))]
        queries += words
        for w in words[:12]:
            queries += [f"{w} {ch}" for ch in alphabet]
    with db() as con:
        queries += [r["term"] for r in con.execute("""SELECT k.term FROM keywords k JOIN ranks r ON r.keyword_id = k.id
            AND r.date = (SELECT MAX(date) FROM ranks WHERE keyword_id = k.id) WHERE k.country = ? AND r.rank IS NOT NULL""", (country,))]
        for q in dict.fromkeys(x.strip().lower() for x in queries if len(x.strip()) >= 3):
            con.execute("INSERT OR IGNORE INTO suggest_queries (country, query) VALUES (?, ?)", (country, q))


def harvest_suggestions(country, cfg, n, d):
    """Kaydir n recherches f autocomplete (li 3mmrhom ma tdarou lwlin) o kaysjjel suggestions.
    Kol suggestion jdida katwelli recherche 7ta hiya (snowball)."""
    ensure_suggest_queries(country, cfg)
    cutoff = (datetime.date.fromisoformat(d) - datetime.timedelta(days=RETEST_DAYS)).isoformat()
    with db() as con:
        todo = [r["query"] for r in con.execute("""SELECT query FROM suggest_queries WHERE country = ?
            AND (done_at IS NULL OR done_at < ?) ORDER BY done_at IS NOT NULL, done_at, rowid LIMIT ?""", (country, cutoff, n))]
    new = 0
    vocab = domain_vocab(country)
    for q in todo:
        try:
            terms = [x for x in suggest(q, country) if is_relevant(x, vocab)]
        except Exception as e:
            log(f"{country.upper()} suggest '{q}': {e}")
            continue
        with db() as con:
            con.execute("UPDATE suggest_queries SET done_at = ?, results = ? WHERE country = ? AND query = ?", (d, len(terms), country, q))
            for pos, term in enumerate(terms):
                if not term or TRADEMARKS & set(term.split()):
                    continue
                score = suggestion_score(q, pos)
                cur = con.execute("SELECT score FROM suggestions WHERE country = ? AND term = ?", (country, term)).fetchone()
                if cur is None:
                    new += 1
                    con.execute("INSERT INTO suggestions VALUES (?,?,?,?,?,?)", (country, term, score, q, d, d))
                    con.execute("INSERT OR IGNORE INTO suggest_queries (country, query) VALUES (?, ?)", (country, term))
                else:
                    con.execute("UPDATE suggestions SET last_seen = ?, score = MAX(score, ?) WHERE country = ? AND term = ?",
                                (d, score, country, term))
    return len(todo), new


def vip_candidates(country, cfg):
    """Suggestions dyal Apple li mazal ma ttestaw (wla ttestaw 9bel 3 iyam), b score."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=VIP_RETEST_DAYS)).isoformat()
    removed = {r.partition("|")[0] for r in cfg.get("removed_keywords", []) if r.endswith("|" + country)}
    with db() as con:
        tracked = {r["term"] for r in con.execute("SELECT term FROM keywords WHERE country=?", (country,))}
        rows = con.execute("""SELECT s.term, s.query FROM suggestions s
            LEFT JOIN candidates c ON c.country = s.country AND c.term = s.term
            WHERE s.country = ? AND (c.tested_at IS NULL OR c.tested_at < ?)
            ORDER BY c.tested_at IS NOT NULL, s.score DESC""", (country, cutoff)).fetchall()
    return [(r["term"], f"vip:{r['query']}") for r in rows if r["term"] not in tracked and r["term"] not in removed]


def analyze_competitors(country, app_id, d):
    """Concurrents = apps (nefs category dyal app) li kaybano bzzaf f top 10 dyal keywords dyalna."""
    with db() as con:
        rows = con.execute("""SELECT s.app_id, s.name, s.ratings, s.pos FROM serp s JOIN keywords k ON k.id = s.keyword_id
                              WHERE k.country = ? AND s.date = (SELECT MAX(date) FROM serp WHERE keyword_id = s.keyword_id)""",
                           (country,)).fetchall()
        own = con.execute("SELECT data FROM app_info WHERE country=? ORDER BY date DESC LIMIT 1", (country,)).fetchone()
    own_genre = (json.loads(own["data"]).get("genre_ids") or [None])[0] if own else None
    stats = {}
    for r in rows:
        if r["app_id"] == app_id:
            continue
        st = stats.setdefault(r["app_id"], {"n": 0, "pos": 0})
        st["n"] += 1
        st["pos"] += r["pos"]
    top = sorted(stats, key=lambda a: (-stats[a]["n"], stats[a]["pos"] / stats[a]["n"]))[:40]
    info = lookup(top, country) if top else {}
    localized = {lang: lookup(top, country, lang) for lang in COUNTRY_LANGS.get(country, [])} if top else {}
    kept = 0
    with db() as con:
        con.execute("DELETE FROM competitors WHERE date=? AND country=?", (d, country))
        for aid in top:
            r = info.get(aid)
            if not r or (own_genre and str(r.get("primaryGenreId")) != str(own_genre)):
                continue
            st = stats[aid]
            terms = competitor_terms(r.get("trackName", ""), r.get("description", ""))
            for lang, li in localized.items():
                lr = li.get(aid) or {}
                if (lr.get("trackName"), lr.get("description")) != (r.get("trackName"), r.get("description")):
                    terms += [x for x in competitor_terms(lr.get("trackName", ""), lr.get("description", ""), 15) if x not in terms]
            con.execute("INSERT INTO competitors VALUES (?,?,?,?,?,?,?,?,?)", (
                d, country, aid, r.get("trackName"), r.get("userRatingCount"), st["n"], round(st["pos"] / st["n"], 1),
                r.get("trackViewUrl"), json.dumps(terms[:45])))
            kept += 1
            if kept == COMPETITORS_PER_COUNTRY:
                break
    return kept


POOL_REFRESH_DAYS = 30   # keywords dyal app f pool kayt3awdo ytjabdo mn ba3d had l iyam
POOL_NEW_APPS_PER_RUN = 80


def grow_pool(country, app_id, d):
    """Snowball: apps li f top 10 dyal keywords fin app dyalna f top 10 ➜ pool_apps,
    o keywords dyalhom (b ga3 lughat dyal bled) ➜ pool_terms. App msjjla 9bel kattjawz."""
    cutoff = (datetime.date.fromisoformat(d) - datetime.timedelta(days=POOL_REFRESH_DAYS)).isoformat()
    with db() as con:
        rows = con.execute("""SELECT k.term, s.app_id FROM keywords k
            JOIN ranks r ON r.keyword_id = k.id AND r.date = (SELECT MAX(date) FROM ranks WHERE keyword_id = k.id)
            JOIN serp s ON s.keyword_id = k.id AND s.date = (SELECT MAX(date) FROM serp WHERE keyword_id = k.id)
            WHERE k.country = ? AND r.rank IS NOT NULL AND r.rank <= 10 ORDER BY r.rank, s.pos""", (country,)).fetchall()
        fresh = {r["app_id"] for r in con.execute(
            "SELECT app_id FROM pool_apps WHERE country=? AND refreshed_at >= ?", (country, cutoff))}
        own = con.execute("SELECT data FROM app_info WHERE country=? ORDER BY date DESC LIMIT 1", (country,)).fetchone()
    own_genre = (json.loads(own["data"]).get("genre_ids") or [None])[0] if own else None
    from_term = {}
    for r in rows:
        if r["app_id"] != app_id and r["app_id"] not in fresh:
            from_term.setdefault(r["app_id"], r["term"])
    new_ids = list(from_term)[:POOL_NEW_APPS_PER_RUN]
    if not new_ids:
        return 0, 0
    info = lookup(new_ids, country)
    localized = {lang: lookup(new_ids, country, lang) for lang in COUNTRY_LANGS.get(country, [])}
    apps = terms_added = 0
    with db() as con:
        for aid in new_ids:
            r = info.get(aid)
            if not r or (own_genre and str(r.get("primaryGenreId")) != str(own_genre)):
                continue
            terms = [(x, None) for x in competitor_terms(r.get("trackName", ""), r.get("description", ""), 30)]
            for lang, li in localized.items():
                lr = li.get(aid) or {}
                if (lr.get("trackName"), lr.get("description")) != (r.get("trackName"), r.get("description")):
                    seen = {x for x, _ in terms}
                    terms += [(x, lang) for x in competitor_terms(lr.get("trackName", ""), lr.get("description", ""), 20) if x not in seen]
            before = con.total_changes
            for term, lang in terms:
                con.execute("""INSERT INTO pool_terms (country, term, app_id, added_at, n_apps, lang) VALUES (?,?,?,?,1,?)
                               ON CONFLICT(country, term) DO UPDATE SET n_apps = n_apps + 1""", (country, term, aid, d, lang))
            terms_added += con.execute("SELECT COUNT(*) FROM pool_terms WHERE country=? AND app_id=? AND added_at=?",
                                       (country, aid, d)).fetchone()[0] if con.total_changes > before else 0
            con.execute("""INSERT INTO pool_apps VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(country, app_id) DO UPDATE SET
                           name=excluded.name, ratings=excluded.ratings, refreshed_at=excluded.refreshed_at, n_terms=excluded.n_terms""",
                        (country, aid, r.get("trackName"), r.get("userRatingCount"), from_term[aid], d, d, len(terms)))
            apps += 1
    return apps, terms_added


def pool_candidates(country, cfg, n, exclude):
    """Keywords mn pool li mazal ma ttestaw (lwlin), mn ba3d li ttestaw 9bel RETEST_DAYS."""
    if n <= 0:
        return []
    cutoff = (datetime.date.today() - datetime.timedelta(days=RETEST_DAYS)).isoformat()
    removed = {r.partition("|")[0] for r in cfg.get("removed_keywords", []) if r.endswith("|" + country)}
    with db() as con:
        tracked = {r["term"] for r in con.execute("SELECT term FROM keywords WHERE country=?", (country,))}
        rows = con.execute("""SELECT p.term, a.name, c.tested_at FROM pool_terms p
            LEFT JOIN pool_apps a ON a.country = p.country AND a.app_id = p.app_id
            LEFT JOIN candidates c ON c.country = p.country AND c.term = p.term
            WHERE p.country = ? AND (c.tested_at IS NULL OR c.tested_at < ?)
            ORDER BY p.lang IS NOT NULL, c.tested_at IS NOT NULL, c.tested_at, p.n_apps DESC, p.added_at""", (country, cutoff)).fetchall()
    out = []
    for r in rows:
        term = r["term"]
        words = set(term.split())
        if term in tracked or term in removed or term in exclude or words & (TRADEMARKS | STOPWORDS | DESC_STOP):
            continue
        out.append((term, f"pool:{r['name'] or ''}"))
        if len(out) == n:
            break
    return out


def latest_competitors(country):
    with db() as con:
        return [dict(r) for r in con.execute("""SELECT * FROM competitors WHERE country=? AND
                    date=(SELECT MAX(date) FROM competitors WHERE country=?) ORDER BY appearances DESC, avg_pos""",
                    (country, country))]


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
        add(k, 100, "my_keywords")
    # keywords b kol lughat dyal had lbled (tartib dyal seeds.json = priorité)
    seeds = load_seeds()
    for lang in COUNTRY_SEARCH_LANGS.get(country, ["en"]):
        for i, term in enumerate(seeds.get(lang, [])):
            add(term.lower(), 72 - i * 0.2, f"seed:{lang}")
    for w in own_words:
        add(w, 60 + market_uni[w] * 5, "own_title")
    for p in phrases(title_text, 2):
        add(p, 55 + market_bi[p] * 5, "own_title")
    for w, c in market_uni.most_common(60):
        if c >= 2:
            add(w, 20 + c * 5 + (10 if w in own_words else 0), f"competitors:{c}")
    for p, c in market_bi.most_common(40):
        if c >= 2:
            add(p, 25 + c * 6, f"competitors:{c}")
    for w, c in desc_words.most_common(25):
        if c >= 2:
            add(w, 15 + c * 2, "description")
    comp_terms, comp_name = Counter(), {}
    for comp in latest_competitors(country):
        for term in json.loads(comp["terms"] or "[]"):
            comp_terms[term] += 1
            comp_name.setdefault(term, comp["name"])
    for term, c in comp_terms.most_common(120):
        add(term, 30 + c * 8, f"competitor:{comp_name[term]}" if c == 1 else f"competitors:{c}")
    core = sorted((w for w in own_words if "." not in w), key=lambda w: -market_uni[w])[:6]
    for a in core:
        for b, c in market_uni.most_common(15):
            if c < 2 or "." in b or a in b or b in a:
                continue
            # kalma li kayna kter 3nd concurrents (b7al "basketball") kat ji lekher: "street basketball"
            first, last = (a, b) if market_uni[a] <= market_uni[b] else (b, a)
            add(f"{first} {last}", 18 + min(c, 10) * 2, "combo")

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


def run_job(discover=True, only_country=None, keyword_ids=None, hourly=False):
    """Check kamel. Ila t3taw keyword_ids, kaychecki ghir had keywords (bla app info, charts o discovery).
    hourly=True: ghir pool o discovery (kol sa3a), bla check dyal keywords li kattba3o.
    STATUS["step"] = code ("info:US", "keyword:term|US"...) bach dashboard yterjmo."""
    if not _job_lock.acquire(blocking=False):
        return False
    started = time.time()
    global _last_checkpoint
    _last_checkpoint = started
    try:
        STATUS.update(running=True, error="", done=0, total=0, log=[])
        cfg = load_config()
        app_id = str(cfg["app_id"])
        countries = [only_country] if only_country else cfg["countries"]
        keywords_only = keyword_ids is not None
        d = today()

        with db() as con:
            if hourly:
                # keywords li ma tchekawch f check d sba7 (ila ma kmlch), bach ykmlo f runs dyal kol sa3a
                kws = con.execute("""SELECT k.* FROM keywords k WHERE k.country IN (%s) AND NOT EXISTS
                                     (SELECT 1 FROM ranks r WHERE r.keyword_id = k.id AND r.date = ?)""" % ",".join("?" * len(countries)),
                                  countries + [d]).fetchall() if meta_get("last_run") == d else []
            elif keywords_only:
                kws = [r for kid in keyword_ids for r in con.execute("SELECT * FROM keywords WHERE id=?", (kid,))]
            else:
                kws = con.execute("SELECT * FROM keywords WHERE country IN (%s)" % ",".join("?" * len(countries)),
                                  countries).fetchall()
        prio_cc = cfg.get("priority_countries", ["us"])
        if not keywords_only:
            kws = sorted(kws, key=lambda k: prio_cc.index(k["country"]) if k["country"] in prio_cc else len(prio_cc))
        n_cand = cfg["candidates_per_run"] if discover and not keywords_only else 0
        n_pool = int(cfg.get("pool_per_run", 0)) if discover and not keywords_only else 0
        budget = float(cfg.get("time_budget_minutes", 0) or 0)
        deadline = min(started + budget * 60 if budget else float("inf"), started + HARD_LIMIT_MIN * 60)
        STATUS["total"] = len(kws) + (0 if keywords_only or hourly else len(countries) * 6)

        # 1. ma3lomat 3la app
        genres = ["6014"]
        for cc in [] if keywords_only or hourly else countries:
            STATUS["step"] = f"info:{cc.upper()}"
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
                    info["localized"] = {}
                    for lang in COUNTRY_LANGS.get(cc, []):
                        lr = lookup([app_id], cc, lang).get(app_id) or {}
                        if lr.get("trackName") and (lr.get("trackName"), lr.get("description")) != (r.get("trackName"), r.get("description")):
                            info["localized"][lang] = {"name": lr.get("trackName"), "description": lr.get("description", "")}
                    for g in r.get("genreIds", []):
                        if g.startswith("70") and g not in genres:
                            genres.append(g)
                    with db() as con:
                        con.execute("INSERT OR REPLACE INTO app_info VALUES (?,?,?)", (d, cc, json.dumps(info)))
                else:
                    log(f"{cc.upper()}: app not available in this store")
            except Exception as e:
                log(f"{cc.upper()}: lookup failed: {e}")
            STATUS["done"] += 2

        # 2. top charts
        for cc in [] if keywords_only or hourly else countries:
            STATUS["step"] = f"charts:{cc.upper()}"
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
        hard_deadline = started + HARD_LIMIT_MIN * 60
        for i, k in enumerate(kws):
            if time.time() > hard_deadline:
                log(f"Time limit: {len(kws) - i} tracked keywords left, the next run continues.")
                break
            maybe_checkpoint()
            STATUS["step"] = f"keyword:{k['term']}|{k['country'].upper()}"
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

        # 4. concurrents: chkoun kaybano m3ana o chno keywords dyalhom
        for cc in [] if keywords_only or hourly else countries:
            STATUS["step"] = f"competitors:{cc.upper()}"
            try:
                analyze_competitors(cc, app_id, d)
            except Exception as e:
                log(f"{cc.upper()} competitors: {e}")
            STATUS["done"] += 1

        # 5a. suggestions dyal App Store (VIP)
        n_sug = int(cfg.get("suggest_per_run", 60)) if discover and not keywords_only else 0
        for cc in [] if not n_sug else sorted(countries, key=lambda c: prio_cc.index(c) if c in prio_cc else len(prio_cc)):
            STATUS["step"] = f"suggest:{cc.upper()}"
            try:
                done, new = harvest_suggestions(cc, cfg, n_sug * (2 if cc in prio_cc else 1), d)
                if new:
                    log(f"{cc.upper()} App Store suggestions: {done} searches, +{new} new")
            except Exception as e:
                log(f"{cc.upper()} suggestions: {e}")

        # 5b. pool (snowball): apps li f top 10 m3ana o keywords dyalhom
        for cc in countries if discover and not keywords_only else []:
            STATUS["step"] = f"pool:{cc.upper()}"
            try:
                apps, terms = grow_pool(cc, app_id, d)
                if apps:
                    log(f"{cc.upper()} pool: +{apps} apps, +{terms} keywords")
            except Exception as e:
                log(f"{cc.upper()} pool: {e}")

        # 6. discovery: keywords jdad (ideas + pool), bled b bled 7ta ykmel budget d w9t
        found = tested = 0
        queues, seen_terms = {}, {}
        if discover and not keywords_only:
            for cc in countries:
                # VIP (suggestions dyal Apple) lwlin, mn ba3d ideas, mn ba3d pool
                q = vip_candidates(cc, cfg)
                q += [x for x in generate_candidates(cc, cfg)[:n_cand] if x[0] not in {t for t, _ in q}]
                # pool_per_run = 0 ➜ bla 7ad (budget d w9t howa li kay7bes)
                q += pool_candidates(cc, cfg, n_pool if n_pool else 100000, {t for t, _ in q})
                with db() as con:
                    seen = {r["term"] for r in con.execute("SELECT term FROM candidates WHERE country=?", (cc,))}
                # keywords jdad (3mmrhom ma ttestaw) dima 9bel li kayt3awdo, o kol wa7d b tartib dyalo
                q.sort(key=lambda x: x[0] in seen)
                queues[cc] = q
                seen_terms[cc] = seen
            STATUS["total"] = STATUS["done"] + sum(map(len, queues.values()))
        prio = [c for c in cfg.get("priority_countries", ["us"]) if c in queues]
        for cc in prio:
            # f bled prioritaire: keywords b lugha par défaut (English f US) lwlin
            main_lang = (COUNTRY_SEARCH_LANGS.get(cc) or ["en"])[0]
            seen = seen_terms.get(cc, set())
            queues[cc].sort(key=lambda x: (x[0] in seen, not x[1].startswith("vip:"),
                                           x[1].startswith("seed:") and x[1] != f"seed:{main_lang}"))
        while any(queues.values()):
            pending = [c for c in prio if queues.get(c)]
            for cc in (pending[:1] if pending else countries):
                if not queues.get(cc):
                    continue
                if time.time() > deadline:
                    log(f"Time budget reached: {sum(map(len, queues.values()))} keywords left for the next run.")
                    queues = {}
                    break
                maybe_checkpoint()
                term, origin = queues[cc].pop(0)
                STATUS["step"] = f"discovery:{term}|{cc.upper()}"
                try:
                    rank, depth, method, top_ids, subs = check_rank(term, cc, app_id, fast=True)
                    tested += 1
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
                        log(f"✅ New keyword: '{term}' ({cc.upper()}) rank #{rank}")
                except Exception as e:
                    log(f"discovery '{term}' {cc}: {e}")
                STATUS["done"] += 1
        if tested:
            log(f"Discovery: {tested} keywords tested in {(time.time() - started) / 60:.0f} min (delay {_delay:.1f}s).")

        if not only_country and not keywords_only and not hourly:
            meta_set("last_run", d)
            meta_set("last_run_at", datetime.datetime.now().isoformat(timespec="minutes"))
        meta_set("last_refresh_at", datetime.datetime.now().isoformat(timespec="minutes"))
        meta_set("api_mode", "blocked" if _api_blocked else "ok")
        log(f"Done. {len(kws)} keywords checked, {found} new keywords found.")
        STATUS["step"] = "done"
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
        "status": STATUS, "last_run_at": meta_get("last_run_at"), "last_refresh_at": meta_get("last_refresh_at"),
        "api_mode": meta_get("api_mode"),
    }


# Share ta9ribi mn installs dyal keyword 3la 7sab blasa f resultats (CTR)
def rank_share(rank):
    if not rank:
        return 0
    for limit, share in ((1, .30), (2, .17), (3, .11), (4, .08), (5, .06), (10, .03), (20, .01), (50, .002)):
        if rank <= limit:
            return share
    return 0


def keyword_metrics(term, apps, rank):
    """Ta9dir (machi data dyal Apple): popularity, difficulty o 9ima dyal keyword mn top 10 apps.
    - popularity: ratings dyal apps li 3ndhom 3la9a b keyword. Ila Apple 3mmrat resultats b apps
      bla 3la9a (b7al Instagram f "basketball.io"), ya3ni keyword nadir.
    - difficulty: 9owat top 10 (median dyal ratings).
    - value: popularity x share dyal blasa dyalk."""
    if not apps:
        return {"pop": None, "diff": None, "value": None, "value_label": None}
    words = tokens(term, split_dots=False) or [term.lower()]
    stems = [w[:max(4, len(w) - 2)] for w in words]

    def weight(name):
        # app li fih kolchi kalimat dyal keyword = 1; fih ghir chi wa7da = a9al bzzaf
        n = (name or "").lower()
        return (sum(st in n for st in stems) / len(stems)) ** 2

    weights = [weight(a["name"]) for a in apps]
    rel_sum = sum(w * (a["ratings"] or 0) for w, a in zip(weights, apps))
    pop = math.log10(rel_sum + 1) / 6 * 100  # ~1M ratings = 100
    if sum(1 for w in weights if w) < 0.3 * len(apps):
        pop *= 0.5
    n_words = len(term.split())
    pop *= 1.1 if n_words == 1 else 1.0 if n_words == 2 else 0.85
    # difficulty: ghir apps li 3ndhom 3la9a (apps bla 3la9a kaybano ghir mli keyword nadir)
    rel_apps = [a for w, a in zip(weights, apps) if w] or []
    logs = sorted(math.log10((a["ratings"] or 0) + 1) for a in rel_apps)
    diff = logs[len(logs) // 2] / 5 * 100 if logs else 0  # median ~100k ratings = 100
    pop, diff = round(min(100, max(0, pop))), round(min(100, max(0, diff)))
    value = round(pop * rank_share(rank), 1)
    if rank and rank <= 50:
        label = "high" if value >= 10 else "medium" if value >= 3 else "low" if value > 0.2 else "none"
    else:
        label = "target" if pop >= 45 and diff <= 60 else "none"
    return {"pop": pop, "diff": diff, "value": value, "value_label": label}


def api_keywords():
    with db() as con:
        kws = [dict(r) for r in con.execute("SELECT * FROM keywords ORDER BY country, term")]
        ranks = {}
        for r in con.execute("SELECT * FROM ranks ORDER BY date"):
            ranks.setdefault(r["keyword_id"], []).append({"date": r["date"], "rank": r["rank"], "depth": r["depth"], "method": r["method"]})
        serps = {}
        for r in con.execute("""SELECT s.keyword_id, s.name, s.ratings FROM serp s
                                WHERE s.date = (SELECT MAX(date) FROM serp WHERE keyword_id = s.keyword_id)
                                ORDER BY s.keyword_id, s.pos"""):
            serps.setdefault(r["keyword_id"], []).append({"name": r["name"], "ratings": r["ratings"]})
        vip = {(r["country"], r["term"]): r["score"] for r in con.execute("SELECT country, term, score FROM suggestions")}
    for k in kws:
        h = ranks.get(k["id"], [])
        k["history"] = h[-90:]
        k["rank"] = h[-1]["rank"] if h else None
        k["depth"] = h[-1]["depth"] if h else None
        k["checked"] = h[-1]["date"] if h else None
        k["prev"] = h[-2]["rank"] if len(h) > 1 else None
        best = [x["rank"] for x in h if x["rank"]]
        k["best"] = min(best) if best else None
        k.update(keyword_metrics(k["term"], serps.get(k["id"], []), k["rank"]))
        # suggestion dyal Apple = nas kay9elbo 3liha b sa7: popularity ma kattn9ssch 3la score dyalha
        k["vip"] = vip.get((k["country"], k["term"]))
        if k["vip"]:
            k["pop"] = max(k["pop"] or 0, k["vip"])
            if k["value_label"] in (None, "none") and not (k["rank"] and k["rank"] <= 50) and k["pop"] >= 45 and (k["diff"] or 0) <= 60:
                k["value_label"] = "target"
            elif k["rank"] and k["rank"] <= 50:
                k["value"] = round(k["pop"] * rank_share(k["rank"]), 1)
                v = k["value"]
                k["value_label"] = "high" if v >= 10 else "medium" if v >= 3 else "low" if v > 0.2 else "none"
    return kws


def api_serp(keyword_id):
    with db() as con:
        row = con.execute("SELECT MAX(date) d FROM serp WHERE keyword_id=?", (keyword_id,)).fetchone()
        rows = con.execute("SELECT pos, app_id, name, ratings FROM serp WHERE keyword_id=? AND date=? ORDER BY pos",
                           (keyword_id, row["d"])).fetchall()
    return {"date": row["d"], "apps": [dict(r) for r in rows]}


def api_competitors():
    """Kol concurrent m3a keywords dyalo o status dyal app dyalna f kol wa7d."""
    out = {}
    with db() as con:
        kw_rank = {}
        for r in con.execute("""SELECT k.term, k.country, r.rank FROM keywords k LEFT JOIN ranks r ON r.keyword_id = k.id
                                AND r.date = (SELECT MAX(date) FROM ranks WHERE keyword_id = k.id)"""):
            kw_rank[(r["term"], r["country"])] = r["rank"]
        tested = {(r["term"], r["country"]) for r in con.execute("SELECT term, country FROM candidates")}
    for cc in load_config()["countries"]:
        comps = latest_competitors(cc)
        for comp in comps:
            terms = []
            for term in json.loads(comp.pop("terms") or "[]"):
                key = (term, cc)
                status = "ranked" if kw_rank.get(key) else "tracked" if key in kw_rank else "tested" if key in tested else "new"
                terms.append({"term": term, "status": status, "rank": kw_rank.get(key)})
            comp["terms"] = terms
        out[cc] = comps
    return out


def api_suggestions():
    out = {}
    with db() as con:
        for cc in load_config()["countries"]:
            rows = con.execute("""SELECT s.term, s.score, s.query, s.first_seen, c.tested_at, c.rank FROM suggestions s
                LEFT JOIN candidates c ON c.country = s.country AND c.term = s.term
                WHERE s.country = ? ORDER BY s.score DESC LIMIT 3000""", (cc,)).fetchall()
            done = con.execute("SELECT COUNT(*) n, SUM(done_at IS NOT NULL) d FROM suggest_queries WHERE country = ?", (cc,)).fetchone()
            tracked = {r["term"]: (r["date"], r["rank"]) for r in con.execute("""SELECT k.term, r.date, r.rank FROM keywords k
                JOIN ranks r ON r.keyword_id = k.id AND r.date = (SELECT MAX(date) FROM ranks WHERE keyword_id = k.id)
                WHERE k.country = ?""", (cc,))}
            items = []
            for r in rows:
                it = dict(r)
                if it["term"] in tracked:  # keyword kattba3o: rank mn check dyal kol nhar
                    it["tested_at"], it["rank"] = tracked[it["term"]]
                items.append(it)
            out[cc] = {"items": items, "queries": done["n"] or 0, "queries_done": done["d"] or 0}
    return out


def api_pool():
    out = {}
    with db() as con:
        for cc in load_config()["countries"]:
            stats = con.execute("""SELECT COUNT(*) terms, SUM(c.tested_at IS NOT NULL) tested, SUM(c.rank IS NOT NULL) ranked
                FROM pool_terms p LEFT JOIN candidates c ON c.country = p.country AND c.term = p.term WHERE p.country = ?""",
                (cc,)).fetchone()
            apps = [dict(r) for r in con.execute("""SELECT app_id, name, ratings, from_term, added_at, n_terms FROM pool_apps
                WHERE country = ? ORDER BY added_at DESC, ratings DESC""", (cc,))]
            out[cc] = {"apps": apps, "terms": stats["terms"] or 0, "tested": stats["tested"] or 0, "ranked": stats["ranked"] or 0}
    return out


def api_candidates():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM candidates ORDER BY tested_at DESC, rank IS NULL, rank LIMIT 5000")]


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
        if u.path == "/api/suggestions":
            return self.send(api_suggestions())
        if u.path == "/api/pool":
            return self.send(api_pool())
        if u.path == "/api/competitors":
            return self.send(api_competitors())
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
        if u.path == "/api/refresh":
            if STATUS["running"]:
                return self.send({"ok": False, "busy": True})
            ids = b.get("ids")
            if ids is None:
                with db() as con:
                    ids = [r["id"] for r in con.execute("SELECT id FROM keywords")]
            threading.Thread(target=run_job, kwargs={"keyword_ids": [int(i) for i in ids]}, daemon=True).start()
            return self.send({"ok": True})
        if u.path == "/api/run":
            if STATUS["running"]:
                return self.send({"ok": False, "msg": "deja khdam"})
            threading.Thread(target=run_job, kwargs={"discover": b.get("discover", True)}, daemon=True).start()
            return self.send({"ok": True})
        if u.path == "/api/config":
            cfg = load_config()
            for k in ("app_id", "countries", "daily_hour", "candidates_per_run", "pool_per_run", "time_budget_minutes", "my_keywords"):
                if k in b:
                    cfg[k] = b[k]
            if isinstance(cfg["countries"], str):
                cfg["countries"] = [c.strip().lower() for c in cfg["countries"].split(",") if c.strip()]
            cfg["daily_hour"] = int(cfg["daily_hour"])
            cfg["candidates_per_run"] = int(cfg["candidates_per_run"])
            cfg["pool_per_run"] = int(cfg.get("pool_per_run", 0))
            cfg["time_budget_minutes"] = int(cfg.get("time_budget_minutes", 0) or 0)
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
        "competitors.json": api_competitors(),
        "pool.json": api_pool(),
        "suggestions.json": api_suggestions(),
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
    ap.add_argument("--keywords", help='ghir had keywords: "all" wla "term|us;term2|fr"')
    ap.add_argument("--hourly", action="store_true", help="ghir pool o discovery (run dyal kol sa3a)")
    ap.add_argument("--auto", action="store_true",
                    help="run dyal schedule: check kamel mra f nhar (mn daily_hour_utc), o f lb9i discovery")
    args = ap.parse_args()
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
    init_db()
    migrate_db()
    cfg = load_config()
    sync_config_keywords(cfg)
    if args.run:
        keyword_ids = None
        if args.keywords:
            with db() as con:
                rows = con.execute("SELECT id, term, country FROM keywords").fetchall()
            wanted = {w.strip().lower() for w in args.keywords.split(";") if w.strip()}
            keyword_ids = [r["id"] for r in rows if "all" in wanted or f"{r['term']}|{r['country']}" in wanted]
        hourly = args.hourly
        if args.auto:
            # GitHub ymken yt2kher f schedule: check kamel ila mazal ma tdar lyoum o fatt sa3a dyalo
            now = datetime.datetime.now(datetime.timezone.utc)
            hourly = not (meta_get("last_run") != now.date().isoformat() and now.hour >= int(cfg.get("daily_hour_utc", 3)))
            log("Mode: " + ("hourly discovery" if hourly else "daily full check"))
        run_job(discover=not args.no_discover, keyword_ids=keyword_ids, hourly=hourly)
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
