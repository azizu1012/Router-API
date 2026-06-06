import { state } from './state.js';
import { t, applyLanguage } from './i18n.js';

// ─── Template registry ────────────────────────────────────────────
const TEMPLATES = {
  ov:       () => import('./templates/tpl-ov.js').then(m => m.renderOv()),
  ks:       () => import('./templates/tpl-ks.js').then(m => m.renderKs()),
  ac:       () => import('./templates/tpl-ac.js').then(m => m.renderAc()),
  us:       () => import('./templates/tpl-us.js').then(m => m.renderUs()),
  ep:       () => import('./templates/tpl-ep.js').then(m => m.renderEp()),
  pe:       () => import('./templates/tpl-pe.js').then(m => m.renderPe()),
  mu:       () => import('./templates/tpl-mu.js').then(m => m.renderMu()),
  myacc:    () => import('./templates/tpl-myacc.js').then(m => m.renderMyAcc()),
  myuse:    () => import('./templates/tpl-myuse.js').then(m => m.renderMyUse()),
};

// Admin-only tab names
const ADMIN_TABS = new Set(['ov','ks','ac','us','ep','pe','mu']);

// ─── Navigation ───────────────────────────────────────────────────
export async function go(name) {
  // Update active nav button
  document.querySelectorAll('.ni').forEach(n => n.classList.remove('on'));
  const navBtn = document.getElementById('nv-' + name);
  if (navBtn) navBtn.classList.add('on');

  state.cur = name;

  // Inject tab HTML if not yet rendered
  await ensureTabMounted(name);

  // Show only the requested tab (hide others)
  document.querySelectorAll('.tp').forEach(p => p.classList.remove('on'));
  const panel = document.getElementById('tp-' + name);
  if (panel) panel.classList.add('on');

  // Load data
  const { loadTab } = await import('./tabs.js');
  loadTab(name, true);
}

// ─── Ensure tab HTML is injected into #main2 ─────────────────────
async function ensureTabMounted(name) {
  if (document.getElementById('tp-' + name)) return; // already mounted

  const main = document.getElementById('main2');
  if (!main) return;

  const tplFn = TEMPLATES[name];
  if (!tplFn) return;

  const html = await tplFn();

  const div = document.createElement('div');
  div.className = 'tp' + (ADMIN_TABS.has(name) ? ' admin-only' : '');
  div.id = 'tp-' + name;
  div.innerHTML = html;

  // Entrance animation
  div.style.animation = 'tabIn 0.4s cubic-bezier(0.16, 1, 0.3, 1)';
  main.appendChild(div);
}

// ─── Theme ────────────────────────────────────────────────────────
export function applyTheme(theme) {
  state.theme = theme;
  const root  = document.documentElement;
  root.classList.remove('theme-light', 'theme-sakura');

  let active = theme;
  if (theme === 'auto') {
    active = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  if (active === 'light')  root.classList.add('theme-light');
  if (active === 'sakura') root.classList.add('theme-sakura');

  localStorage.setItem('_rtm', theme);

  const selectedThemeText = document.getElementById('selected-theme-text');
  if (selectedThemeText) {
    const map = {
      auto: t('theme_auto'), dark: t('theme_dark'),
      light: t('theme_light'), sakura: t('theme_sakura'),
    };
    selectedThemeText.textContent = map[theme] || t('theme_auto');
  }

  const iconWrapper = document.getElementById('selected-theme-icon-wrapper');
  if (iconWrapper) {
    const opt = document.querySelector(`#theme-options .option-item[data-value="${theme}"]`);
    if (opt) {
      const svg = opt.querySelector('svg');
      if (svg) iconWrapper.innerHTML = svg.outerHTML;
    }
  }

  document.querySelectorAll('#theme-options .option-item').forEach(el => {
    el.classList.toggle('active', el.getAttribute('data-value') === theme);
  });

  updateChartColors();
}

export async function updateChartColors() {
  const { loadOv, loadMyUse } = await import('./tabs.js');
  if (state.cur === 'ov')     loadOv();
  if (state.cur === 'myuse')  loadMyUse();
}

// ─── Custom dropdown toggle ───────────────────────────────────────
export function toggleDropdown(event, dropdownId) {
  event.stopPropagation();
  const target    = document.getElementById(dropdownId);
  if (!target) return;
  const container = target.parentElement;
  const isOpen    = container.classList.contains('open');
  document.querySelectorAll('.custom-select').forEach(el => el.classList.remove('open'));
  if (!isOpen) container.classList.add('open');
}

// ─── Lang / theme switchers ───────────────────────────────────────
export function changeLang(lang) {
  if (lang !== state.lang) {
    localStorage.setItem('_rl', lang);
    window.location.reload();
  }
}

export function changeTheme(theme) { applyTheme(theme); }

// System preference listener
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (localStorage.getItem('_rtm') === 'auto') applyTheme('auto');
});

// Close dropdowns on outside click
document.addEventListener('click', () => {
  document.querySelectorAll('.custom-select').forEach(el => el.classList.remove('open'));
});
