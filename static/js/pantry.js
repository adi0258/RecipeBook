// Pantry matcher: "what do I have at home?" — ranks recipes by how many of
// their ingredients match the user's pantry list. Input persists in localStorage.
function openPantry() {
  pantryOverlay.hidden = false;
  document.body.style.overflow = 'hidden';
  pantryInput.value = localStorage.getItem('pantry') || '';
  if (pantryInput.value) runPantrySearch();
  pantryInput.focus();
}

function closePantry() {
  pantryOverlay.hidden = true;
  document.body.style.overflow = '';
}

function parsePantryItems(text) {
  return text
    .split(/[,\n]/)
    .map(s => s.trim().toLowerCase())
    .filter(s => s.length >= 2);
}

function runPantrySearch() {
  const items = parsePantryItems(pantryInput.value);
  localStorage.setItem('pantry', pantryInput.value);

  if (!items.length) {
    pantryResults.innerHTML = '<p class="pantry-empty">הזינו לפחות מצרך אחד 🙂</p>';
    return;
  }

  const scored = recipes
    .map(r => {
      const lines = (r.ingredients || []).filter(i => !i.startsWith('__section__'));
      if (!lines.length) return null;
      const matched = lines.filter(line =>
        items.some(item => line.toLowerCase().includes(item))
      ).length;
      return { r, matched, total: lines.length, pct: Math.round((matched / lines.length) * 100) };
    })
    .filter(x => x && x.matched > 0)
    .sort((a, b) => b.pct - a.pct || b.matched - a.matched);

  if (!scored.length) {
    pantryResults.innerHTML = '<p class="pantry-empty">לא נמצאו מתכונים שמתאימים למצרכים האלה 😕</p>';
    return;
  }

  pantryResults.innerHTML = `
    <p class="pantry-results-title">המתכונים הקרובים ביותר:</p>
    ${scored.map(x => `
      <button class="pantry-result" data-id="${x.r.id}">
        <span class="pantry-result-info">
          <span class="pantry-result-name" dir="auto">${esc(x.r.title)}</span>
          <span class="pantry-result-count">${x.matched} מתוך ${x.total} מצרכים</span>
        </span>
        <span class="pantry-result-pct ${x.pct >= 70 ? 'high' : x.pct >= 40 ? 'mid' : ''}">${x.pct}%</span>
        <span class="pantry-result-bar"><span style="width:${x.pct}%"></span></span>
      </button>`).join('')}
  `;

  pantryResults.querySelectorAll('.pantry-result').forEach(btn => {
    btn.addEventListener('click', () => {
      const recipe = recipes.find(r => r.id == btn.dataset.id);
      if (recipe) {
        closePantry();
        openModal(recipe);
      }
    });
  });
}

pantryBtn.addEventListener('click', openPantry);
pantryClose.addEventListener('click', closePantry);
pantrySearch.addEventListener('click', runPantrySearch);
pantryOverlay.addEventListener('click', (e) => { if (e.target === pantryOverlay) closePantry(); });
pantryInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) runPantrySearch();
});
