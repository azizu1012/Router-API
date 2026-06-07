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
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { const body = await r.json(); if (body?.detail) detail = body.detail; } catch {}
    throw new Error(detail);
  }
  return r.json();
}
