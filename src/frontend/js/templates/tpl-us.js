import { t } from '../i18n.js';

export function renderUs() {
  return `
    <div class="ptit">${t('us_title')}</div>
    <div class="psub">${t('us_sub')}</div>
    <div class="tcd">
      <div class="tcdh"><h3>${t('us_rank_title')}</h3></div>
      <div id="us-ct" style="padding:20px"></div>
    </div>`;
}
