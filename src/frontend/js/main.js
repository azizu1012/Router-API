/**
 * main.js — Entry point for Router API Cockpit
 * Initializes all modules, binds global window functions,
 * starts security guards and real-time countdown timers.
 */
import { state, $ } from './state.js';
import { t, applyLanguage } from './i18n.js';
import { doLogin, doLogout, enterApp, toggleEye } from './auth.js';
import { go, applyTheme, toggleDropdown, changeLang, changeTheme } from './nav.js';
import {
  filterKeys, filterAccounts,
  handleAddKey,
  handleAddEndpoint, handleEpPoolAssign, handleEpToggle,
  handleEpAccountAssign,
  handleCreateAccount,
  handleKeyPoolAssign,
  toggleEpModels, handleRefreshEpModels,
  handleEpModelToggle,
} from './tabs.js';

// ─── Security: Anti-DevTools ──────────────────────────────────────
document.addEventListener('contextmenu', e => e.preventDefault());
document.addEventListener('keydown', e => {
  if (e.key === 'F12') e.preventDefault();
  if (e.ctrlKey && e.shiftKey && 'IJC'.includes(e.key.toUpperCase())) e.preventDefault();
  if (e.ctrlKey && e.key.toLowerCase() === 'u') e.preventDefault();
});

setInterval(() => {
  const threshold    = 160;
  const isOpen       = (window.outerWidth - window.innerWidth > threshold) ||
                       (window.outerHeight - window.innerHeight > threshold);
  const dtwEl        = document.getElementById('dtw');
  if (dtwEl) dtwEl.style.display = isOpen ? 'flex' : 'none';
}, 1000);

// ─── Real-time countdown timers ───────────────────────────────────
setInterval(() => {
  const now = Date.now() / 1000;

  // Cooldown / penalty countdowns
  document.querySelectorAll('.cooldown-wrapper').forEach(el => {
    const until = parseFloat(el.getAttribute('data-until'));
    const s     = until - now;
    const txtEl = el.querySelector('.countdown-text');
    if (!txtEl) return;
    if (s <= 0) {
      el.innerHTML = `<span class="expired-tag" style="color:var(--emerald);font-weight:600">${t('lbl_expired')}</span>`;
      return;
    }
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    txtEl.textContent = h > 0 ? `${h}h ${m}m ${sec}s` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  });

  // RPD reset countdown
  document.querySelectorAll('.rpd-reset-countdown').forEach(el => {
    const ts     = parseFloat(el.getAttribute('data-tomorrow-ts'));
    const diffMs = (ts * 1000) - Date.now();
    if (diffMs <= 0) { el.textContent = '0h 0m 0s'; return; }
    const hrs  = Math.floor(diffMs / 3600000);
    const mins = Math.floor((diffMs % 3600000) / 60000);
    const secs = Math.floor((diffMs % 60000) / 1000);
    el.textContent = `${hrs}h ${mins}m ${secs}s`;
  });
}, 1000);

