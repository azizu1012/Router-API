import { t } from '../i18n.js';

export function renderAc() {
  return `
    <div class="ptit">${t('ac_title')}</div>
    <div class="psub">${t('ac_sub')}</div>

    <div class="filter-panel">
      <div class="search-input-group">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        <input type="text" id="ac-search" placeholder="${t('placeholder_search_accounts')}" oninput="filterAccounts()">
      </div>
      <div class="filter-select-group">
        <div class="filter-field">
          <label>${t('lbl_tier')}</label>
          <select id="ac-filter-tier" onchange="filterAccounts()">
            <option value="all">${t('opt_all')}</option>
            <option value="admin">${t('opt_admin')}</option>
            <option value="premium">${t('opt_premium')}</option>
            <option value="free">${t('opt_free')}</option>
          </select>
        </div>
        <div class="filter-field">
          <label>${t('lbl_status')}</label>
          <select id="ac-filter-status" onchange="filterAccounts()">
            <option value="all">${t('opt_all')}</option>
            <option value="active">${t('opt_active')}</option>
            <option value="disabled">${t('opt_disabled')}</option>
          </select>
        </div>
      </div>
    </div>

    <div class="cards" id="ac-cards"></div>

    <div class="admin-actions-card">
      <div class="action-box" style="width:100%">
        <h4>Tạo tài khoản con mới</h4>
        <div class="action-input-group" style="display:flex;gap:12px;flex-wrap:wrap">
          <input type="text" id="ac-new-name" placeholder="Tên tài khoản" class="text-input" style="flex:1.5;min-width:150px">
          <select id="ac-new-tier" style="flex:1;min-width:120px">
            <option value="free">Free Tier</option>
            <option value="premium">Premium Tier</option>
            <option value="admin">Admin Tier</option>
          </select>
          <input type="number" id="ac-new-rpm" placeholder="RPM (Mặc định)" class="text-input" style="flex:1;min-width:100px">
          <input type="number" id="ac-new-tpm" placeholder="TPM (Mặc định)" class="text-input" style="flex:1.2;min-width:120px">
          <input type="number" id="ac-new-rpd" placeholder="RPD (Mặc định)" class="text-input" style="flex:1.2;min-width:120px">
          <button class="btn btn-primary" onclick="handleCreateAccount()" style="min-width:90px">${t('btn_add')}</button>
        </div>
        <div id="add-ac-msg" class="msg-box"></div>
      </div>
    </div>

    <div class="tcd">
      <div class="tcdh">
        <h3>${t('ac_list_title')}</h3>
        <span id="ac-cnt" style="font-size:12px;color:var(--text-muted)"></span>
      </div>
      <div class="tscr">
        <table>
          <thead><tr>
            <th class="tooltip" data-tip="${t('tip_account_name')}">${t('th_account_name')}</th>
            <th style="min-width:260px">${t('login_label')}</th>
            <th class="tooltip" data-tip="${t('tip_tier')}">${t('th_tier')}</th>
            <th class="tooltip" data-tip="${t('tip_account_status')}">${t('th_status')}</th>
            <th class="tooltip" data-tip="${t('tip_rpm')}">${t('th_rpm')}</th>
            <th class="tooltip" data-tip="${t('tip_tpm')}">${t('th_tpm')}</th>
            <th class="tooltip" data-tip="${t('tip_rpd')}">${t('th_rpd')}</th>
            <th class="tooltip" data-tip="${t('tip_created')}">${t('th_created')}</th>
            <th style="width:160px">${t('th_actions')}</th>
          </tr></thead>
          <tbody id="ac-tb"></tbody>
        </table>
      </div>
    </div>`;
}
