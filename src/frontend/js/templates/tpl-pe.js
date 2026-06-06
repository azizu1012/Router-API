import { t } from '../i18n.js';

export function renderPe() {
  return `
    <div class="ptit">${t('pe_title')}</div>
    <div class="psub">${t('pe_sub')}</div>
    <div id="pe-ct">
      <div class="ld"><div class="sp"></div>${t('loading')}</div>
    </div>`;
}
