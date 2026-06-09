import { t } from '../i18n.js';

export function renderEp() {
  return `
    <div class="ptit">${t('ep_title')}</div>
    <div class="psub">${t('ep_sub')}</div>

    <div class="admin-actions-card">
      <div class="action-box" style="width:100%">
        <h4>${t('ep_add_title')}</h4>
        <div class="action-input-group" style="display:flex;gap:12px;flex-wrap:wrap">
          <input type="text" id="ep-name-input" placeholder="my-custom-api" class="text-input" style="flex:1;min-width:150px">
          <input type="text" id="ep-url-input" placeholder="https://api.example.com/v1" class="text-input" style="flex:2;min-width:250px">
          <input type="password" id="ep-key-input" placeholder="API Key (admin giữ)" class="text-input" style="flex:1.5;min-width:200px">
          <button class="btn btn-primary" onclick="handleAddEndpoint()" style="min-width:90px">${t('btn_add')}</button>
        </div>
        <div id="add-ep-msg" class="msg-box"></div>
      </div>
    </div>

    <div class="tcd">
      <div class="tcdh">
        <h3>${t('ep_list_title')}</h3>
        <span id="ep-cnt" style="font-size:12px;color:var(--text-muted)"></span>
      </div>
      <div id="ep-list-container">
        <div style="text-align:center;padding:24px;color:var(--text-dark)">Loading…</div>
      </div>
    </div>`;
}
