// Entry point. Loaded LAST — all other js/ files must be loaded before it.
async function init() {
  await loadUser();
  if (currentUser) {
    await loadRecipes();
  }
}

init();
