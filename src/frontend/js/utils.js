import { state, CLR, $ } from './state.js';
import { t } from './i18n.js';

// ─── Number Formatters ────────────────────────────────────────────
export function fmt(v) {
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return (v || 0).toLocaleString();
}

export function fmtD(ts) {
  if (!ts) return '—';
  const locale = state.lang === 'vi' ? 'vi-VN' : state.lang === 'ja' ? 'ja-JP' : 'en-US';
  return new Date(ts * 1000).toLocaleDateString(locale);
}

// ─── Relative time with animated clock ───────────────────────────
export function relt(ts) {
  const s = ts - Date.now() / 1000;
  if (s <= 0) return `<span class="expired-tag">${t('lbl_expired')}</span>`;

  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  let timeStr = h > 0 ? `${h}h ${m}m ${sec}s` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;

  return `
    <span class="cooldown-wrapper" data-until="${ts}">
      <svg class="clock-anim" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"></circle>
        <polyline points="12 6 12 12 16 14" class="clock-hand"></polyline>
      </svg>
      <span class="countdown-text">${timeStr}</span>
    </span>
  `;
}

// ─── Badge helpers ────────────────────────────────────────────────
export function tBadge(tier) {
  const m = { free: 'tb-free', premium: 'tb-premium', admin: 'tb-admin' };
  const label = tier || 'free';
  return `<span class="tb ${m[label] || 'tb-free'}">${t('opt_' + label)}</span>`;
}

export function sBadge(e) {
  return e
    ? `<span class="b bg"><span class="hd hg"></span>${t('opt_active')}</span>`
    : `<span class="b bx"><span class="hd hx"></span>${t('opt_disabled')}</span>`;
}

// ─── UI primitives ────────────────────────────────────────────────
export function spHtml() {
  return `<div class="ld"><div class="sp"></div>${t('loading')}</div>`;
}

export function emptyRow(cols, msg) {
  return `<tr><td colspan="${cols}" class="empty-row">${msg}</td></tr>`;
}

export function animateUpdateText(id, newText) {
  const el = $(id);
  if (!el) return;
  if (el.textContent !== newText) {
    el.textContent = newText;
    el.classList.remove('num-changed');
    void el.offsetWidth; // trigger reflow
    el.classList.add('num-changed');
  }
}

export function updateProgressBar(id, pct, color) {
  const el = $(id);
  if (!el) return;
  el.style.width = pct + '%';
  el.style.background = color;
}

// ─── Message box helper ───────────────────────────────────────────
export function showMsg(id, msg, isError = false) {
  const el = $(id);
  if (!el) return;
  el.textContent = msg;
  el.className = 'msg-box ' + (isError ? 'msg-error' : 'msg-ok');
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; el.textContent = ''; }, 5000);
}
