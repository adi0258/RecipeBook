// Recipe modal: ingredients/steps rendering, category select (PATCH on change).
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

  const categoryOptions = CATEGORIES.map(c =>
    `<option value="${esc(c)}" ${c === recipeCategory(r) ? 'selected' : ''}>${esc(c)}</option>`
  ).join('');

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
        <label class="category-row">
          <span class="category-label">קטגוריה</span>
          <select id="modal-category" class="category-select">${categoryOptions}</select>
        </label>
        ${recipeSections}
        ${rawHtml}
      </div>
    </div>`;

  modalContent.querySelector('#modal-category').addEventListener('change', async (e) => {
    try {
      const res = await fetch(`${API}/api/recipes/${r.id}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: e.target.value }),
      });
      if (res.ok) {
        const updated = await res.json();
        const idx = recipes.findIndex(x => x.id === r.id);
        if (idx !== -1) recipes[idx] = updated;
        r.category = updated.category;
        renderGrid();
      }
    } catch { /* keep previous value on failure */ }
  });

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
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeModal(); closePantry(); }
});
