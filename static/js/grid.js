// Recipe grid: category filter bar, title search, cards, add/delete recipes,
// manual-caption fallback when Instagram is blocked.
async function loadRecipes() {
  try {
    const res = await fetch(`${API}/api/recipes`, { credentials: 'include' });
    if (res.ok) recipes = await res.json();
  } catch {
    recipes = [];
  }
  renderGrid();
}

function renderFilterBar() {
  const cats = CATEGORIES.filter(c => recipes.some(r => recipeCategory(r) === c));
  filterBar.hidden = recipes.length === 0 || cats.length < 2;
  if (filterBar.hidden) { currentFilter = 'all'; filterBar.innerHTML = ''; return; }

  // Reset filter if its category disappeared (e.g. last recipe deleted)
  if (currentFilter !== 'all' && !cats.includes(currentFilter)) currentFilter = 'all';

  const chips = [
    { key: 'all', label: 'הכל' },
    ...cats.map(c => ({ key: c, label: c })),
  ];
  filterBar.innerHTML = chips.map(c => `
    <button class="filter-chip ${currentFilter === c.key ? 'active' : ''}" data-cat="${esc(c.key)}">
      ${esc(c.label)}
    </button>`).join('');

  filterBar.querySelectorAll('.filter-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      currentFilter = btn.dataset.cat;
      renderGrid();
    });
  });
}

function renderGrid() {
  renderFilterBar();
  grid.innerHTML = '';
  emptyState.hidden = recipes.length > 0;
  toolbar.hidden = recipes.length === 0;

  let visible = currentFilter === 'all'
    ? recipes
    : recipes.filter(r => recipeCategory(r) === currentFilter);

  const q = searchQuery.toLowerCase();
  if (q) visible = visible.filter(r => (r.title || '').toLowerCase().includes(q));

  visible.forEach(r => {
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
        <span class="card-category">${esc(recipeCategory(r))}</span>
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

// ── Search ────────────────────────────────────────────────────────────────────
searchInput.addEventListener('input', () => {
  searchQuery = searchInput.value.trim();
  renderGrid();
});

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

function setLoading(on) {
  addBtn.disabled  = on;
  btnLabel.hidden  = on;
  btnSpinner.hidden= !on;
  if (on) btnSpinner.classList.add('spinning');
  else    btnSpinner.classList.remove('spinning');
}

// ── Manual caption fallback ───────────────────────────────────────────────────
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

// ── Delete ────────────────────────────────────────────────────────────────────
async function deleteRecipe(id) {
  await fetch(`${API}/api/recipes/${id}`, { method: 'DELETE', credentials: 'include' });
  recipes = recipes.filter(r => r.id !== id);
  renderGrid();
  if (overlay.dataset.openId == id) closeModal();
}
