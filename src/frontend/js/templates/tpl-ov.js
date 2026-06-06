import { t } from '../i18n.js';

export function renderOv() {
  return `
    <div class="ptit">${t('ov_title')}</div>
    <div class="psub">${t('ov_sub')}</div>
    <div class="cards" id="ov-cards">
      <div class="ld"><div class="sp"></div>${t('loading')}</div>
    </div>
    <div class="crow">
      <div class="cbox">
        <h3>${t('ov_daily_stats')}</h3>
        <div class="cwrap"><canvas id="cDay"></canvas></div>
      </div>
      <div class="cbox">
        <h3>${t('ov_model_ratio')}</h3>
        <div class="cwrap"><canvas id="cMod"></canvas></div>
      </div>
    </div>
    <div class="tcd">
      <div class="tcdh"><h3>${t('ov_model_detail')}</h3></div>
      <div class="tscr">
        <table>
          <thead><tr>
            <th class="tooltip" data-tip="${t('tip_model_alias')}">${t('th_model')}</th>
            <th class="tooltip" data-tip="${t('tip_prompt_tokens')}">${t('th_prompt_tokens')}</th>
            <th class="tooltip" data-tip="${t('tip_completion_tokens')}">${t('th_completion_tokens')}</th>
            <th class="tooltip" data-tip="${t('tip_total_tokens')}">${t('th_total_tokens')}</th>
            <th class="tooltip" data-tip="${t('tip_requests')}">${t('th_requests')}</th>
          </tr></thead>
          <tbody id="ov-tb"></tbody>
        </table>
      </div>
    </div>`;
}