// ─── Particle Canvas Background ───────────────────────────────────
function initParticles() {
  const canvas = document.getElementById('particles-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let W = canvas.width = window.innerWidth;
  let H = canvas.height = window.innerHeight;

  const particles = Array.from({ length: 60 }, () => ({
    x: Math.random() * W,
    y: Math.random() * H,
    r: Math.random() * 1.5 + 0.3,
    vx: (Math.random() - 0.5) * 0.3,
    vy: (Math.random() - 0.5) * 0.3,
    alpha: Math.random() * 0.4 + 0.05,
  }));

  function draw() {
    ctx.clearRect(0, 0, W, H);
    const isDark = !document.documentElement.classList.contains('theme-light')
                && !document.documentElement.classList.contains('theme-sakura');
    const baseColor = isDark ? '99,102,241' : '99,102,241';

    particles.forEach(p => {
      p.x = (p.x + p.vx + W) % W;
      p.y = (p.y + p.vy + H) % H;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${baseColor},${p.alpha})`;
      ctx.fill();
    });

    // Draw faint connecting lines
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 100) {
          ctx.strokeStyle = `rgba(${baseColor},${0.04 * (1 - dist / 100)})`;
          ctx.lineWidth   = 0.5;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
        }
      }
    }
    requestAnimationFrame(draw);
  }

  window.addEventListener('resize', () => {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  });

  draw();
}

// ─── Bind global functions (required for inline onclick in HTML) ──
window.doLogin     = doLogin;
window.doLogout    = doLogout;
window.toggleEye   = toggleEye;
window.go          = go;
window.filterKeys  = filterKeys;
window.filterAccounts = filterAccounts;
window.toggleDropdown = toggleDropdown;
window.changeLang  = changeLang;
window.changeTheme = changeTheme;

// Admin actions
window.handleAddKey                = handleAddKey;
window.handleAddEndpoint           = handleAddEndpoint;
window.handleEpPoolAssign          = handleEpPoolAssign;
window.handleEpToggle              = handleEpToggle;
window.handleEpAccountAssign       = handleEpAccountAssign;
window.handleKeyPoolAssign         = handleKeyPoolAssign;
window.toggleEpModels              = toggleEpModels;
window.handleRefreshEpModels       = handleRefreshEpModels;
window.handleEpModelToggle         = handleEpModelToggle;
window.handleCreateAccount         = handleCreateAccount;

// Key/endpoint delete helpers (called from rendered HTML)
window._deleteKey = async (key) => {
  if (!confirm(`Remove key ${key}?`)) return;
  try {
    const { api } = await import('./api.js');
    await api('/dashboard/admin/keys/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    state.rawKeys = [];
    const { loadKs } = await import('./tabs.js');
    loadKs(true);
  } catch (e) { alert('Error: ' + e.message); }
};

window._deleteEndpoint = async (name) => {
  if (!confirm(`Remove endpoint "${name}"?`)) return;
  try {
    const { api } = await import('./api.js');
    await api('/dashboard/admin/endpoints/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    state.rawEndpoints = null;
    const { loadEp } = await import('./tabs.js');
    loadEp(true);
  } catch (e) { alert('Error: ' + e.message); }
};

// Account management actions (called from rendered HTML)
window._toggleAccount = async (name, currentEnabled) => {
  const newEnabled = !currentEnabled;
  try {
    const { api } = await import('./api.js');
    await api('/dashboard/admin/accounts/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, enabled: newEnabled }),
    });
    const { loadAc } = await import('./tabs.js');
    loadAc(true);
  } catch (e) { alert('Error: ' + e.message); }
};

window._rotateAccountKey = async (name) => {
  if (!confirm(`Rotate Auth Key for account "${name}"? This will invalidate the old key immediately.`)) return;
  try {
    const { api } = await import('./api.js');
    const res = await api('/dashboard/admin/accounts/rotate-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (res && res.account) {
      alert(`Xoay Auth Key thành công!\nKey mới: ${res.account.auth_key}\n\n(Hãy lưu lại khóa này!)`);
    }
    const { loadAc } = await import('./tabs.js');
    loadAc(true);
  } catch (e) { alert('Error: ' + e.message); }
};

window._toggleWebSearch = async (enabled) => {
  try {
    const { api } = await import('./api.js');
    await api('/dashboard/my/web-search-toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const { loadMyAcc } = await import('./tabs.js');
    loadMyAcc(true);
  } catch (e) { console.error('Web search toggle error:', e); }
};

window._deleteAccount = async (name) => {
  if (!confirm(`Delete account "${name}" permanently? All usage statistics and key settings for this account will be lost.`)) return;
  try {
    const { api } = await import('./api.js');
    await api('/dashboard/admin/accounts/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const { loadAc } = await import('./tabs.js');
    loadAc(true);
  } catch (e) { alert('Error: ' + e.message); }
};

window._editAccountLimits = async (name, currentTier, currentRpm, currentTpm, currentRpd) => {
  const tier = prompt(`Nhập tier mới cho "${name}" (free/premium/admin):`, currentTier);
  if (tier === null) return;
  if (tier && !['free', 'premium', 'admin'].includes(tier.trim().toLowerCase())) {
    alert('Tier không hợp lệ! Vui lòng nhập: free, premium, hoặc admin');
    return;
  }

  const rpmStr = prompt(`Nhập RPM mới cho "${name}" (giới hạn RPM, để trống để giữ nguyên):`, currentRpm);
  if (rpmStr === null) return;

  const tpmStr = prompt(`Nhập TPM mới cho "${name}" (giới hạn TPM, để trống để giữ nguyên):`, currentTpm);
  if (tpmStr === null) return;

  const rpdStr = prompt(`Nhập RPD mới cho "${name}" (giới hạn RPD, để trống để giữ nguyên):`, currentRpd);
  if (rpdStr === null) return;

  const body = { name };
  if (tier) body.tier = tier.trim().toLowerCase();
  if (rpmStr.trim() !== '') body.rpm = parseInt(rpmStr, 10);
  if (tpmStr.trim() !== '') body.tpm = parseInt(tpmStr, 10);
  if (rpdStr.trim() !== '') body.rpd = parseInt(rpdStr, 10);

  try {
    const { api } = await import('./api.js');
    await api('/dashboard/admin/accounts/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, ...body }),
    });
    const { loadAc } = await import('./tabs.js');
    loadAc(true);
  } catch (e) { alert('Error: ' + e.message); }
};

// ─── Bind login Enter key ─────────────────────────────────────────
const kiInput = document.getElementById('ki');
if (kiInput) kiInput.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

// ─── Boot ─────────────────────────────────────────────────────────
applyLanguage(state.lang);
applyTheme(state.theme);
initParticles();

if (state.tok) {
  enterApp();
}

// ─── Silent background polls (no flash, no re-render) ─────────────
// Penalty badge — 5s interval, only updates badge number
setInterval(async () => {
  if (!state.tok || state.usr?.tier !== 'admin') return;
  try {
    const { api } = await import('./api.js');
    const data = await api('/dashboard/penalties');
    if (!data) return;
    const nb = document.getElementById('nbp');
    if (nb) {
      const count = (data.penalties || []).length;
      nb.textContent = count;
      nb.style.display = count ? 'inline' : 'none';
    }
  } catch {}
}, 5000);

// Cooldown timestamps + frozen badge — 15s interval, no re-render
setInterval(async () => {
  if (!state.tok) return;
  try {
    const { api } = await import('./api.js');
    const data = await api('/dashboard/keys');
    if (!data?.keys) return;
    state.rawKeys = data.keys;
    const now = Date.now() / 1000;
    const frozenCount = data.keys.filter(k => k.frozen_until > now).length;
    const nbf = document.getElementById('nbf');
    if (nbf) {
      nbf.textContent = frozenCount;
      nbf.style.display = frozenCount ? 'inline' : 'none';
    }
    document.querySelectorAll('.cooldown-wrapper').forEach(el => {
      const key = el.getAttribute('data-key');
      if (!key) return;
      const found = data.keys.find(k => k.key === key);
      if (found && found.frozen_until) {
        el.setAttribute('data-until', found.frozen_until);
      }
    });
  } catch {}
}, 15000);
