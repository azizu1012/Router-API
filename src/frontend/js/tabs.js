import { state, CLR, $ } from './state.js';
import { t } from './i18n.js';
import { fmt, fmtD, relt, tBadge, sBadge, spHtml, emptyRow, animateUpdateText, updateProgressBar, showMsg } from './utils.js';
import { api } from './api.js';
import {
  mkLine, mkDonut, buildDS, updateLineChart, updateDonutChart,
  animateSavings, statsCards, statsTable
} from './charts.js';

// ─── loadTab dispatcher ───────────────────────────────────────────
export function loadTab(n, force = false) {
  const tabs = {
    ov: loadOv, ks: loadKs, ac: loadAc, us: loadUs,
    ep: loadEp, pe: loadPe, mu: loadMu,
    myacc: loadMyAcc, myuse: loadMyUse,
  };
  tabs[n]?.(force);
}

// ─── [ADMIN] Overview ─────────────────────────────────────────────
export async function loadOv(force = false) {
  try {
    if (force || !state.statsData) {
      const data = await api('/api/stats?days=30');
      if (!data) return;
      state.statsData = data;
    }
    const { summary: s = [], daily: d = [], savings } = state.statsData;
    const now = new Date();
    const td  = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    const tdd = d.filter(x => x.d === td);
    const tdt = tdd.reduce((a, b) => a + (b.t || 0), 0);
    const tdr = tdd.reduce((a, b) => a + (b.req || 0), 0);
    const tot = s.reduce((a, b) => a + (b.t || 0), 0);
    const req = s.reduce((a, b) => a + (b.req || 0), 0);
    const sav = savings?.savings || 0;

    if ($('ov-card-today-tokens')) {
      animateUpdateText('ov-card-today-tokens', fmt(tdt));
      animateUpdateText('ov-card-today-reqs',   `${tdr.toLocaleString()} ${t('requests_count')}`);
      animateUpdateText('ov-card-30d-tokens',   fmt(tot));
      animateUpdateText('ov-card-30d-reqs',     `${req.toLocaleString()} ${t('requests_count')}`);
      animateUpdateText('ov-card-active-models', s.length.toString());
      setTimeout(() => animateSavings(sav, 'ov'), 100);
    } else {
      $('ov-cards').innerHTML = statsCards(s, d, savings, 'ov');
    }

    $('ov-tb').innerHTML = statsTable(s);

    const ld = buildDS(d);
    if (_ch('day') && ld.labels.length) {
      updateLineChart(state.ch.day, ld.labels, ld.datasets);
    } else if (ld.labels.length) {
      state.ch.day?.destroy();
      state.ch.day = mkLine($('cDay').getContext('2d'), ld.labels, ld.datasets);
    }
    if (_ch('mod') && s.length) {
      updateDonutChart(state.ch.mod, s.map(x => x.model_alias), s.map(x => x.t || 0));
    } else if (s.length) {
      state.ch.mod?.destroy();
      state.ch.mod = mkDonut($('cMod').getContext('2d'), s.map(x => x.model_alias), s.map(x => x.t || 0));
    }
  } catch (e) {
    $('ov-cards').innerHTML = `<div class="sc cr"><div class="sc-lb">${t('load_error')}</div><div class="sc-s">${e.message}</div></div>`;
  }
}

// ─── [ADMIN] Gemini Keys ──────────────────────────────────────────
export async function loadKs(force = false) {
  if (!state.rawKeys?.length) {
    $('ks-tb').innerHTML = `<tr><td colspan="10">${spHtml()}</td></tr>`;
  }
  try {
    if (force || !state.rawKeys?.length) {
      const data = await api('/dashboard/keys');
      if (!data) return;
      state.rawKeys = data.keys || [];
    }
    const stdKeys  = state.rawKeys;
    const healthy  = stdKeys.filter(k => k.enabled && !k.frozen).length;
    const frozen   = stdKeys.filter(k => k.frozen).length;
    const disabled = stdKeys.filter(k => !k.enabled).length;

    if ($('ks-card-healthy')) {
      animateUpdateText('ks-card-healthy', healthy.toString());
      animateUpdateText('ks-card-cooldown', frozen.toString());
      animateUpdateText('ks-card-disabled', disabled.toString());
      animateUpdateText('ks-card-total', stdKeys.length.toString());
    } else {
      $('ks-cards').innerHTML = `
        <div class="sc cg sc-anim"><div class="sc-lb">${t('st_healthy')}</div><div class="sc-v num-animate" id="ks-card-healthy">${healthy}</div><div class="sc-s">${t('ks_card_healthy_sub')}</div></div>
        <div class="sc ca sc-anim" style="animation-delay:0.05s"><div class="sc-lb">${t('st_cooldown')}</div><div class="sc-v num-animate" id="ks-card-cooldown">${frozen}</div><div class="sc-s">${t('ks_card_cooldown_sub')}</div></div>
        <div class="sc cr sc-anim" style="animation-delay:0.1s"><div class="sc-lb">${t('st_disabled')}</div><div class="sc-v num-animate" id="ks-card-disabled">${disabled}</div><div class="sc-s">${t('ks_card_disabled_sub')}</div></div>
        <div class="sc cp sc-anim" style="animation-delay:0.15s"><div class="sc-lb">${t('ks_card_total')}</div><div class="sc-v num-animate" id="ks-card-total">${stdKeys.length}</div><div class="sc-s">${t('ks_card_total_sub')}</div></div>
      `;
    }
    const nb = $('nbf');
    nb.textContent   = frozen;
    nb.style.display = frozen ? 'inline' : 'none';
    renderFilteredKeys();
  } catch (e) {
    $('ks-tb').innerHTML = `<tr><td colspan="9" style="color:var(--rose);padding:16px">${e.message}</td></tr>`;
  }
}

