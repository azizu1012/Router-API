import { t } from '../i18n.js';

export function renderMu() {
  return `
    <div class="ptit">${t('mu_title')}</div>
    <div class="psub">${t('mu_sub')}</div>
    <div class="cards" style="grid-template-columns:repeat(4,1fr)">
      <div class="sc cp sc-anim">
        <div class="sc-lb">${t('mu_std_cost')}</div>
        <div class="sc-v" id="std-cost-val">$0.0000</div>
        <div class="sc-s">${t('mu_std_cost_sub')}</div>
      </div>
      <div class="sc cc sc-anim" style="animation-delay:.05s;border-color:rgba(6,182,212,.35)">
        <div class="sc-lb">${t('mu_cache_cost')}</div>
        <div class="sc-v" id="cache-cost-val" style="color:var(--cyan)">$0.0000</div>
        <div class="sc-s">${t('mu_cache_cost_sub')}</div>
      </div>
      <div class="sc ca sc-anim" style="animation-delay:.1s;border-color:rgba(245,158,11,.35)">
        <div class="sc-lb">${t('mu_gemini_cost')}</div>
        <div class="sc-v" id="gemini-cost-val" style="color:var(--amber)">$0.0000</div>
        <div class="sc-s">${t('mu_gemini_cost_sub')}</div>
      </div>
      <div class="sc cg sc-anim" style="animation-delay:.15s;border-color:rgba(16,185,129,.35)">
        <div class="sc-lb">${t('mu_net_save')}</div>
        <div class="sc-v" id="save-cost-val" style="color:var(--emerald)">$0.0000</div>
        <div class="sc-s">${t('mu_net_save_sub')}</div>
      </div>
    </div>
    <div class="tcd" style="margin-top:24px">
      <div class="tcdh"><h3>${t('mu_table_title')}</h3></div>
      <div class="tscr">
        <table>
          <thead><tr>
            <th>${t('th_pool_name')}</th>
            <th>${t('th_pool_backing')}</th>
            <th>${t('th_pool_rpm')}</th>
            <th>${t('th_pool_tpm')}</th>
            <th>${t('th_pool_status')}</th>
          </tr></thead>
          <tbody id="mu-tb"></tbody>
        </table>
      </div>
    </div>`;
}
