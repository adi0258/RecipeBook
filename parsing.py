"""Recipe caption parsing: GPT-4o-mini primary, regex/heuristic fallback.

Public API: CATEGORIES, normalize_category(), parse_recipe(), shortcode_from_url().
parse_recipe() returns {title, category, ingredients, steps}; sub-headers inside
ingredients/steps are marked with a '__section__' prefix.
"""
import os
import re
import json
from openai import OpenAI

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


def normalize_category(cat: str) -> str:
    cat = (cat or "").strip()
    return cat if cat in CATEGORIES else "אחר"


def shortcode_from_url(url: str) -> str:
    m = re.search(r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    if not m:
        raise ValueError("Could not parse Instagram shortcode from URL")
    return m.group(1)


_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            _openai_client = OpenAI(api_key=api_key)
    return _openai_client


_SYSTEM_PROMPT = """You are a recipe parser. Given an Instagram post caption (which may be in any language), extract the recipe and return ONLY valid JSON — no markdown, no explanation.

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


def _parse_with_gpt(caption: str) -> dict | None:
    """Returns None if the API key is missing or the call fails."""
    client = _get_openai()
    if not client:
        return None
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": caption},
            ],
            temperature=0,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return {
            "title":       data.get("title", "Untitled Recipe"),
            "category":    normalize_category(data.get("category", "")),
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
    return _parse_with_gpt(caption) or _parse_with_regex(caption)
