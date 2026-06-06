import { state, CLR, $ } from './state.js';
import { t } from './i18n.js';
import { fmt } from './utils.js';

// ─── Line Chart Builder ───────────────────────────────────────────
export function mkLine(ctx, labels, ds) {
  const style = window.getComputedStyle(document.documentElement);
  const textMuted = style.getPropertyValue('--text-muted').trim() || '#9ca3af';
  const textDark  = style.getPropertyValue('--text-dark').trim()  || '#6b7280';
  const border    = style.getPropertyValue('--border').trim()      || 'rgba(255,255,255,.03)';

  return new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: ds },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 600, easing: 'easeInOutCubic' },
      plugins: {
        legend: {
          labels: {
            color: textMuted,
            boxWidth: 8,
            padding: 8,
            font: { size: 10, family: 'Inter' },
          },
        },
        tooltip: {
          backgroundColor: 'rgba(9,13,22,0.95)',
          borderColor: 'rgba(255,255,255,0.08)',
          borderWidth: 1,
          titleFont: { family: 'Inter', size: 12 },
          bodyFont: { family: 'Inter', size: 11 },
          padding: 10,
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${fmt(ctx.parsed.y)}`
          }
        },
      },
      scales: {
        x: {
          ticks: { color: textDark, maxTicksLimit: 7, maxRotation: 30, font: { size: 9, family: 'Inter' } },
          grid: { color: border },
        },
        y: {
          ticks: { color: textDark, callback: v => fmt(v), font: { size: 9, family: 'Inter' } },
          grid: { color: border },
        },
      },
    },
  });
}

// ─── Donut Chart Builder ──────────────────────────────────────────
export function mkDonut(ctx, labels, data) {
  const style = window.getComputedStyle(document.documentElement);
  const textMuted = style.getPropertyValue('--text-muted').trim() || '#9ca3af';

  return new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: CLR.slice(0, data.length),
        borderWidth: 0,
        hoverOffset: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { animateRotate: true, duration: 700, easing: 'easeOutQuart' },
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: textMuted,
            boxWidth: 8,
            padding: 6,
            font: { size: 10, family: 'Inter' },
          },
        },
        tooltip: {
          backgroundColor: 'rgba(9,13,22,0.95)',
          borderColor: 'rgba(255,255,255,0.08)',
          borderWidth: 1,
          callbacks: {
            label: ctx => ` ${ctx.label}: ${fmt(ctx.parsed)}`
          }
        },
      },
      cutout: '70%',
    },
  });
}

// ─── Build dataset from daily data ───────────────────────────────
export function buildDS(d) {
  const labels  = [...new Set(d.map(x => x.d))].sort();
  const models  = [...new Set(d.map(x => x.model_alias))];
  return {
    labels,
    datasets: models.map((m, i) => ({
      label: m,
      data: labels.map(l => {
        const f = d.find(x => x.d === l && x.model_alias === m);
        return f ? f.t : 0;
      }),
      borderColor: CLR[i % CLR.length],
      backgroundColor: CLR[i % CLR.length] + '14',
      fill: true,
      tension: 0.4,
      pointRadius: 2,
      pointHoverRadius: 5,
      borderWidth: 2,
    })),
  };
}

// ─── Chart updaters (no flicker) ─────────────────────────────────
export function updateLineChart(chart, labels, datasets) {
  chart.data.labels = labels;
  datasets.forEach((newDs, i) => {
    if (chart.data.datasets[i]) {
      Object.assign(chart.data.datasets[i], newDs);
    } else {
      chart.data.datasets.push(newDs);
    }
  });
  if (chart.data.datasets.length > datasets.length) {
    chart.data.datasets.splice(datasets.length);
  }
  chart.update('active');
}

export function updateDonutChart(chart, labels, data) {
  chart.data.labels = labels;
  chart.data.datasets[0].data = data;
  chart.data.datasets[0].backgroundColor = CLR.slice(0, data.length);
  chart.update('active');
}

// ─── Animated savings ticker ──────────────────────────────────────
export function animateSavings(targetVal, prefix) {
  const el = $(`${prefix}-ticking-savings`);
  if (!el) return;

  let startVal = 0.0;
  const currentText = el.textContent;
  if (currentText?.startsWith('$')) {
    const parsed = parseFloat(currentText.substring(1));
    if (!isNaN(parsed) && parsed > 0) startVal = parsed;
  }

  if (Math.abs(targetVal - startVal) < 0.0001) {
    el.textContent = `$${targetVal.toFixed(4)}`;
    _startTicker(el, targetVal, prefix);
    return;
  }

  let currentVal = startVal;
  const duration = 1200;
  const start = performance.now();

  function update(time) {
    const progress = Math.min((time - start) / duration, 1);
    currentVal = startVal + (targetVal - startVal) * progress;
    el.textContent = `$${currentVal.toFixed(4)}`;
    if (progress < 1) {
      requestAnimationFrame(update);
    } else {
      el.textContent = `$${targetVal.toFixed(4)}`;
      _startTicker(el, targetVal, prefix);
    }
  }
  requestAnimationFrame(update);
}

function _startTicker(el, val, prefix) {
  const key = 'savingsInterval_' + prefix;
  if (window[key]) clearInterval(window[key]);
  window[key] = setInterval(() => {
    val += Math.random() * 0.0001;
    el.textContent = `$${val.toFixed(4)}`;
  }, 3500);
}

// ─── Stats summary cards ──────────────────────────────────────────
export function statsCards(s, d, savings, prefix) {
  const tot = s.reduce((a, b) => a + (b.t || 0), 0);
  const req = s.reduce((a, b) => a + (b.req || 0), 0);
  const now = new Date();
  const td  = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  const tdd = d.filter(x => x.d === td);
  const tdt = tdd.reduce((a, b) => a + (b.t || 0), 0);
  const tdr = tdd.reduce((a, b) => a + (b.req || 0), 0);
  const sav = savings?.savings || 0;

  setTimeout(() => animateSavings(sav, prefix), 100);

  const icon = (path) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px">${path}</svg>`;

  return `
    <div class="sc cp sc-anim">
      <div class="sc-header">
        <div class="sc-lb">${t('today')}</div>
        <div class="sc-icon" style="color:var(--primary)">${icon('<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>')}</div>
      </div>
      <div class="sc-v num-animate" id="${prefix}-card-today-tokens">${fmt(tdt)}</div>
      <div class="sc-s" id="${prefix}-card-today-reqs">${tdr.toLocaleString()} ${t('requests_count')}</div>
    </div>
    <div class="sc cc sc-anim" style="animation-delay:0.05s">
      <div class="sc-header">
        <div class="sc-lb">${t('last_30_days')}</div>
        <div class="sc-icon" style="color:var(--cyan)">${icon('<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>')}</div>
      </div>
      <div class="sc-v num-animate" id="${prefix}-card-30d-tokens">${fmt(tot)}</div>
      <div class="sc-s" id="${prefix}-card-30d-reqs">${req.toLocaleString()} ${t('requests_count')}</div>
    </div>
    <div class="sc cg sc-anim" style="animation-delay:0.1s">
      <div class="sc-header">
        <div class="sc-lb">${t('savings_amt')}</div>
        <div class="sc-icon" style="color:var(--emerald)">${icon('<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>')}</div>
      </div>
      <div class="sc-v" id="${prefix}-ticking-savings" style="color:var(--emerald)">$0.0000</div>
      <div class="sc-s">${t('saved_vs_claude')}</div>
    </div>
    <div class="sc ca sc-anim" style="animation-delay:0.15s">
      <div class="sc-header">
        <div class="sc-lb">${t('active_models')}</div>
        <div class="sc-icon" style="color:var(--amber)">${icon('<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>')}</div>
      </div>
      <div class="sc-v num-animate" id="${prefix}-card-active-models">${s.length}</div>
      <div class="sc-s">${t('gemini_supported')}</div>
    </div>
  `;
}

// ─── Stats data table rows ────────────────────────────────────────
export function statsTable(s) {
  return s.map((r, i) => `
    <tr class="tr-anim" style="animation-delay:${i * 0.03}s">
      <td><strong>${r.model_alias}</strong></td>
      <td>${fmt(r.p || 0)}</td>
      <td>${fmt(r.c || 0)}</td>
      <td style="font-weight:700">${fmt(r.t || 0)}</td>
      <td>${(r.req || 0).toLocaleString()}</td>
    </tr>
  `).join('') || `<tr><td colspan="5" class="empty-row">${t('no_data')}</td></tr>`;
}
