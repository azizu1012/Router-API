import { useEffect, useState, useMemo } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Title, Tooltip, Legend, ArcElement
} from 'chart.js';
import { Line, Doughnut } from 'react-chartjs-2';
import { Calendar, BarChart3, TrendingUp, Cpu, Wifi, WifiOff, Clock, Table2, LineChart } from 'lucide-react';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import Button from '../components/Button';
import { useThemeTokens } from '../utils/themeTokens';

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, ArcElement
);

/* Relative time in the reader's language. The unit strings live in i18n so a
   Japanese operator reads 分前, not "m trước". */
function timeAgo(timestamp, lang) {
  if (!timestamp) return '-';
  const diff = Math.floor((Date.now() - new Date(timestamp)) / 1000);
  if (diff < 5) return t('time_just_now', lang);
  if (diff < 60) return `${diff}${t('time_ago_s', lang)}`;
  if (diff < 3600) return `${Math.floor(diff / 60)}${t('time_ago_m', lang)}`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}${t('time_ago_h', lang)}`;
  return `${Math.floor(diff / 86400)}${t('time_ago_d', lang)}`;
}

/* Re-renders on a slow tick so "12s trước" does not stick at 12s forever. */
function TimeAgo({ timestamp, lang }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick(n => n + 1), 2000);
    return () => clearInterval(timer);
  }, []);
  return <span>{timeAgo(timestamp, lang)}</span>;
}

export default function OverviewTab() {
  const { tabData, lang, refreshTab, wsHook } = useApp();
  const ovData = tabData.ov;
  const tk = useThemeTokens();

  const [flipped, setFlipped] = useState(false);
  const [liveStats, setLiveStats] = useState(null);
  const [showTable, setShowTable] = useState(false);

  const recentRequests = liveStats?.recent_requests || ovData?.recent_requests || [];
  // Derived, not mirrored: the figure is whatever the last poll reported.
  const savings = ovData?.savings?.net_savings ?? 0;

  useEffect(() => {
    if (!wsHook || !wsHook.connected) return;
    const unsub = wsHook.subscribe('stats:overview', msg => {
      if (msg.type === 'stats_snapshot') setLiveStats(msg);
    });
    return unsub;
  }, [wsHook]);

  /* Entity → slot. Sorted by alias so a model keeps its colour across polls,
     across filter changes, and regardless of how the API orders the rows. */
  const slotOf = useMemo(() => {
    const names = [...new Set([
      ...(ovData?.summary || []).map(r => r.model_alias),
      ...(ovData?.daily || []).map(r => r.model_alias),
    ].filter(Boolean))].sort((a, b) => a.localeCompare(b));
    return new Map(names.map((n, i) => [n, i % 8]));
  }, [ovData]);

  const summary = ovData?.summary || [];
  const daily = ovData?.daily || [];

  if (!ovData) return <Loading message={t('loading', lang)} />;
  if (!tk) return <Loading message={t('loading', lang)} />;

  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const todayRows = daily.filter(x => x.d === todayStr);
  const todayTokens = todayRows.reduce((a, b) => a + (b.t || 0), 0);
  const todayReqs = todayRows.reduce((a, b) => a + (b.req || 0), 0);
  const total30dTokens = summary.reduce((a, b) => a + (b.t || 0), 0);
  const total30dReqs = summary.reduce((a, b) => a + (b.req || 0), 0);

  const days = [...new Set(daily.map(x => x.d))].sort();
  const models = [...new Set(daily.map(x => x.model_alias))].sort((a, b) => a.localeCompare(b));

  const lineData = {
    labels: days,
    datasets: models.map(m => {
      const c = tk.series[slotOf.get(m) ?? 0];
      return {
        label: m,
        data: days.map(day => {
          const row = daily.find(x => x.d === day && x.model_alias === m);
          return row ? row.t : 0;
        }),
        borderColor: c,
        backgroundColor: c + '14',
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBorderWidth: 2,
        pointHoverBackgroundColor: c,
        pointHoverBorderColor: tk.surface,
        borderWidth: 2,
      };
    }),
  };

  const axisFont = { size: 11, family: 'Inter' };
  const legendFont = { size: 11, family: 'Inter' };

  const lineOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        position: 'top',
        align: 'start',
        labels: {
          color: tk.ink2,
          font: legendFont,
          boxWidth: 8,
          boxHeight: 8,
          usePointStyle: true,
          pointStyle: 'rectRounded',
          padding: 14,
        },
      },
      tooltip: {
        backgroundColor: tk.surface,
        titleColor: tk.ink,
        bodyColor: tk.ink2,
        borderColor: tk.line,
        borderWidth: 1,
        padding: 10,
        cornerRadius: 8,
        boxWidth: 8,
        boxHeight: 8,
        usePointStyle: true,
        callbacks: { label: ctx => ` ${ctx.dataset.label}: ${fmt(ctx.parsed.y)}` },
      },
    },
    scales: {
      x: {
        ticks: { color: tk.ink3, maxTicksLimit: 6, font: axisFont, maxRotation: 0 },
        grid: { display: false },
        border: { color: tk.line },
      },
      y: {
        beginAtZero: true,
        ticks: { color: tk.ink3, callback: v => fmt(v), font: axisFont, maxTicksLimit: 5 },
        grid: { color: tk.grid, drawTicks: false },
        border: { display: false },
      },
    },
  };

  const donutTotal = summary.reduce((a, b) => a + (b.t || 0), 0);

  const donutData = {
    labels: summary.map(x => x.model_alias),
    datasets: [{
      data: summary.map(x => x.t || 0),
      backgroundColor: summary.map(x => tk.series[slotOf.get(x.model_alias) ?? 0]),
      // The 2 px surface-coloured ring is what separates neighbouring arcs
      // without shrinking the marks.
      borderColor: tk.surface,
      borderWidth: 2,
      hoverOffset: 4,
      hoverBorderColor: tk.surface,
    }],
  };

  const donutOptions = {
    responsive: true,
    maintainAspectRatio: false,
    cutout: '68%',
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: tk.surface,
        titleColor: tk.ink,
        bodyColor: tk.ink2,
        borderColor: tk.line,
        borderWidth: 1,
        padding: 10,
        cornerRadius: 8,
        boxWidth: 8,
        boxHeight: 8,
        usePointStyle: true,
        callbacks: {
          label: ctx => {
            const pct = donutTotal > 0 ? (ctx.parsed / donutTotal) * 100 : 0;
            return ` ${ctx.label}: ${fmt(ctx.parsed)} (${pct.toFixed(1)}%)`;
          },
        },
      },
    },
  };

  const kpiCards = [
    {
      id: 'today',
      title: t('today', lang),
      value: fmt(todayTokens),
      subtext: `${todayReqs.toLocaleString()} ${t('requests_count', lang)}`,
      icon: <Calendar className="w-4 h-4" />,
      accent: 'var(--c-accent)',
      onClick: () => {
        refreshTab();
        window.dispatchEvent(new CustomEvent('spawn-custom-particles', { detail: { type: 'sparkles' } }));
      },
    },
    {
      id: 'last_30_days',
      title: t('last_30_days', lang),
      value: fmt(total30dTokens),
      subtext: `${total30dReqs.toLocaleString()} ${t('requests_count', lang)}`,
      icon: <BarChart3 className="w-4 h-4" />,
      accent: 'var(--s-3)',
      onClick: () => {
        window.dispatchEvent(new CustomEvent('spawn-custom-particles', { detail: { type: 'sparkles' } }));
      },
    },
    {
      id: 'savings',
      title: flipped ? "Claude's Tears" : t('savings_amt', lang),
      value: flipped ? '😢' : `$${savings.toFixed(4)}`,
      subtext: flipped ? "Claude: 😢 · Gemini: 😎" : t('saved_vs_claude', lang),
      icon: <TrendingUp className="w-4 h-4" />,
      accent: flipped ? 'var(--c-critical)' : 'var(--c-good)',
      onClick: e => {
        window.dispatchEvent(new CustomEvent('spawn-custom-particles', {
          detail: { type: 'money', x: e.clientX, y: e.clientY },
        }));
        window.dispatchEvent(new CustomEvent('egg-unlocked-event', { detail: { id: 'savings' } }));
      },
      onDoubleClick: e => {
        e.stopPropagation();
        setFlipped(p => !p);
        window.dispatchEvent(new CustomEvent('egg-unlocked-event', { detail: { id: 'claude' } }));
      },
    },
    {
      id: 'active_models',
      title: t('active_models', lang),
      value: summary.length.toString(),
      subtext: t('gemini_supported', lang),
      icon: <Cpu className="w-4 h-4" />,
      accent: 'var(--s-4)',
      onClick: () => {
        document.getElementById('model-detail-table')?.scrollIntoView({ behavior: 'smooth' });
      },
    },
  ];

  /* Pool live capacity: per-pool, with the meter filled by *consumed* share so
     the bar and the number agree. */
  const poolCards = (() => {
    if (!liveStats) return null;
    const models = liveStats.models || {};
    const pools = {
      flash: { rpm_rem: 0, rpm_lim: 0, tpm_rem: 0, tpm_lim: 0, cnt: 0 },
      lite: { rpm_rem: 0, rpm_lim: 0, tpm_rem: 0, tpm_lim: 0, cnt: 0 },
    };
    for (const [alias, ms] of Object.entries(models)) {
      const p = alias.includes('lite') ? pools.lite : pools.flash;
      p.rpm_rem += ms.rpm_remaining || 0;
      p.rpm_lim += ms.rpm_limit || 0;
      p.tpm_rem += ms.tpm_remaining || 0;
      p.tpm_lim += ms.tpm_limit || 0;
      p.cnt++;
    }
    const out = [];
    if (pools.flash.cnt > 0) out.push({ key: 'flash', label: 'Gemini Flash', ...pools.flash });
    if (pools.lite.cnt > 0) out.push({ key: 'lite', label: 'Gemini Flash Lite', ...pools.lite });
    return out;
  })();

  /* Bar and number measure the same thing — headroom left. A bar filled to the
     right means "plenty", which is the opposite of how a load meter reads, so
     the colour carries the alarm instead of the length. */
  const meter = (label, remain, limit) => {
    const left = limit > 0 ? Math.max(0, Math.min(100, Math.round((remain / limit) * 100))) : 0;
    const color = left < 15 ? tk.critical : left < 40 ? tk.warn : tk.good;
    return (
      <div key={label}>
        <div className="flex items-baseline justify-between gap-2 mb-1.5">
          <span className="label-micro">{label}</span>
          <span className="text-xs font-medium tabular-nums" style={{ color: 'var(--c-ink-2)' }}>
            {remain.toLocaleString()}
            <span style={{ color: 'var(--c-ink-3)' }}> / {limit.toLocaleString()} left</span>
          </span>
        </div>
        <div className="meter" role="meter" aria-valuenow={left} aria-valuemin={0} aria-valuemax={100}
             aria-label={`${label} headroom ${left}%`}>
          <span style={{ width: `${left}%`, background: color }} />
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-5">
      <PageHeader title={t('ov_title', lang)} subtitle={t('ov_sub', lang)} />

      {poolCards && (
        <div className="animate-fade-in-up">
          <div className="flex items-center gap-1.5 mb-2">
            {wsHook?.connected
              ? <Wifi className="w-3.5 h-3.5" style={{ color: 'var(--c-good)' }} />
              : <WifiOff className="w-3.5 h-3.5" style={{ color: 'var(--c-critical)' }} />}
            <span className="label-micro">
              {wsHook?.connected ? t('ov_pool_live', lang) : t('ov_offline', lang)}
            </span>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {poolCards.map(p => (
              <Card key={p.key} padding="sm">
                <div className="flex items-baseline justify-between mb-3">
                  <span className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{p.label}</span>
                  <span className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
                    {t('ov_pool_members', lang).replace('{n}', p.cnt)}
                  </span>
                </div>
                <div className="space-y-2.5">
                  {meter('RPM', p.rpm_rem, p.rpm_lim)}
                  {meter('TPM', p.tpm_rem, p.tpm_lim)}
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* KPI row — the one place the page spends weight. */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {kpiCards.map(card => (
          <Card
            key={card.id}
            padding="sm"
            hoverable
            role="button"
            tabIndex={0}
            onClick={card.onClick}
            onDoubleClick={card.onDoubleClick}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.onClick?.(e); } }}
          >
            <div className="flex items-center gap-1.5 mb-2.5">
              <span style={{ color: card.accent }}>{card.icon}</span>
              <span className="label-micro truncate">{card.title}</span>
            </div>
            <div className="text-[1.75rem] leading-none font-semibold tabular-nums truncate"
                 style={{ color: 'var(--c-ink)', letterSpacing: '-0.02em' }}>
              {card.value}
            </div>
            <div className="text-[0.6875rem] mt-2 truncate" style={{ color: 'var(--c-ink-3)' }}>
              {card.subtext}
            </div>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.618fr_1fr] gap-4 items-start">
        <Card padding="sm" className="text-left">
          <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
            <h3 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{t('ov_daily_stats', lang)}</h3>
            <Button
              variant="ghost"
              size="xs"
              icon={showTable ? LineChart : Table2}
              onClick={() => setShowTable(v => !v)}
              aria-pressed={showTable}
            >
              {showTable ? t('ov_view_chart', lang) : t('ov_view_table', lang)}
            </Button>
          </div>

          {showTable ? (
            <div className="overflow-x-auto max-h-72">
              <table className="table table-xs w-full">
                <thead>
                  <tr>
                    <th>{t('th_model', lang)}</th>
                    <th className="text-right">{t('th_total_tokens', lang)}</th>
                    <th className="text-right">{t('th_share', lang)}</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.map(row => (
                    <tr key={row.model_alias}>
                      <td>
                        <span className="flex items-center gap-2">
                          <span className="series-dot" style={{ background: tk.series[slotOf.get(row.model_alias) ?? 0] }} />
                          <span className="font-mono text-[0.8125rem]">{row.model_alias}</span>
                        </span>
                      </td>
                      <td className="text-right tabular-nums">{fmt(row.t || 0)}</td>
                      <td className="text-right tabular-nums" style={{ color: 'var(--c-ink-3)' }}>
                        {donutTotal > 0 ? (((row.t || 0) / donutTotal) * 100).toFixed(1) : '0.0'}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="h-72 w-full">
              {models.length > 0
                ? <Line data={lineData} options={lineOptions} />
                : <div className="h-full grid place-items-center text-xs" style={{ color: 'var(--c-ink-3)' }}>{t('no_data', lang)}</div>}
            </div>
          )}
        </Card>

        <Card padding="sm" className="text-left">
          <h3 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink)' }}>{t('ov_model_ratio', lang)}</h3>
          {summary.length > 0 ? (
            <>
              <div className="h-48 w-full">
                <Doughnut data={donutData} options={donutOptions} />
              </div>
              {/* Legend carries identity so the arcs never have to. */}
              <ul className="mt-3 space-y-1.5">
                {summary.map(row => {
                  const pct = donutTotal > 0 ? ((row.t || 0) / donutTotal) * 100 : 0;
                  return (
                    <li key={row.model_alias} className="flex items-center gap-2 text-[0.6875rem]">
                      <span className="series-dot" style={{ background: tk.series[slotOf.get(row.model_alias) ?? 0] }} />
                      <span className="flex-1 truncate font-mono" style={{ color: 'var(--c-ink-2)' }}>{row.model_alias}</span>
                      <span className="tabular-nums" style={{ color: 'var(--c-ink-3)' }}>{pct.toFixed(1)}%</span>
                    </li>
                  );
                })}
              </ul>
            </>
          ) : (
            <div className="h-48 grid place-items-center text-xs" style={{ color: 'var(--c-ink-3)' }}>{t('no_data', lang)}</div>
          )}
        </Card>
      </div>

      <Card padding="none" className="overflow-hidden text-left">
        <div className="px-4 py-3 flex items-center justify-between gap-3"
             style={{ borderBottom: '1px solid var(--c-line)' }}>
          <h3 className="text-xs font-semibold flex items-center gap-2" style={{ color: 'var(--c-ink)' }}>
            <Clock className="w-3.5 h-3.5" style={{ color: 'var(--c-ink-3)' }} />
            {t('ov_recent', lang)}
          </h3>
          <span className="flex items-center gap-1.5 text-[0.6875rem] font-medium"
                style={{ color: 'var(--c-ink-3)' }}>
            <span className="w-1.5 h-1.5 rounded-full animate-breathing" style={{ background: 'var(--c-good)' }} />
            {t('lg_live', lang)}
          </span>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table w-full">
            <thead>
              <tr>
                <th className="w-6"></th>
                <th>{t('th_model', lang)}</th>
                <th className="text-right">{t('th_prompt_tokens', lang)}</th>
                <th className="text-right">{t('th_completion_tokens', lang)}</th>
                <th className="text-right">{t('th_time', lang)}</th>
              </tr>
            </thead>
            <tbody>
              {recentRequests.length > 0 ? recentRequests.map((r, i) => (
                <tr key={`${r.timestamp}-${i}`}>
                  <td>
                    <span className="block w-1.5 h-1.5 rounded-full" style={{ background: 'var(--c-good)' }} aria-hidden="true" />
                  </td>
                  <td className="font-mono text-[0.8125rem] whitespace-nowrap" style={{ color: 'var(--c-ink)' }}>
                    {r.model_alias}
                  </td>
                  <td className="text-right tabular-nums whitespace-nowrap">{fmt(r.prompt_tokens)}</td>
                  <td className="text-right tabular-nums whitespace-nowrap">{fmt(r.completion_tokens)}</td>
                  <td className="text-right whitespace-nowrap" style={{ color: 'var(--c-ink-3)' }}>
                    <TimeAgo timestamp={r.timestamp} lang={lang} />
                  </td>
                </tr>
              )) : (
                <tr>
                  <td colSpan="5" className="text-center py-8 text-xs" style={{ color: 'var(--c-ink-3)' }}>
                    {t('ov_recent_empty', lang)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <Card id="model-detail-table" padding="none" className="overflow-hidden text-left">
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--c-line)' }}>
          <h3 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{t('ov_model_detail', lang)}</h3>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table w-full">
            <thead>
              <tr>
                <th>{t('th_model', lang)}</th>
                <th className="text-right">{t('th_prompt_tokens', lang)}</th>
                <th className="text-right">{t('th_completion_tokens', lang)}</th>
                <th className="text-right">{t('th_total_tokens', lang)}</th>
                <th className="text-right">{t('th_requests', lang)}</th>
              </tr>
            </thead>
            <tbody>
              {summary.length > 0 ? summary.map(r => (
                <tr key={r.model_alias}>
                  <td>
                    <span className="flex items-center gap-2">
                      <span className="series-dot" style={{ background: tk.series[slotOf.get(r.model_alias) ?? 0] }} />
                      <span className="font-mono text-[0.8125rem]" style={{ color: 'var(--c-ink)' }}>{r.model_alias}</span>
                    </span>
                  </td>
                  <td className="text-right tabular-nums whitespace-nowrap">{fmt(r.p || 0)}</td>
                  <td className="text-right tabular-nums whitespace-nowrap">{fmt(r.c || 0)}</td>
                  <td className="text-right tabular-nums whitespace-nowrap font-semibold" style={{ color: 'var(--c-ink)' }}>
                    {fmt(r.t || 0)}
                  </td>
                  <td className="text-right tabular-nums whitespace-nowrap">{(r.req || 0).toLocaleString()}</td>
                </tr>
              )) : (
                <tr>
                  <td colSpan="5" className="text-center py-8 text-xs" style={{ color: 'var(--c-ink-3)' }}>
                    {t('no_data', lang)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}