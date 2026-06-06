import { t } from '../i18n.js';

export function renderKs() {
  return `
    <div class="ptit">${t('ks_title')}</div>
    <div class="psub">${t('ks_sub')}</div>

    <div class="filter-panel">
      <div class="search-input-group">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        <input type="text" id="ks-search" placeholder="${t('placeholder_search_keys')}" oninput="filterKeys()">
      </div>
      <div class="filter-select-group">
        <div class="filter-field">
          <label>${t('lbl_tier')}</label>
          <select id="ks-filter-tier" onchange="filterKeys()">
            <option value="all">${t('opt_all')}</option>
            <option value="admin">${t('opt_admin')}</option>
            <option value="premium">${t('opt_premium')}</option>
            <option value="free">${t('opt_free')}</option>
          </select>
        </div>
        <div class="filter-field">
          <label>${t('lbl_status')}</label>
          <select id="ks-filter-status" onchange="filterKeys()">
            <option value="all">${t('opt_all')}</option>
            <option value="healthy">${t('opt_healthy')}</option>
            <option value="frozen">${t('opt_cooldown')}</option>
            <option value="degraded">${t('opt_degraded')}</option>
            <option value="disabled">${t('opt_disabled')}</option>
          </select>
        </div>
      </div>
    </div>

    <div class="cards" id="ks-cards"></div>

    <div class="admin-actions-card">
      <div class="admin-actions-grid">
        <div class="action-box">
          <h4>${t('ks_add_key_title')}</h4>
          <div class="action-input-group">
            <input type="text" id="new-api-key-input" placeholder="AIzaSy..." autocomplete="off" class="text-input">
            <button class="btn btn-primary" onclick="handleAddKey()">${t('btn_add')}</button>
          </div>
          <div id="add-key-msg" class="msg-box"></div>
        </div>
      </div>
    </div>

    <div class="tcd">
      <div class="tcdh">
        <h3>${t('ks_list_title')}</h3>
        <span id="ks-cnt" style="font-size:12px;color:var(--text-muted)"></span>
      </div>
      <div class="tscr">
        <table>
          <thead><tr>
            <th class="tooltip" data-tip="${t('tip_key_code')}">${t('th_key_code')}</th>
            <th class="tooltip" data-tip="${t('tip_tier')}">${t('th_tier')}</th>
            <th class="tooltip" data-tip="${t('tip_status')}">${t('th_status')}</th>
            <th class="tooltip" data-tip="${t('tip_today')}">${t('th_today')}</th>
            <th class="tooltip" data-tip="${t('tip_total')}">${t('th_total')}</th>
            <th class="tooltip" data-tip="${t('tip_concurrency')}">${t('th_concurrency')}</th>
            <th class="tooltip" data-tip="${t('tip_failures')}">${t('th_failures')}</th>
            <th>Pool</th>
            <th style="width:80px">${t('th_actions')}</th>
          </tr></thead>
          <tbody id="ks-tb"></tbody>
        </table>
      </div>
    </div>`;
}