export function renderFilteredKeys() {
  const query  = ($('ks-search')?.value || '').toLowerCase().trim();
  const tier   = $('ks-filter-tier')?.value   || 'all';
  const status = $('ks-filter-status')?.value || 'all';
  const now    = Date.now() / 1000;

  const stdKeys = state.rawKeys || [];

  const filtered = stdKeys.filter(k => {
    const fr     = k.frozen_until > now;
    const stKey  = !k.enabled ? 'disabled' : fr ? 'frozen' : k.consecutive_failures >= 3 ? 'degraded' : 'healthy';
    return k.key.toLowerCase().includes(query)
      && (tier   === 'all' || k.tier === tier)
      && (status === 'all' || stKey === status);
  });

  $('ks-cnt').textContent = `${filtered.length} / ${stdKeys.length} ${t('keys_count')}`;
  if (!filtered.length) { $('ks-tb').innerHTML = emptyRow(9, t('no_keys_found')); return; }

  const tiers = {
    admin:   { label: t('group_admin'),   color: '#a5b4fc', bg: 'rgba(99,102,241,.08)',  list: [] },
    premium: { label: t('group_premium'), color: '#fbbf24', bg: 'rgba(245,158,11,.08)', list: [] },
    free:    { label: t('group_free'),    color: '#d1d5db', bg: 'rgba(156,163,175,.08)', list: [] },
  };
  filtered.forEach(k => (tiers[k.tier || 'free'] || tiers.free).list.push(k));

  let html = '';
  ['admin','premium','free'].forEach(tKey => {
    const g = tiers[tKey];
    if (!g.list.length) return;
    html += `<tr><td colspan="9" style="background:${g.bg};font-weight:700;padding:10px 20px;color:${g.color};font-size:11px;letter-spacing:0.5px">${g.label} (${g.list.length})</td></tr>`;
    html += g.list.map((k, i) => {
      const fr  = k.frozen_until > now;
      const dot = !k.enabled ? 'hx' : fr ? 'ha' : k.consecutive_failures >= 3 ? 'hr' : 'hg';
      const st  = !k.enabled ? t('st_disabled') : fr ? t('st_cooldown') : k.consecutive_failures >= 3 ? t('st_degraded') : t('st_healthy');
      const sb  = !k.enabled ? 'bx' : fr ? 'ba' : k.consecutive_failures >= 3 ? 'br' : 'bg';
      const cooldownInline = fr ? ` <div style="display:inline-block;vertical-align:middle;margin-left:6px">${relt(k.frozen_until)}</div>` : '';
      const allowedPools = k.allowed_pools || [];
      const assignedPool = allowedPools.length > 0 ? allowedPools[0] : 'all';
      return `
        <tr class="tr-anim" style="animation-delay:${i*0.02}s">
          <td><code style="font-weight:500;color:var(--primary)">${k.key}</code></td>
          <td>${tBadge(k.tier)}</td>
          <td>
            <span class="hd ${dot}"></span><span class="b ${sb}">${st}</span>${cooldownInline}
          </td>
          <td>${fmt(k.today || 0)}</td>
          <td>${fmt(k.usage || 0)}</td>
          <td>${k.active_requests || 0}</td>
          <td>${k.consecutive_failures > 0 ? `<span style="color:var(--rose)">${k.consecutive_failures}</span>` : 0}</td>
          <td>
            <select
              style="background:var(--bg-tertiary,rgba(0,0,0,0.25));border:1px solid var(--border,rgba(255,255,255,0.1));border-radius:6px;padding:3px 8px;font-size:11px;color:var(--text);cursor:pointer;outline:none"
              onchange="window.handleKeyPoolAssign('${k.key}', this.value)">
              <option value="all" ${assignedPool === 'all' ? 'selected' : ''}>Tất cả (All)</option>
              <option value="gemini-flash" ${assignedPool === 'gemini-flash' ? 'selected' : ''}>gemini-flash</option>
              <option value="gemini-flash-lite" ${assignedPool === 'gemini-flash-lite' ? 'selected' : ''}>gemini-flash-lite</option>
            </select>
          </td>
          <td>
            <button class="btn btn-danger" style="padding:4px 8px;font-size:10px" onclick="window._deleteKey('${k.key}')" title="Remove">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>
            </button>
          </td>
        </tr>`;
    }).join('');
  });
  $('ks-tb').innerHTML = html;
}

export function filterKeys() { renderFilteredKeys(); }

// ─── [ADMIN] Accounts ─────────────────────────────────────────────
export async function loadAc(force = false) {
  if (!state.rawAccounts?.length) {
    $('ac-tb').innerHTML = `<tr><td colspan="9">${spHtml()}</td></tr>`;
  }
  try {
    if (force || !state.rawAccounts?.length) {
      const data = await api('/dashboard/accounts');
      if (!data) return;
      state.rawAccounts = data.accounts || [];
    }
    const bt = { free: 0, premium: 0, admin: 0 };
    state.rawAccounts.forEach(a => { bt[a.tier || 'free'] = (bt[a.tier || 'free'] || 0) + 1; });

    if ($('ac-card-total')) {
      animateUpdateText('ac-card-total',   state.rawAccounts.length.toString());
      animateUpdateText('ac-card-free',    (bt.free || 0).toString());
      animateUpdateText('ac-card-premium', (bt.premium || 0).toString());
      animateUpdateText('ac-card-admin',   (bt.admin || 0).toString());
    } else {
      $('ac-cards').innerHTML = `
        <div class="sc sc-anim"><div class="sc-lb">${t('ac_card_total')}</div><div class="sc-v num-animate" id="ac-card-total">${state.rawAccounts.length}</div><div class="sc-s">${t('ac_card_total_sub')}</div></div>
        <div class="sc cg sc-anim" style="animation-delay:0.05s"><div class="sc-lb">${t('ac_card_free')}</div><div class="sc-v num-animate" id="ac-card-free">${bt.free||0}</div><div class="sc-s">${t('ac_card_free_sub')}</div></div>
        <div class="sc ca sc-anim" style="animation-delay:0.1s"><div class="sc-lb">${t('ac_card_premium')}</div><div class="sc-v num-animate" id="ac-card-premium">${bt.premium||0}</div><div class="sc-s">${t('ac_card_premium_sub')}</div></div>
        <div class="sc cp sc-anim" style="animation-delay:0.15s"><div class="sc-lb">${t('ac_card_admin')}</div><div class="sc-v num-animate" id="ac-card-admin">${bt.admin||0}</div><div class="sc-s">${t('ac_card_admin_sub')}</div></div>
      `;
    }
    renderFilteredAccounts();
  } catch (e) {
    $('ac-tb').innerHTML = `<tr><td colspan="9" style="color:var(--rose);padding:16px">${e.message}</td></tr>`;
  }
}

