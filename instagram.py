"""Instagram fetching: OG meta tags (crawler UA) primary, instaloader fallback.

Public API: fetch_instagram_post(url), fetch_via_og(shortcode),
download_image_b64(url), fetch_post_with_fresh_loader(shortcode).

Instagram blocks its internal API for datacenter IPs, but always serves
Open Graph tags (caption ~1300 chars + image) to link-preview crawlers.
Images are stored base64 in the DB because CDN URLs expire within days.
"""
import re
import base64
import html as html_lib
import urllib.request
import instaloader

from parsing import parse_recipe, shortcode_from_url

_CRAWLER_HEADERS = {
    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Accept-Language": "en",
}

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


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


def fetch_post_with_fresh_loader(shortcode: str):
    """Fetch with a brand-new loader context each time — a stale context can
    cause NoneType errors after Instagram returns an unexpected response."""
    fresh = _make_loader()
    return instaloader.Post.from_shortcode(fresh.context, shortcode)


def _http_get(url: str, timeout: int = 20, headers: dict = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or _BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_via_og(shortcode: str) -> dict | None:
    """Fetch the post page as a link-preview crawler and read the Open Graph
    meta tags. Returns {caption, image_url, author} or None."""
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


def download_image_b64(image_url: str) -> str:
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
    og = fetch_via_og(shortcode)
    if og:
        caption   = og["caption"]
        image_url = og["image_url"]
        author    = og["author"]

    # Strategy 2: instaloader (full caption, but often blocked on Vercel)
    if not caption:
        try:
            post = fetch_post_with_fresh_loader(shortcode)
            caption   = post.caption or caption
            image_url = post.url or image_url
            author    = getattr(post, "owner_username", "") or author
        except Exception as e:
            if not og:
                msg = str(e) or "Instagram returned an unexpected response."
                raise RuntimeError(msg)

    # Persist the actual image bytes so it never expires
    image_data = download_image_b64(image_url) if image_url else ""

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
