import os
import re
import sqlite3
import json
import instaloader
import urllib.request
from flask import (
    Flask, request, jsonify, send_from_directory,
    session, redirect, url_for,
)
from flask_cors import CORS
from authlib.integrations.flask_client import OAuth
from datetime import datetime
from functools import wraps

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
CORS(app, supports_credentials=True)

DB_PATH    = os.path.join(os.path.dirname(__file__), "recipes.db")
IMAGES_DIR = os.path.join(os.path.dirname(__file__), "static", "images")
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
loader = instaloader.Instaloader(
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    quiet=True,
)


# ── DB ────────────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
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
            local_image  TEXT,
            author       TEXT,
            added_at     TEXT,
            UNIQUE(user_id, url)
        );
    """)
    con.commit()
    con.close()


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def upsert_user(google_id, email, name, picture):
    now = datetime.utcnow().isoformat()
    con = get_db()
    con.execute(
        """INSERT INTO users (google_id, email, name, picture, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(google_id) DO UPDATE SET
             email=excluded.email, name=excluded.name, picture=excluded.picture""",
        (google_id, email, name, picture),
    )
    con.commit()
    row = con.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
    con.close()
    return dict(row)


# ── Auth helpers ──────────────────────────────────────────────────────────────
def current_user():
    return session.get("user")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "Not authenticated"}), 401
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


@app.route("/auth/me")
def auth_me():
    user = current_user()
    if not user:
        return jsonify(None)
    return jsonify({
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "picture": user["picture"],
    })


# ── Recipe parsing ────────────────────────────────────────────────────────────
def parse_recipe(caption: str) -> dict:
    if not caption:
        return {"title": "Untitled Recipe", "ingredients": [], "steps": []}

    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    title = lines[0] if lines else "Untitled Recipe"
    title = re.sub(r'^[\U00010000-\U0010ffff☀-⛿✀-➿\s]+', '', title).strip() or lines[0]

    ingredient_headers = re.compile(
        r'(ingredient|what you.?ll need|you.?ll need|needs|for the|materials)', re.I)
    step_headers = re.compile(
        r'(instruction|direction|method|how to|steps?|preparation|let.?s make|make it|procedure)', re.I)

    ingredients: list[str] = []
    steps: list[str] = []
    mode = None

    for line in lines[1:]:
        clean = re.sub(r'#\S+', '', line).strip()
        if not clean or all(w.startswith('#') for w in clean.split()):
            continue

        if ingredient_headers.search(clean):
            mode = 'ingredients'
            continue
        if step_headers.search(clean):
            mode = 'steps'
            continue

        if re.match(r'^\d+[\.\)]\s', clean):
            mode = 'steps'
            steps.append(re.sub(r'^\d+[\.\)]\s*', '', clean))
            continue

        bullet = re.match(r'^[-•✔✅🔸🔹▶️➡️➤*]\s*', clean)
        if bullet:
            clean = clean[bullet.end():]

        if mode == 'ingredients':
            ingredients.append(clean)
        elif mode == 'steps':
            steps.append(clean)

    if not ingredients and not steps:
        steps = [re.sub(r'#\S+', '', l).strip() for l in lines[1:] if l.strip()]

    return {"title": title, "ingredients": ingredients, "steps": steps}


def shortcode_from_url(url: str) -> str:
    m = re.search(r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    if not m:
        raise ValueError("Could not parse Instagram shortcode from URL")
    return m.group(1)


def fetch_instagram_post(url: str) -> dict:
    shortcode = shortcode_from_url(url)
    try:
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch post: {e}")

    caption   = post.caption or ""
    image_url = post.url

    local_image = None
    try:
        filename = f"{shortcode}.jpg"
        dest = os.path.join(IMAGES_DIR, filename)
        if not os.path.exists(dest):
            urllib.request.urlretrieve(image_url, dest)
        local_image = f"/static/images/{filename}"
    except Exception:
        pass

    recipe = parse_recipe(caption)
    return {
        "shortcode": shortcode,
        "url": url,
        "author": post.owner_username,
        "raw_caption": caption,
        "image_url": image_url,
        "local_image": local_image,
        **recipe,
    }


# ── Recipe routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/api/recipes", methods=["GET"])
@login_required
def list_recipes():
    uid = current_user()["id"]
    con = get_db()
    rows = con.execute(
        "SELECT * FROM recipes WHERE user_id=? ORDER BY added_at DESC", (uid,)
    ).fetchall()
    con.close()
    return jsonify([
        {**dict(r), "ingredients": json.loads(r["ingredients"] or "[]"),
         "steps": json.loads(r["steps"] or "[]")}
        for r in rows
    ])


@app.route("/api/recipes", methods=["POST"])
@login_required
def add_recipe():
    uid  = current_user()["id"]
    data = request.get_json() or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400

    con = get_db()
    existing = con.execute(
        "SELECT * FROM recipes WHERE user_id=? AND url=?", (uid, url)
    ).fetchone()
    if existing:
        con.close()
        row = dict(existing)
        row["ingredients"] = json.loads(row["ingredients"] or "[]")
        row["steps"]       = json.loads(row["steps"] or "[]")
        return jsonify(row)

    try:
        info = fetch_instagram_post(url)
    except Exception as e:
        con.close()
        return jsonify({"error": str(e)}), 422

    now = datetime.utcnow().isoformat()
    con.execute(
        """INSERT INTO recipes
           (user_id, url, shortcode, title, ingredients, steps, raw_caption,
            image_url, local_image, author, added_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, info["url"], info["shortcode"], info["title"],
         json.dumps(info["ingredients"]), json.dumps(info["steps"]),
         info["raw_caption"], info["image_url"], info["local_image"],
         info["author"], now),
    )
    con.commit()
    row_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    row    = con.execute("SELECT * FROM recipes WHERE id=?", (row_id,)).fetchone()
    con.close()
    result = dict(row)
    result["ingredients"] = json.loads(result["ingredients"] or "[]")
    result["steps"]       = json.loads(result["steps"] or "[]")
    return jsonify(result), 201


@app.route("/api/recipes/<int:recipe_id>", methods=["DELETE"])
@login_required
def delete_recipe(recipe_id):
    uid = current_user()["id"]
    con = get_db()
    con.execute("DELETE FROM recipes WHERE id=? AND user_id=?", (recipe_id, uid))
    con.commit()
    con.close()
    return jsonify({"ok": True})


@app.route("/api/recipes/<int:recipe_id>", methods=["PATCH"])
@login_required
def update_recipe(recipe_id):
    uid  = current_user()["id"]
    data = request.get_json() or {}
    con  = get_db()
    if "title" in data:
        con.execute("UPDATE recipes SET title=? WHERE id=? AND user_id=?",
                    (data["title"], recipe_id, uid))
    if "ingredients" in data:
        con.execute("UPDATE recipes SET ingredients=? WHERE id=? AND user_id=?",
                    (json.dumps(data["ingredients"]), recipe_id, uid))
    if "steps" in data:
        con.execute("UPDATE recipes SET steps=? WHERE id=? AND user_id=?",
                    (json.dumps(data["steps"]), recipe_id, uid))
    con.commit()
    row = con.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
    con.close()
    result = dict(row)
    result["ingredients"] = json.loads(result["ingredients"] or "[]")
    result["steps"]       = json.loads(result["steps"] or "[]")
    return jsonify(result)


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