export function renderFilteredAccounts() {
  const query  = ($('ac-search')?.value || '').toLowerCase().trim();
  const tier   = $('ac-filter-tier')?.value   || 'all';
  const status = $('ac-filter-status')?.value || 'all';

  const filtered = state.rawAccounts.filter(a =>
    (a.name.toLowerCase().includes(query) || a.account_id?.toLowerCase().includes(query))
    && (tier   === 'all' || a.tier    === tier)
    && (status === 'all' || (status === 'active' && a.enabled) || (status === 'disabled' && !a.enabled))
  );

  $('ac-cnt').textContent = `${filtered.length} / ${state.rawAccounts.length} ${t('accounts_count')}`;
  if (!filtered.length) { $('ac-tb').innerHTML = emptyRow(9, t('no_accounts_found')); return; }

  $('ac-tb').innerHTML = filtered.map(a => {
    const keyDisplay = a.auth_key 
      ? `<code class="clickable-key" onclick="navigator.clipboard.writeText('${a.auth_key}'); alert('${t('lbl_refreshed')} (Copied)')" title="Click to copy" style="cursor:pointer;color:var(--primary);font-weight:600">${a.auth_key}</code>`
      : `<span style="color:var(--text-muted)">— (Hidden)</span>`;
      
    const actionButtons = `
      <div style="display:flex;gap:6px;align-items:center">
        <button class="btn btn-secondary" style="padding:4px 8px" onclick="window._toggleAccount('${a.name}', ${a.enabled})" title="${a.enabled ? 'Vô hiệu hóa' : 'Kích hoạt'}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;color:${a.enabled ? 'var(--rose)' : 'var(--emerald)'}"><path d="M18.36 6.64a9 9 0 1 1-12.73 0M12 2v10"/></svg>
        </button>
        <button class="btn btn-secondary" style="padding:4px 8px" onclick="window._editAccountLimits('${a.name}', '${a.tier}', ${a.rpm}, ${a.tpm}, ${a.rpd})" title="Sửa giới hạn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        </button>
        <button class="btn btn-secondary" style="padding:4px 8px" onclick="window._rotateAccountKey('${a.name}')" title="Xoay Auth Key">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
        <button class="btn btn-danger" style="padding:4px 8px" onclick="window._deleteAccount('${a.name}')" title="Xóa tài khoản">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>
        </button>
      </div>`;

    return `
      <tr class="tr-anim">
        <td><strong>${a.name}</strong></td>
        <td>${keyDisplay}</td>
        <td>${tBadge(a.tier)}</td>
        <td>${sBadge(a.enabled)}</td>
        <td>${(a.rpm || 0).toLocaleString()}</td>
        <td>${fmt(a.tpm || 0)}</td>
        <td>${(a.rpd || 0).toLocaleString()}</td>
        <td style="color:var(--text-muted)">${fmtD(a.created_at)}</td>
        <td>${actionButtons}</td>
      </tr>`;
  }).join('');
}

export function filterAccounts() { renderFilteredAccounts(); }

