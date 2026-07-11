// Shared state, DOM refs, and small helpers. Loaded FIRST.
const API = '';

const CATEGORIES = ['אפייה', 'קינוח', 'בישול יומיומי', 'נשנוש ביניים', 'סלטים', 'מרקים', 'משקאות', 'אחר'];

let recipes = [];
let currentUser = null;
let currentFilter = 'all';
let searchQuery = '';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const authArea       = document.getElementById('auth-area');
const loggedOutBanner= document.getElementById('logged-out-banner');
const addSection     = document.getElementById('add-section');
const form           = document.getElementById('add-form');
const urlInput       = document.getElementById('url-input');
const addBtn         = document.getElementById('add-btn');
const addError       = document.getElementById('add-error');
const btnLabel       = addBtn.querySelector('.btn-label');
const btnSpinner     = addBtn.querySelector('.btn-spinner');
const grid           = document.getElementById('grid');
const filterBar      = document.getElementById('filter-bar');
const toolbar        = document.getElementById('toolbar');
const searchInput    = document.getElementById('search-input');
const pantryBtn      = document.getElementById('pantry-btn');
const pantryOverlay  = document.getElementById('pantry-overlay');
const pantryClose    = document.getElementById('pantry-close');
const pantryInput    = document.getElementById('pantry-input');
const pantrySearch   = document.getElementById('pantry-search');
const pantryResults  = document.getElementById('pantry-results');
const emptyState     = document.getElementById('empty-state');
const overlay        = document.getElementById('modal-overlay');
const modalClose     = document.getElementById('modal-close');
const modalContent   = document.getElementById('modal-content');
const manualFallback = document.getElementById('manual-fallback');
const manualCaption  = document.getElementById('manual-caption');
const manualSubmit   = document.getElementById('manual-submit');
const manualCancel   = document.getElementById('manual-cancel');

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/** True when the string contains Hebrew (or Arabic) characters. */
function isRTL(str) {
  return /[֐-׿؀-ۿ]/.test(str ?? '');
}

/** "rtl" for Hebrew/Arabic recipes, "ltr" otherwise. */
function recipeDir(r) {
  const probe = [r.title, ...(r.ingredients || []), ...(r.steps || [])].join(' ');
  return isRTL(probe) ? 'rtl' : 'ltr';
}

function recipeCategory(r) {
  return r.category || 'אחר';
}
