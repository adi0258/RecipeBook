"""Auth: Google OAuth + guest mode, session management.

Public API: auth_bp (blueprint), oauth (call oauth.init_app(app)),
current_user(), login_required decorator.
login_required also re-syncs session["user"] with the DB row by google_id,
which protects against stale ids after a DB migration.
"""
import os
import uuid
from functools import wraps
from flask import Blueprint, jsonify, redirect, session, url_for
from authlib.integrations.flask_client import OAuth

from db import get_db, q, fetchone, upsert_user

auth_bp = Blueprint("auth", __name__)

oauth = OAuth()
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def current_user():
    return session.get("user")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "Not authenticated"}), 401

        # Re-verify the user exists in the current DB and refresh the session id
        google_id = user.get("google_id")
        if google_id:
            try:
                con = get_db()
                cur = con.cursor()
                cur.execute(q("SELECT * FROM users WHERE google_id=?"), (google_id,))
                db_user = fetchone(cur)
                con.close()
                if db_user:
                    session["user"] = db_user
                else:
                    # User missing from DB (new/cleared DB) — re-create them
                    session["user"] = upsert_user(
                        google_id, user.get("email"),
                        user.get("name"), user.get("picture"),
                    )
            except Exception:
                pass  # fall through using whatever is in the session

        return f(*args, **kwargs)
    return decorated


@auth_bp.route("/auth/login")
def auth_login():
    redirect_uri = url_for("auth.auth_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo") or google.userinfo()
    session["user"] = upsert_user(
        google_id=userinfo["sub"],
        email=userinfo.get("email"),
        name=userinfo.get("name"),
        picture=userinfo.get("picture"),
    )
    return redirect("/")


@auth_bp.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect("/")


@auth_bp.route("/auth/guest")
def auth_guest():
    guest_id = f"guest_{uuid.uuid4().hex[:16]}"
    session["user"] = upsert_user(
        google_id=guest_id, email=None, name="Guest", picture=None,
    )
    return redirect("/")


@auth_bp.route("/auth/me")
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
