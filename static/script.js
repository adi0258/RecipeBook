const API = '';

let recipes = [];
let currentUser = null;

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
const emptyState     = document.getElementById('empty-state');
const overlay        = document.getElementById('modal-overlay');
const modalClose     = document.getElementById('modal-close');
const modalContent   = document.getElementById('modal-content');

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  await loadUser();
  if (currentUser) {
    await loadRecipes();
  }
}

async function loadUser() {
  try {
    const res = await fetch(`${API}/auth/me`, { credentials: 'include' });
    currentUser = await res.json();
  } catch {
    currentUser = null;
  }
  renderAuthArea();
  renderAppShell();
}

async function loadRecipes() {
  try {
    const res = await fetch(`${API}/api/recipes`, { credentials: 'include' });
    if (res.ok) recipes = await res.json();
  } catch {
    recipes = [];
  }
  renderGrid();
}

// ── Auth UI ───────────────────────────────────────────────────────────────────
function renderAuthArea() {
  if (!currentUser) {
    authArea.innerHTML = `
      <a class="header-login-btn" href="/auth/login">
        <img src="/static/google-icon.svg" width="18" height="18" alt="" />
        Sign in with Google
      </a>`;
    return;
  }
  authArea.innerHTML = `
    <div class="user-chip">
      ${currentUser.picture
        ? `<img src="${esc(currentUser.picture)}" alt="${esc(currentUser.name)}" />`
        : ''}
      <span class="user-name">${esc(currentUser.name || currentUser.email)}</span>
    </div>
    <a class="logout-btn" href="/auth/logout">Sign out</a>`;
}

function renderAppShell() {
  const loggedIn = !!currentUser;
  loggedOutBanner.hidden = loggedIn;
  addSection.hidden      = !loggedIn;
  if (!loggedIn) {
    emptyState.hidden = true;
    grid.innerHTML    = '';
  }
}

// ── Render grid ───────────────────────────────────────────────────────────────
function renderGrid() {
  grid.innerHTML = '';
  emptyState.hidden = recipes.length > 0;

  recipes.forEach(r => {
    const card = document.createElement('div');
    card.className = 'card';
    card.dataset.id = r.id;

    const imgHtml = r.local_image
      ? `<img class="card-thumb" src="${r.local_image}" alt="${esc(r.title)}" loading="lazy" />`
      : `<div class="card-thumb-placeholder">🍽️</div>`;

    const dir = recipeDir(r);
    card.innerHTML = `
      ${imgHtml}
      <div class="card-body" dir="${dir}">
        <div class="card-title">${esc(r.title)}</div>
        <div class="card-meta" dir="ltr">by @${esc(r.author || '—')}</div>
        <div class="card-counts" dir="ltr">
          ${r.ingredients.length} ingredients · ${r.steps.length} steps
        </div>
        <button class="card-delete" data-id="${r.id}" title="Remove recipe">✕ Remove</button>
      </div>`;

    card.addEventListener('click', (e) => {
      if (e.target.closest('.card-delete')) return;
      openModal(r);
    });
    card.querySelector('.card-delete').addEventListener('click', (e) => {
      e.stopPropagation();
      deleteRecipe(r.id);
    });

    grid.appendChild(card);
  });
}

// ── Add recipe ────────────────────────────────────────────────────────────────
let _pendingUrl = '';   // URL saved while waiting for manual caption

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;

  hideManualFallback();
  setLoading(true);
  addError.hidden = true;

  try {
    const data = await submitRecipe(url);
    const exists = recipes.find(r => r.id === data.id);
    if (!exists) recipes.unshift(data);
    renderGrid();
    urlInput.value = '';
    openModal(data);
  } catch (err) {
    if (err.instagram_blocked) {
      _pendingUrl = url;
      showManualFallback(err.message);
    } else {
      addError.textContent = err.message;
      addError.hidden = false;
    }
  } finally {
    setLoading(false);
  }
});

async function submitRecipe(url, caption = null) {
  const body = { url };
  if (caption) body.caption = caption;
  const res = await fetch(`${API}/api/recipes`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    const err = new Error(data.error || 'Something went wrong');
    err.instagram_blocked = data.instagram_blocked || false;
    throw err;
  }
  return data;
}

// ── Manual caption fallback ───────────────────────────────────────────────────
const manualFallback = document.getElementById('manual-fallback');
const manualCaption  = document.getElementById('manual-caption');
const manualSubmit   = document.getElementById('manual-submit');
const manualCancel   = document.getElementById('manual-cancel');

function showManualFallback(errorMsg) {
  addError.textContent = errorMsg;
  addError.hidden = false;
  manualFallback.hidden = false;
  manualCaption.focus();
}

function hideManualFallback() {
  manualFallback.hidden = true;
  manualCaption.value = '';
  _pendingUrl = '';
}

