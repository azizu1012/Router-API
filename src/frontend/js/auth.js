import { state, $ } from './state.js';
import { t } from './i18n.js';
import { api } from './api.js';

// ─── Login form ───────────────────────────────────────────────────
export async function doLogin() {
  const key = $('ki').value.trim();
  const err = $('lerr');
  const btn = $('lbtn');
  if (!key) { showErr(t('err_auth_key_required')); return; }
  btn.disabled = true;
  btn.textContent = t('btn_authenticating');
  err.style.display = 'none';
  try {
    const r = await fetch('/dashboard/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auth_key: key }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showErr(d.error || t('err_invalid_auth_key'));
      return;
    }
    const data = await r.json();
    state.tok  = data.token;
    state.usr  = { name: data.name, tier: data.tier };
    sessionStorage.setItem('_rt', state.tok);
    $('ki').value = '';
    enterApp();
  } catch (e) {
    showErr(t('err_server_connection') + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = t('login_btn');
  }
}

export function showErr(m) {
  const e = $('lerr');
  e.textContent = m;
  e.style.display = 'block';
}

// ─── Eye toggle ───────────────────────────────────────────────────
export function toggleEye() {
  const i = $('ki');
  const b = $('eb');
  if (i.type === 'password') {
    i.type = 'text';
    b.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
      <line x1="1" y1="1" x2="23" y2="23"></line>
    </svg>`;
  } else {
    i.type = 'password';
    b.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
      <circle cx="12" cy="12" r="3"></circle>
    </svg>`;
  }
}

// ─── Logout ───────────────────────────────────────────────────────
export function doLogout() {
  sessionStorage.removeItem('_rt');
  state.tok = null;
  state.usr = null;
  state.rawKeys = [];
  state.rawAccounts = [];
  state.statsData = null;
  state.myStatsData = null;
  state.rawEndpoints = null;
  state.rawPenalties = null;
  state.rawPools = null;
  state.rawPoolsDetail = null;
  state.rawMyAcc = null;
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  Object.values(state.ch).forEach(c => c?.destroy?.());
  state.ch = {};
  $('app').style.display = 'none';
  $('ls').style.display = 'flex';
}

// ─── Session init ─────────────────────────────────────────────────
export async function enterApp() {
  if (state.tok && !state.usr) {
    try {
      const me = await api('/dashboard/me');
      if (!me) return;
      state.usr = me;
    } catch {
      doLogout();
      return;
    }
  }

  $('ls').style.display = 'none';
  $('app').style.display = 'flex';
  $('uname').textContent = state.usr.name;

  const tb = $('utier');
  tb.textContent = state.usr.tier;
  tb.className   = 'tb tb-' + (state.usr.tier || 'free');

  const isAdmin = state.usr.tier === 'admin';
  document.querySelectorAll('.admin-only').forEach(el => el.classList.toggle('hide', !isAdmin));
  document.querySelectorAll('.user-only').forEach(el  => el.classList.toggle('hide', isAdmin));

  // Animate nav items in
  document.querySelectorAll('.ni').forEach((el, i) => {
    el.style.animation = 'none';
    void el.offsetWidth;
    el.style.animation = `navSlideIn 0.4s cubic-bezier(0.16, 1, 0.3, 1) ${i * 0.04}s both`;
  });

  const { go } = await import('./nav.js');
  go(isAdmin ? 'ov' : 'myacc');

  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(async () => {
    const { loadTab } = await import('./tabs.js');
    if (state.cur) loadTab(state.cur, true);
    // showRefresh();  // removed flash — data updates silently
  }, 10000);
}

export function showRefresh() {
  const el = $('rfind');
  if (!el) return;
  el.innerHTML = `<span class="refresh-badge">${t('lbl_refreshed')}</span>`;
  setTimeout(() => { el.innerHTML = ''; }, 3000);
}
