// Entry point. Loaded LAST — all other js/ files must be loaded before it.

const PENDING_KEY = 'pendingSharedUrl';

// Opened from the iOS share-sheet Shortcut: /?url=<instagram link or shared text>
function consumeSharedUrl() {
  const params = new URLSearchParams(location.search);
  const raw = params.get('url') || params.get('text') || '';
  if (raw) {
    history.replaceState(null, '', location.pathname);
    const match = raw.match(/https?:\/\/[^\s]+/);
    if (match) {
      try { localStorage.setItem(PENDING_KEY, match[0]); } catch {}
      return match[0];
    }
  }
  try { return localStorage.getItem(PENDING_KEY) || ''; } catch { return ''; }
}

function clearPendingUrl() {
  try { localStorage.removeItem(PENDING_KEY); } catch {}
}

async function init() {
  const sharedUrl = consumeSharedUrl();
  await loadUser();
  if (currentUser) {
    await loadRecipes();
    if (sharedUrl) {
      clearPendingUrl();
      urlInput.value = sharedUrl;
      form.requestSubmit();
    }
  }
}

init();