export async function handleCreateAccount() {
  const nameEl = $('ac-new-name');
  const name = nameEl?.value.trim();
  if (!name) return;

  const tier = $('ac-new-tier')?.value || 'free';
  const rpm = $('ac-new-rpm')?.value.trim();
  const tpm = $('ac-new-tpm')?.value.trim();
  const rpd = $('ac-new-rpd')?.value.trim();

  const body = { name, tier };
  if (rpm) body.rpm = parseInt(rpm, 10);
  if (tpm) body.tpm = parseInt(tpm, 10);
  if (rpd) body.rpd = parseInt(rpd, 10);

  showMsg('add-ac-msg', t('loading'));
  try {
    const res = await api('/dashboard/admin/accounts/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    nameEl.value = '';
    const rpmEl = $('ac-new-rpm'); if (rpmEl) rpmEl.value = '';
    const tpmEl = $('ac-new-tpm'); if (tpmEl) tpmEl.value = '';
    const rpdEl = $('ac-new-rpd'); if (rpdEl) rpdEl.value = '';
    
    if (res && res.account) {
      alert(`Tạo tài khoản ${name} thành công!\nKey truy cập: ${res.account.auth_key}\n\n(Hãy lưu lại khóa này!)`);
    }
    showMsg('add-ac-msg', t('msg_saved'));
    loadAc(true);
  } catch (e) {
    showMsg('add-ac-msg', e.message, true);
  }
}



// ─── [ADMIN] Usage Analytics ──────────────────────────────────────
export async function loadUs(force = false) {
  const el = $('us-ct');
  if (!state.statsData) el.innerHTML = spHtml();
  try {
    if (force || !state.statsData) {
      const data = await api('/api/stats?days=30');
      if (!data) return;
      state.statsData = data;
    }
    const tk = state.statsData.top_keys || [];
    if (!tk.length) { el.innerHTML = `<div class="empty-state">📭 ${t('no_data')}</div>`; return; }
    const mx = Math.max(...tk.map(x => x.t));
    el.innerHTML = tk.map((k, i) => `
      <div class="usage-row tr-anim" style="animation-delay:${i*0.03}s">
        <div class="usage-row-head">
          <span><strong>${k.account_name}</strong><code style="font-size:11px;color:var(--primary);margin-left:8px">${k.full_key}</code></span>
          <span style="font-size:12px;color:var(--text-muted)">${fmt(k.t)} tokens · ${k.req} ${t('requests_count')}</span>
        </div>
        <div class="pw"><div class="pb" style="width:${(k.t/mx*100).toFixed(0)}%;background:${CLR[i%CLR.length]}"></div></div>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = `<p style="color:var(--rose)">${t('load_error')}: ${e.message}</p>`;
  }
}

// ─── [ADMIN] Endpoints ────────────────────────────────────────────
export async function loadEp(force = false) {
  const container = $('ep-list-container');
  if (container && !state.rawEndpoints) container.innerHTML = spHtml();
  try {
    // Load endpoints and pool config in parallel
    const fetches = [];
    if (force || !state.rawEndpoints) fetches.push(api('/dashboard/endpoints').then(d => { if (d) state.rawEndpoints = d.endpoints || []; }));
    if (force || !state.rawPools)     fetches.push(api('/api/model-pools').then(d => { if (d) state.rawPools = d.pools || []; }));
    await Promise.all(fetches);

    const eps   = state.rawEndpoints || [];
    const pools = state.rawPools || [];
    const cntEl = $('ep-cnt');
    if (cntEl) cntEl.textContent = `${eps.length} ${t('endpoints_count')}`;
    if (!container) return;

    if (!eps.length) {
      container.innerHTML = `<div style="text-align:center;padding:32px;color:var(--text-dark)">${t('no_endpoints')}</div>`;
      return;
    }

    // Pool options HTML for dropdowns (built from API, not hardcoded)
    const poolOptions = [
      `<option value="none">— ${t('lbl_no_pool') || 'Not assigned'} —</option>`,
      ...pools.map(p => `<option value="${p.id}">${p.icon} ${p.label}</option>`),
    ].join('');

    container.innerHTML = eps.map((ep, i) => {
      const models       = ep.models || [];
      const poolAssign   = ep.pool_assignments || {}; // { model_id: pool_name_or_array }
      const isEnabled    = ep.enabled !== false;
      const hasFallback  = !!ep.fallback;
      const sanitizedId  = ep.name.replace(/[^a-z0-9]/gi,'_');

      // Per-model pool assignment rows
      const modelRows = models.map(mid => {
        const assignedPool = Array.isArray(poolAssign[mid]) ? poolAssign[mid][0] : (poolAssign[mid] || 'none');
        return `
          <tr style="border-bottom:1px solid var(--border-subtle,rgba(255,255,255,0.04))">
            <td style="padding:5px 8px;font-family:monospace;font-size:11px;color:var(--primary)">${mid}</td>
            <td style="padding:5px 8px">
              <select
                style="background:var(--bg-tertiary,rgba(0,0,0,0.25));border:1px solid var(--border,rgba(255,255,255,0.1));border-radius:6px;padding:3px 8px;font-size:11px;color:var(--text);cursor:pointer;outline:none"
                onchange="handleEpPoolAssign('${ep.name}', '${mid}', this.value)">
                ${poolOptions.replace(`value="${assignedPool}"`, `value="${assignedPool}" selected`)}
              </select>
            </td>
          </tr>`;
      }).join('');

      const noModels = !models.length ? `
        <div style="padding:10px;font-size:11px;color:var(--text-muted);text-align:center">
          No models fetched yet — endpoint may need to be reached
        </div>` : '';

      return `
        <div class="tcd" style="margin-bottom:12px;overflow:visible" data-ep="${ep.name}">
          <!-- EP Header -->
          <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;flex-wrap:wrap;gap:8px">
            <div style="display:flex;align-items:center;gap:10px;min-width:0">
              <div style="width:8px;height:8px;border-radius:50%;background:${isEnabled ? 'var(--emerald)' : 'var(--rose)'};flex-shrink:0"></div>
              <strong style="font-size:14px;color:var(--text)">${ep.name}</strong>
              <span style="font-size:11px;font-family:monospace;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${ep.base_url}">${ep.base_url}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;flex-shrink:0">
              ${hasFallback ? `<span class="b ba" style="font-size:10px">Fallback</span>` : ''}
              <button class="btn btn-secondary" style="font-size:10px;padding:3px 8px" id="ep-fetch-btn-${sanitizedId}"
                onclick="window.handleRefreshEpModels('${ep.name}')">
                🔄 Fetch Models
              </button>
              <button class="btn btn-secondary" style="font-size:10px;padding:3px 8px"
                onclick="handleEpToggle('${ep.name}', ${isEnabled})"
                title="${isEnabled ? 'Disable' : 'Enable'}">
                ${isEnabled ? '⏸ Disable' : '▶ Enable'}
              </button>
              <button class="btn btn-danger" style="font-size:10px;padding:3px 8px"
                onclick="window._deleteEndpoint('${ep.name}')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:11px;height:11px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg>
              </button>
            </div>
          </div>

          <!-- Pool assignment table -->
          <div style="border-top:1px solid var(--border,rgba(255,255,255,0.08));padding:10px 16px">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
              <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:var(--text-muted)">Pool Assignment</span>
              ${models.length ? `
              <button class="btn btn-secondary" style="font-size:10px;padding:2px 8px"
                id="btn-toggle-ep-${sanitizedId}" data-count="${models.length}"
                onclick="window.toggleEpModels('${sanitizedId}')">
                ▶ Hiện (${models.length} models)
              </button>` : ''}
            </div>
            ${noModels}
            <div id="ep-models-container-${sanitizedId}" style="display:none;margin-top:6px">
              ${models.length ? `<table style="width:100%;border-collapse:collapse">${modelRows}</table>` : ''}
            </div>
          </div>

          <div id="ep-pool-msg-${sanitizedId}" class="msg-box" style="margin:0 16px 8px"></div>
        </div>`;
    }).join('');
  } catch (e) {
    if ($('ep-list-container')) $('ep-list-container').innerHTML = `<p style="color:var(--rose);padding:16px">${e.message}</p>`;
  }
}

// ─── [ADMIN] Penalties ────────────────────────────────────────────
export async function loadPe(force = false) {
  const el = $('pe-ct');
  if (!state.rawPenalties) el.innerHTML = spHtml();
  try {
    if (force || !state.rawPenalties) {
      const data = await api('/dashboard/penalties');
      if (!data) return;
      state.rawPenalties = data.penalties || [];
    }
    const ps = state.rawPenalties;
    const nb = $('nbp');
    nb.textContent   = ps.length;
    nb.style.display = ps.length ? 'inline' : 'none';

    if (!ps.length) {
      el.innerHTML = `<div class="empty-state-green">✓ ${t('no_penalties')}</div>`;
      return;
    }
    el.innerHTML = `
      <div class="tcd">
        <div class="tscr">
          <table>
            <thead><tr>
              <th class="tooltip" data-tip-i18n="tip_key_code" data-tip="" data-i18n="th_key_code">${t('th_key_code')}</th>
              <th data-i18n="th_model">${t('th_model')}</th>
              <th data-i18n="lbl_error_reason">${t('lbl_error_reason')}</th>
              <th data-i18n="th_score_reduction">${t('th_score_reduction')}</th>
              <th data-i18n="lbl_expires_after">${t('lbl_expires_after')}</th>
            </tr></thead>
            <tbody>
              ${ps.map(p => `
                <tr class="tr-anim">
                  <td><code style="color:var(--primary)">${p.key}</code></td>
                  <td><code>${p.model_id || t('lbl_global')}</code></td>
                  <td style="color:var(--amber)"><code>${p.reason || '—'}</code></td>
                  <td><span class="b br">-${p.score_reduction || 0}</span></td>
                  <td>${relt(p.expires)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>`;
  } catch (e) {
    el.innerHTML = `<p style="color:var(--rose);padding:20px">${t('load_error')}: ${e.message}</p>`;
  }
}

// ─── [ADMIN] Pool Analysis & Savings ─────────────────────────────
export async function loadMu(force = false) {
  try {
    if (force || !state.statsData) {
      const data = await api('/api/stats?days=30');
      if (!data) return;
      state.statsData = data;
    }
    const sav = state.statsData.savings || {};
    $('std-cost-val').textContent   = `$${(sav.standard_cost || 0).toFixed(4)}`;
    $('cache-cost-val').textContent = `$${(sav.cached_cost   || 0).toFixed(4)}`;
    $('gemini-cost-val').textContent= `$${(sav.gemini_cost   || 0).toFixed(4)}`;
    $('save-cost-val').textContent  = `$${(sav.net_savings   || 0).toFixed(4)}`;

    if (force || !state.rawPoolsDetail) {
      const poolData = await api('/api/model-pools-detail');
      if (poolData) state.rawPoolsDetail = poolData.pools || [];
    }
    if (state.rawPoolsDetail) {
      $('mu-tb').innerHTML = state.rawPoolsDetail.map(p => {
        const bc = p.name.includes('pro') ? 'bp' : 'bg';
        return `
          <tr class="tr-anim">
            <td><code>${p.name}</code></td>
            <td><code style="color:var(--primary)">${p.models}</code></td>
            <td>${p.rpm}</td>
            <td>${p.tpm}</td>
            <td><span class="b ${bc}">${p.status}</span></td>
          </tr>`;
      }).join('');
    }
  } catch {}
}


// Pool assignment for custom endpoints (called from inline dropdown onchange)
export async function handleEpPoolAssign(epName, modelId, poolName) {
  const safeId = epName.replace(/[^a-z0-9]/gi, '_');
  const msgEl = $(`ep-pool-msg-${safeId}`);
  try {
    await api('/dashboard/admin/endpoints/pool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: epName, model_id: modelId, pool_name: poolName }),
    });
    if (msgEl) { msgEl.textContent = `✅ ${modelId} → ${poolName === 'none' ? 'unassigned' : poolName}`; msgEl.className = 'msg-box msg-ok'; setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 2500); }
    state.rawEndpoints = null;
  } catch (e) {
    if (msgEl) { msgEl.textContent = e.message; msgEl.className = 'msg-box msg-err'; }
  }
}

// Toggle enable/disable endpoint
export async function handleEpToggle(epName, currentEnabled) {
  try {
    await api('/dashboard/admin/endpoints/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: epName, action: currentEnabled ? 'disable' : 'enable' }),
    });
    state.rawEndpoints = null;
    loadEp(true);
  } catch (e) { alert('Error: ' + e.message); }
}

export function toggleEpModels(epId) {
  const container = $(`ep-models-container-${epId}`);
  const btn = $(`btn-toggle-ep-${epId}`);
  if (!container || !btn) return;
  const isCollapsed = container.style.display === 'none';
  if (isCollapsed) {
    container.style.display = 'block';
    btn.textContent = '▲ Thu gọn';
  } else {
    container.style.display = 'none';
    const count = btn.getAttribute('data-count') || '';
    btn.textContent = `▶ Hiện (${count} models)`;
  }
}

export async function handleRefreshEpModels(epName) {
  const sanitizedId = epName.replace(/[^a-z0-9]/gi,'_');
  const btn = $(`ep-fetch-btn-${sanitizedId}`);
  const msgEl = $(`ep-pool-msg-${sanitizedId}`);
  if (btn) btn.disabled = true;
  if (msgEl) { msgEl.textContent = '⏳ Fetching...'; msgEl.className = 'msg-box msg-ok'; }
  try {
    const data = await api('/dashboard/admin/endpoints/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: epName }),
    });
    if (msgEl) {
      msgEl.textContent = `✅ Thành công: Cập nhật được ${data.count} models từ Endpoint`;
      msgEl.className = 'msg-box msg-ok';
      setTimeout(() => { if (msgEl) msgEl.textContent = ''; }, 3000);
    }
    state.rawEndpoints = null;
    loadEp(true);
  } catch (e) {
    if (msgEl) { msgEl.textContent = `❌ Lỗi: ${e.message}`; msgEl.className = 'msg-box msg-err'; }
  } finally {
    if (btn) btn.disabled = false;
  }
}



