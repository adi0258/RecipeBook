"""Flask entrypoint — creates the app and wires up the modules.

Module map: db.py (drivers/schema), auth.py (OAuth/session),
parsing.py (GPT/regex caption parsing), instagram.py (post fetching),
recipes.py (API routes). Vercel imports `app` via api/index.py.
"""
import os
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from db import init_db
from auth import auth_bp, oauth
from recipes import recipes_bp

# Resolve paths relative to this file so they work when imported by Vercel
_HERE = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(_HERE, "static"),
    template_folder=os.path.join(_HERE, "templates"),
)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
CORS(app, supports_credentials=True)

oauth.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(recipes_bp)


# Always return JSON on errors — the frontend can't parse HTML 500 pages
@app.errorhandler(Exception)
def _handle_any_exception(e):
    import traceback
    traceback.print_exc()
    return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    return send_from_directory(os.path.join(_HERE, "templates"), "index.html")


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
