// Entry point. Loaded LAST — all other js/ files must be loaded before it.

// Opened from the iOS share-sheet Shortcut: /?url=<instagram link or shared text>
function consumeSharedUrl() {
  const params = new URLSearchParams(location.search);
  const raw = params.get('url') || params.get('text') || '';
  if (!raw) return '';
  history.replaceState(null, '', location.pathname);
  const match = raw.match(/https?:\/\/[^\s]+/);
  return match ? match[0] : '';
}

async function init() {
  const sharedUrl = consumeSharedUrl();
  await loadUser();
  if (currentUser) {
    await loadRecipes();
    if (sharedUrl) {
      urlInput.value = sharedUrl;
      form.requestSubmit();
    }
  }
}

init();