export async function handleKeyPoolAssign(keyName, poolName) {
  try {
    await api('/dashboard/admin/keys/pool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: keyName, pool: poolName }),
    });
    state.rawKeys = [];
    loadKs(true);
  } catch (e) {
    alert('Error: ' + e.message);
  }
}




// ─── Admin actions ────────────────────────────────────────────────
export async function handleAddKey() {
  const input = $('new-api-key-input');
  const key   = input?.value.trim();
  if (!key) return;
  try {
    await api('/dashboard/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key }),
    });
    input.value = '';
    showMsg('add-key-msg', t('msg_key_added'));
    state.rawKeys = [];
    loadKs(true);
  } catch (e) {
    showMsg('add-key-msg', t('msg_key_error') + e.message, true);
  }
}



export async function handleAddEndpoint() {
  const name = $('ep-name-input')?.value.trim();
  const url  = $('ep-url-input')?.value.trim();
  const key  = $('ep-key-input')?.value.trim();
  if (!name || !url) return;
  try {
    await api('/dashboard/endpoints', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, base_url: url, api_key: key || undefined }),
    });
    $('ep-name-input').value = '';
    $('ep-url-input').value  = '';
    $('ep-key-input').value  = '';
    showMsg('add-ep-msg', t('msg_ep_added'));
    state.rawEndpoints = null;
    loadEp(true);
  } catch (e) {
    showMsg('add-ep-msg', t('msg_ep_error') + e.message, true);
  }
}

