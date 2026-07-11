"""Database layer: PostgreSQL (Neon) when DATABASE_URL is set, SQLite locally.

Public API: USE_PG, get_db(), q(), fetchone(), fetchall(), init_db(), upsert_user().
q() converts '?' placeholders to '%s' for PostgreSQL.
"""
import os
import sqlite3
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = bool(DATABASE_URL)

if USE_PG:
    import psycopg2
    import psycopg2.extras
    from urllib.parse import urlparse, urlencode, parse_qs, urlunparse


def _clean_pg_url(url: str) -> str:
    """Strip connection-string params psycopg2 doesn't understand (e.g. channel_binding)."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    for key in ("channel_binding",):
        params.pop(key, None)
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def get_db():
    if USE_PG:
        con = psycopg2.connect(
            _clean_pg_url(DATABASE_URL),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        con.autocommit = False
        return con
    con = sqlite3.connect(os.environ.get("DB_PATH", "/tmp/recipes.db"))
    con.row_factory = sqlite3.Row
    return con


def q(sql: str) -> str:
    """Convert ? placeholders to %s for PostgreSQL."""
    if USE_PG:
        return sql.replace("?", "%s")
    return sql


def fetchone(cur):
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def fetchall(cur):
    return [dict(r) for r in cur.fetchall()]


def init_db():
    con = get_db()
    cur = con.cursor()
    if USE_PG:
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
                category    TEXT,
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
                category     TEXT,
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
    cur.execute(q("""
        INSERT INTO users (google_id, email, name, picture, created_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(google_id) DO UPDATE SET
          email=excluded.email, name=excluded.name, picture=excluded.picture
    """), (google_id, email, name, picture, datetime.utcnow().isoformat()))
    con.commit()
    cur.execute(q("SELECT * FROM users WHERE google_id=?"), (google_id,))
    row = fetchone(cur)
    con.close()
    return row
