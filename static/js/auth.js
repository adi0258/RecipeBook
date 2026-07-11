// Auth UI: header user chip + logged-in/out shell toggling.
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
    toolbar.hidden    = true;
    grid.innerHTML    = '';
  }
}