// ─── [USER] My Account ────────────────────────────────────────────
export async function loadMyAcc(force = false) {
  const el = $('myacc-ct');
  if (!state.rawMyAcc) el.innerHTML = spHtml();
  try {
    if (force || !state.rawMyAcc) {
      const data = await api('/dashboard/me');
      if (!data) return;
      state.rawMyAcc = data;
    }
    const data  = state.rawMyAcc;
    const tier  = data.tier || 'free';
    const webSearchEnabled = data.web_search_enabled === true;
    const flash = data.flash || { rpm:0, tpm:0, rpd:0, rpm_used:0, tpm_used:0, rpd_used:0, rpm_left:0, tpm_left:0, rpd_left:0 };
    const lite  = data.lite  || { rpm:0, tpm:0, rpd:0, rpm_used:0, tpm_used:0, rpd_used:0, rpm_left:0, tpm_left:0, rpd_left:0 };

    const now       = new Date();
    const tomorrow  = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    const diffMs    = tomorrow - now;
    const hrs       = Math.floor(diffMs / 3600000);
    const mins      = Math.floor((diffMs % 3600000) / 60000);
    const resetCountdown = `${hrs}h ${mins}m`;

    const pct  = (used, lim) => Math.min(100, Math.round(((used||0) / (lim||1)) * 100)) || 0;
    const col  = p => p < 50 ? 'var(--emerald)' : p < 80 ? 'var(--amber)' : 'var(--rose)';
    const fRpm = pct(flash.rpm_used, flash.rpm),  fTpm = pct(flash.tpm_used, flash.tpm),  fRpd = pct(flash.rpd_used, flash.rpd);
    const lRpm = pct(lite.rpm_used,  lite.rpm),   lTpm = pct(lite.tpm_used,  lite.tpm),   lRpd = pct(lite.rpd_used,  lite.rpd);

    if ($('myacc-flash-rpm-val')) {
      // Update-only mode (already rendered)
      animateUpdateText('myacc-name', data.name);
      $('myacc-tier').innerHTML = tBadge(tier);
      animateUpdateText('myacc-id', data.account_id || '—');
      animateUpdateText('myacc-flash-rpm-val', `${flash.rpm_used} / ${(flash.rpm||0).toLocaleString()}`);
      updateProgressBar('myacc-flash-rpm-bar', fRpm, col(fRpm));
      animateUpdateText('myacc-flash-rpm-left', flash.rpm_left.toLocaleString());
      animateUpdateText('myacc-flash-rpm-pct',  `${fRpm}%`);
      animateUpdateText('myacc-flash-tpm-val',  `${fmt(flash.tpm_used)} / ${fmt(flash.tpm||0)}`);
      updateProgressBar('myacc-flash-tpm-bar', fTpm, col(fTpm));
      animateUpdateText('myacc-flash-tpm-left', fmt(flash.tpm_left));
      animateUpdateText('myacc-flash-tpm-pct',  `${fTpm}%`);
      animateUpdateText('myacc-flash-rpd-val',  `${flash.rpd_used} / ${fmt(flash.rpd||0)}`);
      updateProgressBar('myacc-flash-rpd-bar', fRpd, col(fRpd));
      animateUpdateText('myacc-flash-rpd-left', flash.rpd_left.toLocaleString());
      animateUpdateText('myacc-lite-rpm-val',   `${lite.rpm_used} / ${(lite.rpm||0).toLocaleString()}`);
      updateProgressBar('myacc-lite-rpm-bar',  lRpm, col(lRpm));
      animateUpdateText('myacc-lite-rpm-left',  lite.rpm_left.toLocaleString());
      animateUpdateText('myacc-lite-rpm-pct',   `${lRpm}%`);
      animateUpdateText('myacc-lite-tpm-val',   `${fmt(lite.tpm_used)} / ${fmt(lite.tpm||0)}`);
      updateProgressBar('myacc-lite-tpm-bar',  lTpm, col(lTpm));
      animateUpdateText('myacc-lite-tpm-left',  fmt(lite.tpm_left));
      animateUpdateText('myacc-lite-tpm-pct',   `${lTpm}%`);
      animateUpdateText('myacc-lite-rpd-val',   `${lite.rpd_used} / ${fmt(lite.rpd||0)}`);
      updateProgressBar('myacc-lite-rpd-bar',  lRpd, col(lRpd));
      animateUpdateText('myacc-lite-rpd-left',  lite.rpd_left.toLocaleString());
      document.querySelectorAll('#myacc-ct .rpd-reset-countdown').forEach(el => {
        el.setAttribute('data-tomorrow-ts', Math.floor(tomorrow.getTime() / 1000));
        el.textContent = resetCountdown;
      });
      if (data.flash_pool) {
        animateUpdateText('myacc-flash-rpd',  `${fmt(data.flash_pool.rpd_left)} / ${fmt(data.flash_pool.rpd_limit)}`);
        animateUpdateText('myacc-flash-1h',   `${fmt(data.flash_pool.tokens_1h_left)} / ${fmt(data.flash_pool.tokens_1h_limit)}`);
        animateUpdateText('myacc-flash-12h',  `${fmt(data.flash_pool.tokens_12h_left)} / ${fmt(data.flash_pool.tokens_12h_limit)}`);
        animateUpdateText('myacc-flash-24h',  `${fmt(data.flash_pool.tokens_24h_left)} / ${fmt(data.flash_pool.tokens_24h_limit)}`);
      }
      if (data.lite_pool) {
        animateUpdateText('myacc-lite-rpd',   `${fmt(data.lite_pool.rpd_left)} / ${fmt(data.lite_pool.rpd_limit)}`);
        animateUpdateText('myacc-lite-1h',    `${fmt(data.lite_pool.tokens_1h_left)} / ${fmt(data.lite_pool.tokens_1h_limit)}`);
        animateUpdateText('myacc-lite-12h',   `${fmt(data.lite_pool.tokens_12h_left)} / ${fmt(data.lite_pool.tokens_12h_limit)}`);
        animateUpdateText('myacc-lite-24h',   `${fmt(data.lite_pool.tokens_24h_left)} / ${fmt(data.lite_pool.tokens_24h_limit)}`);
      }
      const wsToggle = $('ws-toggle-input');
      if (wsToggle) wsToggle.checked = webSearchEnabled;
      const avatarEl = $('myacc-avatar');
      if (avatarEl) avatarEl.textContent = data.name.substring(0, 2).toUpperCase();
    } else {
      el.innerHTML = _renderMyAccHtml(data, tier, webSearchEnabled, flash, lite, fRpm, fTpm, fRpd, lRpm, lTpm, lRpd, col, resetCountdown, tomorrow);
    }
    document.querySelectorAll('#myacc-ct [data-tip-i18n]').forEach(el => {
      el.setAttribute('data-tip', t(el.getAttribute('data-tip-i18n')));
    });
  } catch (e) {
    el.innerHTML = `<p style="color:var(--rose);padding:20px">${t('load_error')}: ${e.message}</p>`;
  }
}

