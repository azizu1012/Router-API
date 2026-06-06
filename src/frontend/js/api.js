import { state, $ } from './state.js';

// ─── Authenticated fetch wrapper ──────────────────────────────────
export async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: {
      'X-Dashboard-Token': state.tok || '',
      ...(opts.headers || {}),
    },
  });
  if (r.status === 401) {
    // Token expired → logout
    const { doLogout } = await import('./auth.js');
    doLogout();
    return null;
  }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}
