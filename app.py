import os
import re
import sqlite3
import json
import base64
import html as html_lib
import instaloader
import uuid
import urllib.request
from openai import OpenAI

# ── Database driver abstraction ───────────────────────────────────────────────
# Use PostgreSQL (psycopg2) when DATABASE_URL is set (Vercel/production),
# fall back to SQLite for local development.
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_USE_PG = bool(_DATABASE_URL)

if _USE_PG:
    import psycopg2
    import psycopg2.extras
    from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

def _clean_pg_url(url: str) -> str:
    """Strip connection-string params psycopg2 doesn't understand (e.g. channel_binding)."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    # Remove params not supported by libpq / psycopg2
    for key in ("channel_binding",):
        params.pop(key, None)
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))

def _pg_con():
    con = psycopg2.connect(
        _clean_pg_url(_DATABASE_URL),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    con.autocommit = False
    return con

def get_db():
    if _USE_PG:
        return _pg_con()
    con = sqlite3.connect(os.environ.get("DB_PATH", "/tmp/recipes.db"))
    con.row_factory = sqlite3.Row
    return con

def _ph(n=1):
    """Return n positional placeholders for the active driver."""
    if _USE_PG:
        return tuple(f"%s" for _ in range(n))
    return tuple("?" for _ in range(n))

def _q(sql: str) -> str:
    """Convert ? placeholders to %s for PostgreSQL."""
    if _USE_PG:
        return sql.replace("?", "%s")
    return sql

def _fetchone(cur):
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)

def _fetchall(cur):
    return [dict(r) for r in cur.fetchall()]
from flask import (
    Flask, request, jsonify, send_from_directory,
    session, redirect, url_for,
)
from flask_cors import CORS
from authlib.integrations.flask_client import OAuth
from datetime import datetime
from functools import wraps

# Resolve paths relative to this file so they work when imported by Vercel
_HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(_HERE, "static"),
    template_folder=os.path.join(_HERE, "templates"),
)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
CORS(app, supports_credentials=True)

# ── Global error handler — always return JSON, never raw HTML 500 ─────────────
@app.errorhandler(Exception)
def _handle_any_exception(e):
    import traceback
    traceback.print_exc()
    return jsonify({"error": str(e)}), 500

IMAGES_DIR = os.environ.get("IMAGES_DIR", "/tmp/recipe_images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# ── Google OAuth ──────────────────────────────────────────────────────────────
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ── Instaloader ───────────────────────────────────────────────────────────────
def _make_loader():
    return instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
        max_connection_attempts=2,
    )

loader = _make_loader()


# ── DB ────────────────────────────────────────────────────────────────────────
def init_db():
    con = get_db()
    cur = con.cursor()
    if _USE_PG:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id         SERIAL PRIMARY KEY,
                google_id  TEXT UNIQUE NOT NULL,
                email      TEXT,
                name       TEXT,
                picture    TEXT,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS recipes (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                url         TEXT,
                shortcode   TEXT,
                title       TEXT,
                ingredients TEXT,
                steps       TEXT,
                raw_caption TEXT,
                image_url   TEXT,
                image_data  TEXT,
                local_image TEXT,
                author      TEXT,
                added_at    TEXT,
                UNIQUE(user_id, url)
            )
        """)
        # Migrations for tables created before these columns existed
        cur.execute("ALTER TABLE recipes ADD COLUMN IF NOT EXISTS image_data TEXT")
        cur.execute("ALTER TABLE recipes ADD COLUMN IF NOT EXISTS category TEXT")
    else:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                google_id   TEXT UNIQUE NOT NULL,
                email       TEXT,
                name        TEXT,
                picture     TEXT,
                created_at  TEXT
            );
            CREATE TABLE IF NOT EXISTS recipes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                url          TEXT,
                shortcode    TEXT,
                title        TEXT,
                ingredients  TEXT,
                steps        TEXT,
                raw_caption  TEXT,
                image_url    TEXT,
                image_data   TEXT,
                local_image  TEXT,
                author       TEXT,
                added_at     TEXT,
                UNIQUE(user_id, url)
            );
        """)
        for col in ("image_data TEXT", "category TEXT"):
            try:
                con.execute(f"ALTER TABLE recipes ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # column already exists
    con.commit()
    con.close()


def upsert_user(google_id, email, name, picture):
    con = get_db()
    cur = con.cursor()
    cur.execute(_q("""
        INSERT INTO users (google_id, email, name, picture, created_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(google_id) DO UPDATE SET
          email=excluded.email, name=excluded.name, picture=excluded.picture
    """), (google_id, email, name, picture, datetime.utcnow().isoformat()))
    con.commit()
    cur.execute(_q("SELECT * FROM users WHERE google_id=?"), (google_id,))
    row = _fetchone(cur)
    con.close()
    return row


# ── Auth helpers ──────────────────────────────────────────────────────────────
def current_user():
    return session.get("user")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "Not authenticated"}), 401

        # Re-verify the user exists in the current DB and refresh the session id.
        # This protects against stale session ids after a DB migration (e.g. SQLite→Postgres).
        google_id = user.get("google_id")
        if google_id:
            try:
                con = get_db()
                cur = con.cursor()
                cur.execute(_q("SELECT * FROM users WHERE google_id=?"), (google_id,))
                db_user = _fetchone(cur)
                con.close()
                if db_user:
                    session["user"] = db_user   # refresh with current DB id
                else:
                    # User missing from DB (new DB / cleared DB) — re-create them
                    db_user = upsert_user(
                        google_id,
                        user.get("email"),
                        user.get("name"),
                        user.get("picture"),
                    )
                    session["user"] = db_user
            except Exception:
                pass  # fall through using whatever is in the session

        return f(*args, **kwargs)
    return decorated


# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/auth/login")
def auth_login():
    redirect_uri = url_for("auth_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo") or google.userinfo()
    user = upsert_user(
        google_id=userinfo["sub"],
        email=userinfo.get("email"),
        name=userinfo.get("name"),
        picture=userinfo.get("picture"),
    )
    session["user"] = user
    return redirect("/")


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect("/")

@app.route("/auth/guest")
def auth_guest():
    # יצירת מזהה ייחודי לאורח כדי למנוע התנגשויות
    guest_id = f"guest_{uuid.uuid4().hex[:16]}"

    # יצירת המשתמש במסד הנתונים
    user = upsert_user(
        google_id=guest_id,
        email=None,
        name="Guest",
        picture=None,
    )

    session["user"] = user
    return redirect("/")

@app.route("/auth/me")
def auth_me():
    user = current_user()
    if not user:
        return jsonify(None)
    return jsonify({
        "id":      user["id"],
        "name":    user["name"],
        "email":   user["email"],
        "picture": user["picture"],
    })


# ── Recipe parsing ────────────────────────────────────────────────────────────
CATEGORIES = [
    "אפייה",
    "קינוח",
    "בישול יומיומי",
    "נשנוש ביניים",
    "סלטים",
    "מרקים",
    "משקאות",
    "אחר",
]


def _normalize_category(cat: str) -> str:
    cat = (cat or "").strip()
    return cat if cat in CATEGORIES else "אחר"


_openai_client = None

def _get_openai():
    global _openai_client
    if _openai_client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            _openai_client = OpenAI(api_key=api_key)
    return _openai_client


def _parse_with_gpt(caption: str) -> dict | None:
    """Call gpt-4o-mini to extract title, ingredients and steps from a caption.
    Returns None if the API key is missing or the call fails."""
    client = _get_openai()
    if not client:
        return None

    system_prompt = """You are a recipe parser. Given an Instagram post caption (which may be in any language), extract the recipe and return ONLY valid JSON — no markdown, no explanation.

The JSON must follow this exact structure:
{
  "title": "recipe name",
  "category": "one of the allowed categories",
  "ingredients": [
    "__section__For the base",
    "200g biscuit crumbs",
    "__section__For the filling",
    "500g cream cheese"
  ],
  "steps": [
    "__section__Prepare the base",
    "Mix biscuits with melted butter and press into a pan.",
    "Beat cream cheese with sugar until smooth."
  ]
}

Rules:
- Preserve the original language for all text (do NOT translate).
- category MUST be exactly one of these Hebrew values:
  "אפייה" (breads, pastries, doughs), "קינוח" (cakes, cookies, sweets),
  "בישול יומיומי" (everyday savory cooking, mains, sides),
  "נשנוש ביניים" (snacks, energy bites, finger food),
  "סלטים" (salads), "מרקים" (soups), "משקאות" (drinks, smoothies), "אחר" (anything else).
- Use "__section__<name>" entries (no colon) to mark sub-group headings inside ingredients or steps.
- ingredients: list every ingredient on its own line, no bullet symbols.
- steps: one clear action per item, no numbering.
- Strip hashtags and unrelated promotional text.
- If there are no distinct steps (only ingredients), return an empty steps array.
- If title is unclear, infer it from context."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": caption},
            ],
            temperature=0,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        return {
            "title":       data.get("title", "Untitled Recipe"),
            "category":    _normalize_category(data.get("category", "")),
            "ingredients": data.get("ingredients", []),
            "steps":       data.get("steps", []),
        }
    except Exception as e:
        print(f"[GPT parse error] {e}")
        return None


