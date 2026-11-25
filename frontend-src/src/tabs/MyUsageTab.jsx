import { useMemo, useState } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Title, Tooltip, Legend, ArcElement
} from 'chart.js';
import { Line, Doughnut } from 'react-chartjs-2';
import { Calendar, BarChart3, TrendingUp, Cpu } from 'lucide-react';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import { useThemeTokens } from '../utils/themeTokens';

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, ArcElement
);

export default function MyUsageTab() {
  const { tabData, lang, refreshTab } = useApp();
  const myUseData = tabData.myuse;
  const tk = useThemeTokens();

  const [clrOffset, setClrOffset] = useState(0);
  const [flipped, setFlipped] = useState(false);

  /* Series slots are keyed by model name, in alphabetical order, so filtering or
     reordering the table never repaints a surviving model. The offset only
     moves on an explicit click — an easter egg, never the default state. */
  const { summary = [], daily = [] } = myUseData || {};
  const models = useMemo(
    () => [...new Set([...summary, ...daily].map(r => r.model_alias).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b)),
    [summary, daily]
  );
  const slotOf = useMemo(() => new Map(models.map((m, i) => [m, i % 8])), [models]);

  if (!myUseData) return <Loading message={t('loading', lang)} />;

  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const todayRows = daily.filter(x => x.d === todayStr);
  const todayTokens = todayRows.reduce((a, b) => a + (b.t || 0), 0);
  const todayReqs = todayRows.reduce((a, b) => a + (b.req || 0), 0);
  const total30dTokens = summary.reduce((a, b) => a + (b.t || 0), 0);
  const total30dReqs = summary.reduce((a, b) => a + (b.req || 0), 0);
  const savings = myUseData?.savings?.net_savings ?? 0;

  const days = [...new Set(daily.map(x => x.d))].sort();
  const seriesModels = [...new Set(daily.map(x => x.model_alias))].sort((a, b) => a.localeCompare(b));

  const hueOf = (name) => tk.series[((slotOf.get(name) ?? 0) + clrOffset) % 8];

  const lineData = {
    labels: days,
    datasets: seriesModels.map(m => {
      const c = hueOf(m);
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

  const donutData = {
    labels: summary.map(x => x.model_alias),
    datasets: [{
      data: summary.map(x => x.t || 0),
      backgroundColor: summary.map(x => hueOf(x.model_alias)),
      // 2 px surface ring separates neighbouring arcs without shrinking them.
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
        callbacks: { label: ctx => ` ${ctx.label}: ${fmt(ctx.parsed)}` },
      },
    },
  };

  const kpiCards = [
    {
      id: 'today',
      title: t('today', lang),
      value: fmt(todayTokens),
      subtext: `${todayReqs.toLocaleString()} ${t('requests_count', lang)}`,
      icon: Calendar,
      color: 'var(--c-accent)',
      delay: 'cascade-1',
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
      icon: BarChart3,
      color: 'var(--s-1)',
      delay: 'cascade-2',
      onClick: () => {
        setClrOffset(prev => prev + 1);
        window.dispatchEvent(new CustomEvent('spawn-custom-particles', { detail: { type: 'sparkles' } }));
      },
    },
    {
      id: 'savings',
      title: flipped ? "Claude's Tears" : t('savings_amt', lang),
      value: flipped ? 'Claude: 😢 | Gemini: 😎' : `$${savings.toFixed(4)}`,
      subtext: flipped ? 'Unlocked secret!' : t('saved_vs_claude', lang),
      icon: TrendingUp,
      color: flipped ? 'var(--c-critical)' : 'var(--s-3)',
      delay: 'cascade-3',
      onClick: (e) => {
        window.dispatchEvent(new CustomEvent('spawn-custom-particles', {
          detail: { type: 'money', x: e.clientX, y: e.clientY }
        }));
        window.dispatchEvent(new CustomEvent('egg-unlocked-event', { detail: { id: 'savings' } }));
      },
      onDoubleClick: (e) => {
        e.stopPropagation();
        setFlipped(prev => !prev);
        window.dispatchEvent(new CustomEvent('egg-unlocked-event', { detail: { id: 'claude' } }));
      },
    },
    {
      id: 'active_models',
      title: t('active_models', lang),
      value: summary.length.toString(),
      subtext: t('gemini_supported', lang),
      icon: Cpu,
      color: 'var(--s-4)',
      delay: 'cascade-4',
      onClick: () => {
        document.getElementById('model-detail-table')?.scrollIntoView({ behavior: 'smooth' });
      },
    },
  ];

  const donutTotal = summary.reduce((a, b) => a + (b.t || 0), 0);

  return (
    <div className="space-y-5">
      <PageHeader title={t('nav_myuse', lang)} subtitle={t('myuse_sub', lang)} />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 animate-fade-in-up cascade-1">
        {kpiCards.map((card) => {
          const Icon = card.icon;
          return (
            <Card
              key={card.id}
              padding="md"
              className={`glass-card-interactive animate-fade-in-up ${card.delay}`}
              onClick={card.onClick}
              onDoubleClick={card.onDoubleClick}
            >
              <div className="flex items-center gap-2 mb-3">
                <span className="w-6 h-6 rounded-md flex items-center justify-center shrink-0"
                      style={{ background: `color-mix(in srgb, ${card.color} 14%, transparent)`, color: card.color }}>
                  <Icon className="w-3.5 h-3.5" />
                </span>
                <span className="label-micro truncate">{card.title}</span>
              </div>
              <div className="text-[1.5rem] leading-none font-semibold truncate tabular-nums"
                   style={{ color: 'var(--c-ink)', letterSpacing: '-0.02em' }}>
                {card.value}
              </div>
              <p className="text-[0.6875rem] mt-2 truncate" style={{ color: 'var(--c-ink-3)' }}>
                {card.subtext}
              </p>
            </Card>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.618fr_1fr] gap-4 items-start animate-fade-in-up cascade-2">
        <Card padding="lg" className="text-left">
          <h3 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink-2)' }}>
            {t('ov_daily_stats', lang)}
          </h3>
          <div className="h-72 w-full">
            {seriesModels.length > 0 ? (
              <Line data={lineData} options={lineOptions} />
            ) : (
              <div className="h-full grid place-items-center text-xs" style={{ color: 'var(--c-ink-3)' }}>
                {t('no_data', lang)}
              </div>
            )}
          </div>
        </Card>

        <Card padding="lg" className="text-left">
          <h3 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink-2)' }}>
            {t('ov_model_ratio', lang)}
          </h3>
          <div className="flex items-center justify-center gap-4 flex-wrap">
            <div className="relative w-40 h-40 shrink-0">
              {summary.length > 0 ? (
                <>
                  <Doughnut data={donutData} options={donutOptions} />
                  <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                    <span className="text-[1.125rem] leading-none font-semibold tabular-nums"
                          style={{ color: 'var(--c-ink)' }}>
                      {fmt(donutTotal)}
                    </span>
                    <span className="label-micro mt-1">tokens</span>
                  </div>
                </>
              ) : (
                <div className="h-full grid place-items-center text-xs" style={{ color: 'var(--c-ink-3)' }}>
                  {t('no_data', lang)}
                </div>
              )}
            </div>
            {summary.length > 0 && (
              <ul className="space-y-1.5 min-w-0">
                {summary.map(row => (
                  <li key={row.model_alias} className="flex items-center gap-2 text-[0.6875rem] min-w-0">
                    <span className="w-2 h-2 rounded-sm shrink-0"
                          style={{ background: hueOf(row.model_alias) }} />
                    <span className="truncate" style={{ color: 'var(--c-ink-2)' }}>{row.model_alias}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Card>
      </div>

      <div id="model-detail-table" className="glass-card rounded-xl overflow-hidden text-left animate-fade-in-up cascade-3">
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--c-line)' }}>
          <h3 className="text-xs font-semibold" style={{ color: 'var(--c-ink-2)' }}>
            {t('ov_model_detail', lang)}
          </h3>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table w-full">
            <thead>
              <tr>
                <th className="min-w-[11rem]">{t('th_model', lang)}</th>
                <th className="min-w-[8rem] text-right">{t('th_prompt_tokens', lang)}</th>
                <th className="min-w-[8rem] text-right">{t('th_completion_tokens', lang)}</th>
                <th className="min-w-[8rem] text-right">{t('th_total_tokens', lang)}</th>
                <th className="min-w-[6rem] text-right">{t('th_requests', lang)}</th>
              </tr>
            </thead>
            <tbody>
              {summary.length > 0 ? summary.map((r) => (
                <tr key={r.model_alias}>
                  <td>
                    <span className="flex items-center gap-2 min-w-0">
                      <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: hueOf(r.model_alias) }} />
                      <span className="truncate text-xs font-medium" style={{ color: 'var(--c-ink)' }}>
                        {r.model_alias}
                      </span>
                    </span>
                  </td>
                  <td className="text-right tabular-nums">{fmt(r.p || 0)}</td>
                  <td className="text-right tabular-nums">{fmt(r.c || 0)}</td>
                  <td className="text-right tabular-nums font-medium" style={{ color: 'var(--c-ink)' }}>
                    {fmt(r.t || 0)}
                  </td>
                  <td className="text-right tabular-nums">{(r.req || 0).toLocaleString()}</td>
                </tr>
              )) : (
                <tr>
                  <td colSpan="5" className="text-center py-6 text-xs" style={{ color: 'var(--c-ink-3)' }}>
                    {t('no_data', lang)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}