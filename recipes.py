"""Recipe API routes: list / add / delete / update + thumbnail proxy.

Public API: recipes_bp (blueprint). All routes are user-scoped via login_required.
Thumbnails are served from base64 stored in the DB (never expires); old rows
without stored bytes are recovered from Instagram and persisted on first view.
"""
import json
import re
import base64
from datetime import datetime
from flask import Blueprint, Response, jsonify, request

from db import get_db, q, fetchone, fetchall, USE_PG
from auth import current_user, login_required
from parsing import parse_recipe, normalize_category, shortcode_from_url
from instagram import fetch_instagram_post, fetch_via_og, download_image_b64, \
    fetch_post_with_fresh_loader

recipes_bp = Blueprint("recipes", __name__)


def _recipe_json(r: dict) -> dict:
    """Prepare a recipe row for a JSON response: parse JSON fields and
    drop the (potentially huge) base64 image blob."""
    r.pop("image_data", None)
    r["ingredients"] = json.loads(r["ingredients"] or "[]")
    r["steps"]       = json.loads(r["steps"] or "[]")
    return r


@recipes_bp.route("/api/image/<shortcode>")
def serve_image(shortcode):
    if not re.match(r'^[A-Za-z0-9_-]{1,50}$', shortcode):
        return "Not found", 404

    con = get_db()
    cur = con.cursor()
    cur.execute(q("SELECT id, image_url, image_data FROM recipes WHERE shortcode=? LIMIT 1"),
                (shortcode,))
    row = fetchone(cur)
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

    # 2. Old recipe without stored bytes — recover the image and persist it
    b64 = ""
    if row.get("image_url"):
        b64 = download_image_b64(row["image_url"])
    if not b64:
        og = fetch_via_og(shortcode)
        if og and og["image_url"]:
            b64 = download_image_b64(og["image_url"])
    if not b64:
        try:
            post = fetch_post_with_fresh_loader(shortcode)
            if post.url:
                b64 = download_image_b64(post.url)
        except Exception:
            pass

    if not b64:
        return "Could not fetch image", 502

    try:
        con2 = get_db()
        cur2 = con2.cursor()
        cur2.execute(q("UPDATE recipes SET image_data=? WHERE shortcode=?"),
                     (b64, shortcode))
        con2.commit()
        con2.close()
    except Exception:
        pass

    return Response(base64.b64decode(b64), mimetype="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000"})


@recipes_bp.route("/api/recipes", methods=["GET"])
@login_required
def list_recipes():
    uid = current_user()["id"]
    con = get_db()
    cur = con.cursor()
    cur.execute(q("SELECT * FROM recipes WHERE user_id=? ORDER BY added_at DESC"), (uid,))
    rows = fetchall(cur)
    con.close()
    return jsonify([_recipe_json(r) for r in rows])


@recipes_bp.route("/api/recipes", methods=["POST"])
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
        cur.execute(q("SELECT * FROM recipes WHERE user_id=? AND url=?"), (uid, url))
        existing = fetchone(cur)
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
                og = fetch_via_og(shortcode)
                if og:
                    image_url = og["image_url"]
                    author    = og["author"]
                    if image_url:
                        image_data = download_image_b64(image_url)
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
        category = normalize_category(info.get("category", ""))
        if USE_PG:
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
        cur.execute(q("SELECT * FROM recipes WHERE id=?"), (row_id,))
        result = fetchone(cur)
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


@recipes_bp.route("/api/recipes/<int:recipe_id>", methods=["DELETE"])
@login_required
def delete_recipe(recipe_id):
    uid = current_user()["id"]
    con = get_db()
    cur = con.cursor()
    cur.execute(q("DELETE FROM recipes WHERE id=? AND user_id=?"), (recipe_id, uid))
    con.commit()
    con.close()
    return jsonify({"ok": True})


@recipes_bp.route("/api/recipes/<int:recipe_id>", methods=["PATCH"])
@login_required
def update_recipe(recipe_id):
    uid  = current_user()["id"]
    data = request.get_json() or {}
    con  = get_db()
    cur  = con.cursor()
    if "title" in data:
        cur.execute(q("UPDATE recipes SET title=? WHERE id=? AND user_id=?"),
                    (data["title"], recipe_id, uid))
    if "category" in data:
        cur.execute(q("UPDATE recipes SET category=? WHERE id=? AND user_id=?"),
                    (normalize_category(data["category"]), recipe_id, uid))
    if "ingredients" in data:
        cur.execute(q("UPDATE recipes SET ingredients=? WHERE id=? AND user_id=?"),
                    (json.dumps(data["ingredients"]), recipe_id, uid))
    if "steps" in data:
        cur.execute(q("UPDATE recipes SET steps=? WHERE id=? AND user_id=?"),
                    (json.dumps(data["steps"]), recipe_id, uid))
    con.commit()
    cur.execute(q("SELECT * FROM recipes WHERE id=?"), (recipe_id,))
    result = fetchone(cur)
    con.close()
    return jsonify(_recipe_json(result))