def _guess_category(caption: str) -> str:
    """Keyword heuristic used when GPT is unavailable."""
    c = caption or ""
    rules = [
        ("משקאות",       r'שייק|סמוזי|משקה|קוקטייל|לימונדה|קפה קר'),
        ("מרקים",        r'מרק'),
        ("סלטים",        r'סלט'),
        ("קינוח",        r'\bעוג|מוס|קינוח|גלידה|בראוניז|פאדג|טירמיסו|מלבי|קרם שניט'),
        ("אפייה",        r'לחם|חלה|פוקצ|מאפה|בצק שמרים|פיתות|בייגל|קרואסון|בורקס'),
        ("נשנוש ביניים", r'חטיף|כדורי אנרגיה|נשנוש|קרקר|צ\'יפס'),
    ]
    for cat, pattern in rules:
        if re.search(pattern, c):
            return cat
    return "בישול יומיומי"


def _parse_with_regex(caption: str) -> dict:
    """Regex/heuristic fallback parser."""
    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    title = lines[0] if lines else "Untitled Recipe"
    title = re.sub(r'^[\U00010000-\U0010ffff☀-➿\s]+', '', title).strip() or lines[0]

    ingredient_headers = re.compile(
        r'(ingredient|what you.?ll need|you.?ll need|needs|for the|materials'
        r'|מצרכים|חומרים|רכיבים)', re.I)
    step_headers = re.compile(
        r'(instruction|direction|method|how to|steps?|preparation|let.?s make|make it|procedure'
        r'|הוראות הכנה|אופן הכנה|שלבי הכנה|דרך הכנה|הכנה\s*:)', re.I)
    measurement_re = re.compile(
        r'\b(\d+[\./\d]*\s*('
        r'גרם|ג\'|ק"ג|קג|כוס|כוסות|כף|כפות|כפית|כפיות|מ"ל|מל|ליטר|יח|יחידות'
        r'|cup|tbsp|tsp|g|kg|ml|oz|lb'
        r'))\b', re.I)

    ingredients: list = []
    steps: list = []
    mode = None

    for line in lines[1:]:
        clean = re.sub(r'#\S+', '', line).strip()
        if not clean or all(w.startswith('#') for w in clean.split()):
            continue
        bullet = re.match(r'^[-•✔✅🔸🔹▶️➡️➤*]\s*', clean)
        if bullet:
            clean = clean[bullet.end():]
        if ingredient_headers.search(clean):
            mode = 'ingredients'
            ingredients.append(f'__section__{re.sub(r"[:：]\\s*$", "", clean).strip()}')
            continue
        if step_headers.search(clean):
            mode = 'steps'
            continue
        if re.match(r'^.{1,40}[:：]\s*$', clean) and not re.search(r'\d', clean):
            label = clean.rstrip(':：').strip()
            if mode == 'steps':
                steps.append(f'__section__{label}')
            else:
                mode = 'ingredients'
                ingredients.append(f'__section__{label}')
            continue
        if re.match(r'^\d+[\.\)]\s', clean):
            mode = 'steps'
            steps.append(re.sub(r'^\d+[\.\)]\s*', '', clean))
            continue
        if mode == 'ingredients':
            ingredients.append(clean)
        elif mode == 'steps':
            steps.append(clean)
        elif mode is None and measurement_re.search(clean):
            mode = 'ingredients'
            ingredients.append(clean)

    real_ing = [i for i in ingredients if not i.startswith('__section__')]
    if not real_ing and not steps:
        steps = [re.sub(r'#\S+', '', l).strip() for l in lines[1:] if l.strip()]
        ingredients = []

    return {"title": title, "category": _guess_category(caption),
            "ingredients": ingredients, "steps": steps}