manualCancel.addEventListener('click', () => {
  hideManualFallback();
  addError.hidden = true;
});

manualSubmit.addEventListener('click', async () => {
  const caption = manualCaption.value.trim();
  if (!caption || !_pendingUrl) return;

  setLoading(true);
  addError.hidden = true;

  try {
    const data = await submitRecipe(_pendingUrl, caption);
    const exists = recipes.find(r => r.id === data.id);
    if (!exists) recipes.unshift(data);
    renderGrid();
    urlInput.value = '';
    hideManualFallback();
    openModal(data);
  } catch (err) {
    addError.textContent = err.message;
    addError.hidden = false;
  } finally {
    setLoading(false);
  }
});

function setLoading(on) {
  addBtn.disabled  = on;
  btnLabel.hidden  = on;
  btnSpinner.hidden= !on;
  if (on) btnSpinner.classList.add('spinning');
  else    btnSpinner.classList.remove('spinning');
}

// ── Delete ────────────────────────────────────────────────────────────────────
async function deleteRecipe(id) {
  await fetch(`${API}/api/recipes/${id}`, { method: 'DELETE', credentials: 'include' });
  recipes = recipes.filter(r => r.id !== id);
  renderGrid();
  if (overlay.dataset.openId == id) closeModal();
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function renderIngredients(items, dir = 'ltr') {
  if (!items.length) return '';
  const rows = items.map(i => {
    if (i.startsWith('__section__')) {
      return `<li class="ing-subsection">${esc(i.replace('__section__', ''))}</li>`;
    }
    return `<li>${esc(i)}</li>`;
  }).join('');
  return `
    <div class="recipe-section">
      <p class="modal-section-title">🧂 Ingredients</p>
      <ul class="ingredients-list" dir="${dir}">${rows}</ul>
    </div>`;
}

function renderSteps(items, dir = 'ltr') {
  if (!items.length) return '';
  let counter = 0;
  const rows = items.map(s => {
    if (s.startsWith('__section__')) {
      return `<li class="step-subsection">${esc(s.replace('__section__', ''))}</li>`;
    }
    counter++;
    return `<li><span class="step-num">${counter}</span><span>${esc(s)}</span></li>`;
  }).join('');
  return `
    <div class="recipe-section">
      <p class="modal-section-title">👨‍🍳 Instructions</p>
      <ol class="steps-list" dir="${dir}">${rows}</ol>
    </div>`;
}

function openModal(r) {
  overlay.dataset.openId = r.id;

  const dir = recipeDir(r);

  const imgHtml = r.local_image
    ? `<img src="${r.local_image}" alt="${esc(r.title)}" />`
    : `<div class="modal-img-placeholder">🍽️</div>`;

  const hasIngredients = r.ingredients.length > 0;
  const hasSteps       = r.steps.length > 0;

  const rawHtml = r.raw_caption
    ? `<details>
         <summary>View original caption</summary>
         <pre class="raw-caption" dir="${dir}">${esc(r.raw_caption)}</pre>
       </details>`
    : '';

  // Side-by-side when both sections exist, otherwise full-width
  const recipeSections = (hasIngredients && hasSteps)
    ? `<div class="recipe-two-col">
         ${renderIngredients(r.ingredients, dir)}
         ${renderSteps(r.steps, dir)}
       </div>`
    : `${renderIngredients(r.ingredients, dir)}${renderSteps(r.steps, dir)}`;

  modalContent.innerHTML = `
    <div class="modal-inner">
      <div class="modal-image">${imgHtml}</div>
      <div class="modal-details">
        <h2 class="modal-title" dir="${dir}">${esc(r.title)}</h2>
        <p class="modal-author" dir="ltr">
          by <a href="https://www.instagram.com/${esc(r.author || '')}" target="_blank" rel="noopener">
            @${esc(r.author || '—')}
          </a>
          &nbsp;·&nbsp;
          <a href="${esc(r.url)}" target="_blank" rel="noopener">View post ↗</a>
        </p>
        ${recipeSections}
        ${rawHtml}
      </div>
    </div>`;

  overlay.hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  overlay.hidden = true;
  document.body.style.overflow = '';
  modalContent.innerHTML = '';
}

modalClose.addEventListener('click', closeModal);
overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

// ── Utility ───────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/** Returns true when the string contains Hebrew (or Arabic) characters. */
function isRTL(str) {
  return /[֐-׿؀-ۿ]/.test(str ?? '');
}

/**
 * Returns the best dir attribute value for a block of text.
 * Uses "rtl" for Hebrew/Arabic, "ltr" for everything else.
 * Pass a recipe object to check title + first ingredient/step together.
 */
function recipeDir(r) {
  const probe = [r.title, ...(r.ingredients || []), ...(r.steps || [])].join(' ');
  return isRTL(probe) ? 'rtl' : 'ltr';
}

init();
