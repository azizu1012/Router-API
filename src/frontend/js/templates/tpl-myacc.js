import { t } from '../i18n.js';

export function renderMyAcc() {
  return `
    <div class="ptit">${t('myacc_title')}</div>
    <div class="psub">${t('myacc_sub')}</div>
    <div id="myacc-ct">
      <div class="ld"><div class="sp"></div>${t('loading')}</div>
    </div>`;
}