def parse_recipe(caption: str) -> dict:
    if not caption:
        return {"title": "Untitled Recipe", "category": "אחר",
                "ingredients": [], "steps": []}
    # Try GPT first, fall back to regex if unavailable or erroring
    return _parse_with_gpt(caption) or _parse_with_regex(caption)


def shortcode_from_url(url: str) -> str:
    m = re.search(r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    if not m:
        raise ValueError("Could not parse Instagram shortcode from URL")
    return m.group(1)


def _fetch_post_with_fresh_loader(shortcode: str):
    """Fetch an Instagram post using a brand-new loader context each time.
    A stale context can cause NoneType errors when Instagram returns
    an unexpected response, so we never reuse the module-level loader."""
    fresh = _make_loader()
    return instaloader.Post.from_shortcode(fresh.context, shortcode)


_CRAWLER_HEADERS = {
    # Instagram serves full OG meta tags (caption + image) to link-preview
    # crawlers, without login and regardless of the requester's IP.
    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Accept-Language": "en",
}

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def _http_get(url: str, timeout: int = 20, headers: dict = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or _BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_via_og(shortcode: str) -> dict | None:
    """Fetch the post page as a link-preview crawler and read the
    Open Graph meta tags. Returns caption (~first 1300 chars), image
    URL and author — or None if the tags aren't served."""
    try:
        raw = _http_get(
            f"https://www.instagram.com/p/{shortcode}/",
            headers=_CRAWLER_HEADERS,
        ).decode("utf-8", "ignore")
    except Exception:
        return None

    def og(prop):
        m = re.search(rf'property="og:{prop}"\s+content="([^"]*)"', raw)
        return html_lib.unescape(m.group(1)) if m else ""

    image_url = og("image")
    title     = og("title")
    desc      = og("description")

    # og:title looks like:  Some Name on Instagram‎: "CAPTION"
    caption = ""
    m = re.search(r'on Instagram[^:]*:\s*["“](.*)["”]?\s*$', title, re.S)
    if m:
        caption = m.group(1).strip().rstrip('"”').strip()

    # og:description looks like:  123 likes, 4 comments - username on July 9, 2026: "CAPTION"
    author = ""
    m = re.search(r'-\s*([\w.]+)\s+on\s+\w+\s+\d', desc)
    if m:
        author = m.group(1)
    if not caption and desc:
        m = re.search(r':\s*["“](.*)["”]?\s*$', desc, re.S)
        if m:
            caption = m.group(1).strip().rstrip('"”').strip()

    if not caption and not image_url:
        return None
    return {"caption": caption, "image_url": image_url, "author": author}


def _download_image_b64(image_url: str) -> str:
    """Download an image and return it base64-encoded (empty string on failure)."""
    try:
        data = _http_get(image_url)
        if data and len(data) < 4_000_000:   # cap ~4MB to keep DB rows sane
            return base64.b64encode(data).decode("ascii")
    except Exception:
        pass
    return ""


def fetch_instagram_post(url: str) -> dict:
    shortcode = shortcode_from_url(url)

    caption = image_url = author = ""

    # Strategy 1: OG meta tags via crawler user-agent (most reliable)
    og = _fetch_via_og(shortcode)
    if og:
        caption   = og["caption"]
        image_url = og["image_url"]
        author    = og["author"]

    # Strategy 2: instaloader (full caption, but often blocked on Vercel)
    if not caption:
        try:
            post = _fetch_post_with_fresh_loader(shortcode)
            caption   = post.caption or caption
            image_url = post.url or image_url
            author    = getattr(post, "owner_username", "") or author
        except Exception as e:
            if not og:
                msg = str(e) or "Instagram returned an unexpected response."
                raise RuntimeError(msg)

    # Persist the actual image bytes so it never expires
    image_data = _download_image_b64(image_url) if image_url else ""

    recipe = parse_recipe(caption)
    return {
        "shortcode": shortcode,
        "url": url,
        "author": author,
        "raw_caption": caption,
        "image_url": image_url,
        "image_data": image_data,
        "local_image": f"/api/image/{shortcode}",
        **recipe,
    }


# ── Recipe routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/api/image/<shortcode>")
def serve_image(shortcode):
    """
    Proxy route for recipe thumbnails. Tries the local /tmp cache first;
    if missing (cold Vercel start), re-fetches from the CDN URL stored in DB.
    """
    from flask import Response
    import re as _re

    # Basic safety check on shortcode
    if not _re.match(r'^[A-Za-z0-9_-]{1,50}$', shortcode):
        return "Not found", 404

    con = get_db()
    cur = con.cursor()
    cur.execute(_q("SELECT id, image_url, image_data FROM recipes WHERE shortcode=? LIMIT 1"),
                (shortcode,))
    row = _fetchone(cur)
    con.close()
    if not row:
        return "Not found", 404

    # 1. Image stored permanently in the DB — always wins
    if row.get("image_data"):
        try:
            data = base64.b64decode(row["image_data"])
            return Response(data, mimetype="image/jpeg",
                            headers={"Cache-Control": "public, max-age=31536000"})
        except Exception:
            pass

    # 2. Old recipe without stored bytes — try to recover the image
    #    and persist it so this never has to happen again.
    b64 = ""
    if row.get("image_url"):
        b64 = _download_image_b64(row["image_url"])
    if not b64:
        og = _fetch_via_og(shortcode)
        if og and og["image_url"]:
            b64 = _download_image_b64(og["image_url"])
    if not b64:
        try:
            post = _fetch_post_with_fresh_loader(shortcode)
            if post.url:
                b64 = _download_image_b64(post.url)
        except Exception:
            pass

    if not b64:
        return "Could not fetch image", 502

    # Persist recovered image permanently
    try:
        con2 = get_db()
        cur2 = con2.cursor()
        cur2.execute(_q("UPDATE recipes SET image_data=? WHERE shortcode=?"),
                     (b64, shortcode))
        con2.commit()
        con2.close()
    except Exception:
        pass

    return Response(base64.b64decode(b64), mimetype="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000"})


def _recipe_json(r: dict) -> dict:
    """Prepare a recipe row for a JSON response: parse JSON fields and
    drop the (potentially huge) base64 image blob."""
    r.pop("image_data", None)
    r["ingredients"] = json.loads(r["ingredients"] or "[]")
    r["steps"]       = json.loads(r["steps"] or "[]")
    return r


@app.route("/api/recipes", methods=["GET"])
@login_required
def list_recipes():
    uid = current_user()["id"]
    con = get_db()
    cur = con.cursor()
    cur.execute(_q("SELECT * FROM recipes WHERE user_id=? ORDER BY added_at DESC"), (uid,))
    rows = _fetchall(cur)
    con.close()
    return jsonify([_recipe_json(r) for r in rows])


@app.route("/api/recipes", methods=["POST"])
@login_required
def add_recipe():
    con = None
    try:
        uid  = current_user()["id"]
        data = request.get_json() or {}
        url  = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "url is required"}), 400

        # Optional manual caption — skips Instagram scraping entirely
        manual_caption = data.get("caption", "").strip()

        con = get_db()
        cur = con.cursor()
        cur.execute(_q("SELECT * FROM recipes WHERE user_id=? AND url=?"), (uid, url))
        existing = _fetchone(cur)
        if existing:
            con.close()
            con = None
            return jsonify(_recipe_json(existing))

        if manual_caption:
            shortcode = ""
            try:
                shortcode = shortcode_from_url(url)
            except Exception:
                pass
            # Even with a manual caption, try to grab the image via OG tags
            image_url = image_data = author = ""
            if shortcode:
                og = _fetch_via_og(shortcode)
                if og:
                    image_url = og["image_url"]
                    author    = og["author"]
                    if image_url:
                        image_data = _download_image_b64(image_url)
            recipe = parse_recipe(manual_caption)
            info = {
                "shortcode":   shortcode,
                "url":         url,
                "author":      author,
                "raw_caption": manual_caption,
                "image_url":   image_url,
                "image_data":  image_data,
                "local_image": f"/api/image/{shortcode}" if shortcode else "",
                **recipe,
            }
        else:
            info = fetch_instagram_post(url)

        now = datetime.utcnow().isoformat()
        category = _normalize_category(info.get("category", ""))
        if _USE_PG:
            cur.execute("""
                INSERT INTO recipes
                  (user_id, url, shortcode, title, category, ingredients, steps,
                   raw_caption, image_url, image_data, local_image, author, added_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (uid, info["url"], info["shortcode"], info["title"], category,
                  json.dumps(info["ingredients"]), json.dumps(info["steps"]),
                  info["raw_caption"], info["image_url"], info["image_data"],
                  info["local_image"], info["author"], now))
            row_id = cur.fetchone()["id"]
        else:
            cur.execute("""
                INSERT INTO recipes
                  (user_id, url, shortcode, title, category, ingredients, steps,
                   raw_caption, image_url, image_data, local_image, author, added_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (uid, info["url"], info["shortcode"], info["title"], category,
                  json.dumps(info["ingredients"]), json.dumps(info["steps"]),
                  info["raw_caption"], info["image_url"], info["image_data"],
                  info["local_image"], info["author"], now))
            row_id = cur.lastrowid

        con.commit()
        cur.execute(_q("SELECT * FROM recipes WHERE id=?"), (row_id,))
        result = _fetchone(cur)
        con.close()
        con = None
        return jsonify(_recipe_json(result)), 201

    except Exception as e:
        import traceback
        traceback.print_exc()
        if con:
            try:
                con.rollback()
            except Exception:
                pass
            try:
                con.close()
            except Exception:
                pass
        msg = str(e)
        instagram_blocked = any(k in msg.lower() for k in
                                ("graphql", "metadata failed", "403", "429",
                                 "json", "nonetype", "subscriptable", "unexpected"))
        if instagram_blocked:
            msg = ("Instagram couldn't be reached right now. "
                   "You can paste the post caption manually instead.")
        return jsonify({"error": msg, "instagram_blocked": instagram_blocked}), 422


@app.route("/api/recipes/<int:recipe_id>", methods=["DELETE"])
@login_required
def delete_recipe(recipe_id):
    uid = current_user()["id"]
    con = get_db()
    cur = con.cursor()
    cur.execute(_q("DELETE FROM recipes WHERE id=? AND user_id=?"), (recipe_id, uid))
    con.commit()
    con.close()
    return jsonify({"ok": True})


@app.route("/api/recipes/<int:recipe_id>", methods=["PATCH"])
@login_required
def update_recipe(recipe_id):
    uid  = current_user()["id"]
    data = request.get_json() or {}
    con  = get_db()
    cur  = con.cursor()
    if "title" in data:
        cur.execute(_q("UPDATE recipes SET title=? WHERE id=? AND user_id=?"),
                    (data["title"], recipe_id, uid))
    if "category" in data:
        cur.execute(_q("UPDATE recipes SET category=? WHERE id=? AND user_id=?"),
                    (_normalize_category(data["category"]), recipe_id, uid))
    if "ingredients" in data:
        cur.execute(_q("UPDATE recipes SET ingredients=? WHERE id=? AND user_id=?"),
                    (json.dumps(data["ingredients"]), recipe_id, uid))
    if "steps" in data:
        cur.execute(_q("UPDATE recipes SET steps=? WHERE id=? AND user_id=?"),
                    (json.dumps(data["steps"]), recipe_id, uid))
    con.commit()
    cur.execute(_q("SELECT * FROM recipes WHERE id=?"), (recipe_id,))
    result = _fetchone(cur)
    con.close()
    return jsonify(_recipe_json(result))


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