function _limitCard(title, id, val, used, pct, col, left, leftId, pctId, icon, extra = '') {
  const type = id.split('-').filter(p => ['rpm', 'tpm', 'rpd'].includes(p))[0] || '';
  return `
    <div class="dlim sc-anim">
      <div class="dlim-h">
        <span class="dlim-t tooltip" data-tip-i18n="tip_${type}" data-tip="">${title}</span>
        <span style="color:${col(pct)};display:flex">${icon}</span>
      </div>
      <div class="dlim-v num-animate" id="${id}">${val}</div>
      <div class="dlim-progress"><div class="dlim-bar" id="${id}-bar" style="width:${pct}%;background:${col(pct)}"></div></div>
      <div class="dlim-info">
        <div class="dlim-info-item">${t('lbl_left')} <span id="${leftId}" style="color:${col(pct)}">${left}</span></div>
        <div class="dlim-info-item">${t('lbl_using')} <span id="${pctId}">${pct}%</span></div>
        ${extra}
      </div>
    </div>`;
}

const _SVG_BOLT   = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>`;
const _SVG_TYPE   = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px"><polyline points="4 7 4 4 20 4 20 7"></polyline><line x1="9" y1="20" x2="15" y2="20"></line><line x1="12" y1="4" x2="12" y2="20"></line></svg>`;
const _SVG_CAL    = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`;

function _renderMyAccHtml(data, tier, webSearchEnabled, flash, lite, fRpm, fTpm, fRpd, lRpm, lTpm, lRpd, col, resetCountdown, tomorrow) {
  const tomorrowTs = Math.floor(tomorrow.getTime() / 1000);
  const poolSection = (label, color, iconPath, poolData, prefix) => {
    if (!poolData) return '';
    return `
      <div class="pool-summary-card" style="border-color:${color}20">
        <div class="pool-summary-head">
          <div style="display:flex;align-items:center;gap:10px">
            <span style="color:${color};display:flex"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">${iconPath}</svg></span>
            <span style="font-weight:700;color:${color};font-size:14px">${label}</span>
          </div>
          <span class="b" style="background:${color}18;color:${color};border:1px solid ${color}30"><span class="hd hg"></span>${t('opt_active')}</span>
        </div>
        <div class="drow"><div class="drow-l">${t('lbl_pool_rpd')}</div><div class="drow-v num-animate" id="${prefix}-rpd" style="color:${color}">${fmt(poolData.rpd_left)} / ${fmt(poolData.rpd_limit)}</div></div>
        <div class="drow"><div class="drow-l">${t('lbl_pool_1h')}</div><div class="drow-v num-animate" id="${prefix}-1h" style="color:var(--emerald)">${fmt(poolData.tokens_1h_left)} / ${fmt(poolData.tokens_1h_limit)}</div></div>
        <div class="drow"><div class="drow-l">${t('lbl_pool_12h')}</div><div class="drow-v num-animate" id="${prefix}-12h" style="color:var(--amber)">${fmt(poolData.tokens_12h_left)} / ${fmt(poolData.tokens_12h_limit)}</div></div>
        <div class="drow"><div class="drow-l">${t('lbl_pool_24h')}</div><div class="drow-v num-animate" id="${prefix}-24h" style="color:var(--primary)">${fmt(poolData.tokens_24h_left)} / ${fmt(poolData.tokens_24h_limit)}</div></div>
      </div>`;
  };

  return `
    <div style="max-width:100%">
      <div class="myacc-profile-card sc-anim">
        <div id="myacc-avatar" class="myacc-avatar">${data.name.substring(0,2).toUpperCase()}</div>
        <div style="flex:1">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
            <h2 id="myacc-name" style="font-size:18px;font-weight:800;letter-spacing:-0.3px;color:var(--text);margin:0">${data.name}</h2>
            <span id="myacc-tier">${tBadge(tier)}</span>
          </div>
          <div style="font-size:11px;color:var(--text-muted);font-family:monospace;display:flex;align-items:center;gap:6px">
            <span>${t('lbl_account_id')}</span>
            <span id="myacc-id" style="color:var(--primary)">${data.account_id || '—'}</span>
          </div>
        </div>
      </div>

      <div class="ws-toggle-wrap sc-anim" style="margin-bottom:20px">
        <label class="toggle-switch" for="ws-toggle-input">
          <input type="checkbox" id="ws-toggle-input" ${webSearchEnabled ? 'checked' : ''} onchange="window._toggleWebSearch(this.checked)">
          <span class="toggle-slider"></span>
        </label>
        <div>
          <label class="ws-toggle-label" for="ws-toggle-input">
            ${t('ws_toggle_label')}
            <small>${t('ws_toggle_sub')}</small>
          </label>
        </div>
        <span class="ws-toggle-tip" data-tip="${t('ws_toggle_tip')}">?</span>
      </div>

      <div class="limit-section-header"><h3>Gemini Flash Pool — ${t('lbl_usage_limits')}</h3><p>${t('lbl_usage_limits_sub')}</p></div>
      <div class="cards" style="grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px">
        ${_limitCard(t('lbl_rpm_card'), 'myacc-flash-rpm-val', `${flash.rpm_used} / ${(flash.rpm||0).toLocaleString()}`, flash.rpm_used, fRpm, col, flash.rpm_left.toLocaleString(), 'myacc-flash-rpm-left', 'myacc-flash-rpm-pct', _SVG_BOLT)}
        ${_limitCard(t('lbl_tpm_card'), 'myacc-flash-tpm-val', `${fmt(flash.tpm_used)} / ${fmt(flash.tpm||0)}`, flash.tpm_used, fTpm, col, fmt(flash.tpm_left), 'myacc-flash-tpm-left', 'myacc-flash-tpm-pct', _SVG_TYPE)}
        ${_limitCard(t('lbl_rpd_card'), 'myacc-flash-rpd-val', `${flash.rpd_used} / ${fmt(flash.rpd||0)}`, flash.rpd_used, fRpd, col, flash.rpd_left.toLocaleString(), 'myacc-flash-rpd-left', '', _SVG_CAL,
          `<div class="dlim-info-item">${t('lbl_reset')} <span class="rpd-reset-countdown" data-tomorrow-ts="${tomorrowTs}">${resetCountdown}</span></div>`)}
      </div>

      <div class="limit-section-header" style="margin-top:24px"><h3>Gemini Flash Lite Pool — ${t('lbl_usage_limits')}</h3><p>${t('lbl_usage_limits_sub')}</p></div>
      <div class="cards" style="grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:28px">
        ${_limitCard(t('lbl_rpm_card'), 'myacc-lite-rpm-val', `${lite.rpm_used} / ${(lite.rpm||0).toLocaleString()}`, lite.rpm_used, lRpm, col, lite.rpm_left.toLocaleString(), 'myacc-lite-rpm-left', 'myacc-lite-rpm-pct', _SVG_BOLT)}
        ${_limitCard(t('lbl_tpm_card'), 'myacc-lite-tpm-val', `${fmt(lite.tpm_used)} / ${fmt(lite.tpm||0)}`, lite.tpm_used, lTpm, col, fmt(lite.tpm_left), 'myacc-lite-tpm-left', 'myacc-lite-tpm-pct', _SVG_TYPE)}
        ${_limitCard(t('lbl_rpd_card'), 'myacc-lite-rpd-val', `${lite.rpd_used} / ${fmt(lite.rpd||0)}`, lite.rpd_used, lRpd, col, lite.rpd_left.toLocaleString(), 'myacc-lite-rpd-left', '', _SVG_CAL,
          `<div class="dlim-info-item">${t('lbl_reset')} <span class="rpd-reset-countdown" data-tomorrow-ts="${tomorrowTs}">${resetCountdown}</span></div>`)}
      </div>

      <div class="limit-section-header" style="margin-top:32px"><h3>${t('lbl_key_pools')}</h3><p>${t('lbl_key_pools_sub')}</p></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        ${poolSection('Gemini Flash Pool', 'var(--cyan)', '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>', data.flash_pool, 'myacc-flash')}
        ${poolSection('Gemini Flash Lite Pool', 'var(--emerald)', '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>', data.lite_pool, 'myacc-lite')}
      </div>
    </div>`;
}

// ─── [USER] My Usage ──────────────────────────────────────────────
export async function loadMyUse(force = false) {
  if (!state.myStatsData) {
    $('myuse-cards').innerHTML = '';
    $('myuse-tb').innerHTML = `<tr><td colspan="5">${spHtml()}</td></tr>`;
  }
  try {
    if (force || !state.myStatsData) {
      const data = await api('/dashboard/my-stats?days=30');
      if (!data) return;
      state.myStatsData = data;
    }
    const s   = state.myStatsData.summary || [];
    const d   = state.myStatsData.daily   || [];
    const now = new Date();
    const td  = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
    const tdd = d.filter(x => x.d === td);
    const tdt = tdd.reduce((a, b) => a + (b.t || 0), 0);
    const tdr = tdd.reduce((a, b) => a + (b.req || 0), 0);
    const tot = s.reduce((a, b) => a + (b.t || 0), 0);
    const req = s.reduce((a, b) => a + (b.req || 0), 0);
    const sav = state.myStatsData.savings?.savings || 0;

    if ($('my-card-today-tokens')) {
      animateUpdateText('my-card-today-tokens', fmt(tdt));
      animateUpdateText('my-card-today-reqs',   `${tdr.toLocaleString()} ${t('requests_count')}`);
      animateUpdateText('my-card-30d-tokens',   fmt(tot));
      animateUpdateText('my-card-30d-reqs',     `${req.toLocaleString()} ${t('requests_count')}`);
      animateUpdateText('my-card-active-models', s.length.toString());
      setTimeout(() => animateSavings(sav, 'my'), 100);
    } else {
      $('myuse-cards').innerHTML = statsCards(s, d, state.myStatsData.savings, 'my');
    }

    $('myuse-tb').innerHTML = statsTable(s);

    const ld = buildDS(d);
    if (state.ch.myday && ld.labels.length) {
      updateLineChart(state.ch.myday, ld.labels, ld.datasets);
    } else if (ld.labels.length) {
      state.ch.myday?.destroy();
      state.ch.myday = mkLine($('cMyDay').getContext('2d'), ld.labels, ld.datasets);
    }
    if (state.ch.mymod && s.length) {
      updateDonutChart(state.ch.mymod, s.map(x => x.model_alias), s.map(x => x.t || 0));
    } else if (s.length) {
      state.ch.mymod?.destroy();
      state.ch.mymod = mkDonut($('cMyMod').getContext('2d'), s.map(x => x.model_alias), s.map(x => x.t || 0));
    }
  } catch (e) {
    $('myuse-tb').innerHTML = `<tr><td colspan="5" style="color:var(--rose);padding:16px">${t('load_error')}: ${e.message}</td></tr>`;
  }
}

// ─── Helpers ─────────────────────────────────────────────────────
function _ch(key) { return state.ch[key]; }
