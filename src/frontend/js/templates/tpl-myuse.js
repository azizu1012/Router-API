import { t } from '../i18n.js';

export function renderMyUse() {
  return `
    <div class="ptit">${t('myuse_title')}</div>
    <div class="psub">${t('myuse_sub')}</div>
    <div class="cards" id="myuse-cards"></div>
    <div class="crow">
      <div class="cbox">
        <h3>${t('myuse_daily_chart')}</h3>
        <div class="cwrap"><canvas id="cMyDay"></canvas></div>
      </div>
      <div class="cbox">
        <h3>${t('myuse_model_chart')}</h3>
        <div class="cwrap"><canvas id="cMyMod"></canvas></div>
      </div>
    </div>
    <div class="tcd">
      <div class="tcdh"><h3>${t('myuse_detail_title')}</h3></div>
      <div class="tscr">
        <table>
          <thead><tr>
            <th>${t('th_model')}</th>
            <th>${t('th_prompt_tokens')}</th>
            <th>${t('th_completion_tokens')}</th>
            <th>${t('th_total_tokens')}</th>
            <th>${t('th_requests')}</th>
          </tr></thead>
          <tbody id="myuse-tb"></tbody>
        </table>
      </div>
    </div>`;
}
