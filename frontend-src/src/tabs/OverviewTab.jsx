import React, { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { 
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, 
  LineElement, Title, Tooltip, Legend, ArcElement 
} from 'chart.js';
import { Line, Doughnut } from 'react-chartjs-2';
import { Calendar, BarChart3, TrendingUp, Cpu } from 'lucide-react';

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement, 
  Title, Tooltip, Legend, ArcElement
);

// Colors matching global app theme
const CLR = [
  '#6366f1', '#10b981', '#f59e0b', '#ec4899', '#3b82f6', 
  '#8b5cf6', '#14b8a6', '#f43f5e', '#a855f7', '#06b6d4'
];

export default function OverviewTab() {
  const { tabData, lang, theme, refreshTab } = useApp();
  const ovData = tabData.ov;

  const [tickingSavings, setTickingSavings] = useState(0);
  const [clrOffset, setClrOffset] = useState(0);
  const [flipped, setFlipped] = useState(false);

  // Set up savings animated ticker
  useEffect(() => {
    if (ovData?.savings?.net_savings !== undefined) {
      const target = ovData.savings.net_savings;
      setTickingSavings(target);
      
      const interval = setInterval(() => {
        setTickingSavings(prev => prev + Math.random() * 0.0001);
      }, 3500);
      
      return () => clearInterval(interval);
    }
  }, [ovData?.savings?.net_savings]);

  if (!ovData) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-3">
        <span className="loading loading-spinner loading-md text-primary"></span>
        <span className="text-sm font-semibold opacity-70">{t('loading', lang)}</span>
      </div>
    );
  }

  const { summary = [], daily = [] } = ovData;

  // Calculate stats values
  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  const todayRows = daily.filter(x => x.d === todayStr);
  const todayTokens = todayRows.reduce((a, b) => a + (b.t || 0), 0);
  const todayReqs = todayRows.reduce((a, b) => a + (b.req || 0), 0);
  const total30dTokens = summary.reduce((a, b) => a + (b.t || 0), 0);
  const total30dReqs = summary.reduce((a, b) => a + (b.req || 0), 0);

  // 1. Line Chart Data (Daily stats)
  const days = [...new Set(daily.map(x => x.d))].sort();
  const models = [...new Set(daily.map(x => x.model_alias))];
  
  const lineData = {
    labels: days,
    datasets: models.map((m, idx) => {
      const colorIndex = (idx + clrOffset) % CLR.length;
      return {
        label: m,
        data: days.map(day => {
          const row = daily.find(x => x.d === day && x.model_alias === m);
          return row ? row.t : 0;
        }),
        borderColor: CLR[colorIndex],
        backgroundColor: CLR[colorIndex] + '10',
        fill: true,
        tension: 0.4,
        pointRadius: 2,
        pointHoverRadius: 5,
        borderWidth: 2,
      };
    })
  };

  const lineOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: {
          color: theme === 'light' ? '#4b5563' : '#9ca3af',
          font: { size: 10, family: 'Inter', weight: 'bold' }
        }
      },
      tooltip: {
        backgroundColor: 'rgba(9,13,22,0.95)',
        borderColor: 'rgba(255,255,255,0.08)',
        borderWidth: 1,
        callbacks: {
          label: ctx => ` ${ctx.dataset.label}: ${fmt(ctx.parsed.y)}`
        }
      }
    },
    scales: {
      x: {
        ticks: { color: theme === 'light' ? '#6b7280' : '#4b5563', maxTicksLimit: 7, font: { size: 9 } },
        grid: { color: theme === 'light' ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.03)' }
      },
      y: {
        ticks: { color: theme === 'light' ? '#6b7280' : '#4b5563', callback: v => fmt(v), font: { size: 9 } },
        grid: { color: theme === 'light' ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.03)' }
      }
    }
  };

  // 2. Donut Chart Data (Model ratio)
  const donutData = {
    labels: summary.map(x => x.model_alias),
    datasets: [{
      data: summary.map(x => x.t || 0),
      backgroundColor: CLR.slice(0, summary.length),
      borderWidth: 0,
      hoverOffset: 6,
    }]
  };

  const donutOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'bottom',
        labels: {
          color: theme === 'light' ? '#4b5563' : '#9ca3af',
          font: { size: 10, family: 'Inter' }
        }
      },
      tooltip: {
        backgroundColor: 'rgba(9,13,22,0.95)',
        borderColor: 'rgba(255,255,255,0.08)',
        borderWidth: 1,
        callbacks: {
          label: ctx => ` ${ctx.label}: ${fmt(ctx.parsed)}`
        }
      }
    },
    cutout: '70%'
  };

  const kpiCards = [
    {
      id: 'today',
      title: t('today', lang),
      value: fmt(todayTokens),
      subtext: `${todayReqs.toLocaleString()} ${t('requests_count', lang)}`,
      icon: <Calendar className="w-4 h-4" />,
      colorClass: 'text-primary bg-primary/10 border-primary/15',
      dotColor: 'bg-primary',
      delay: 'cascade-1',
      onClick: () => {
        refreshTab();
        window.dispatchEvent(new CustomEvent('spawn-custom-particles', { detail: { type: 'sparkles' } }));
      }
    },
    {
      id: 'last_30_days',
      title: t('last_30_days', lang),
      value: fmt(total30dTokens),
      subtext: `${total30dReqs.toLocaleString()} ${t('requests_count', lang)}`,
      icon: <BarChart3 className="w-4 h-4" />,
      colorClass: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/15',
      dotColor: 'bg-cyan-400',
      delay: 'cascade-2',
      onClick: () => {
        setClrOffset(prev => prev + 1);
        window.dispatchEvent(new CustomEvent('spawn-custom-particles', { detail: { type: 'sparkles' } }));
      }
    },
    {
      id: 'savings',
      title: flipped ? "Claude's Tears" : t('savings_amt', lang),
      value: flipped ? "Claude: 😢 | Gemini: 😎" : `$${tickingSavings.toFixed(4)}`,
      subtext: flipped ? "Unlocked secret!" : t('saved_vs_claude', lang),
      icon: <TrendingUp className="w-4 h-4" />,
      colorClass: flipped ? 'text-red-400 bg-red-500/10 border-red-500/15' : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/15',
      dotColor: flipped ? 'bg-red-400' : 'bg-emerald-400',
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
      }
    },
    {
      id: 'active_models',
      title: t('active_models', lang),
      value: summary.length.toString(),
      subtext: t('gemini_supported', lang),
      icon: <Cpu className="w-4 h-4" />,
      colorClass: 'text-amber-400 bg-amber-500/10 border-amber-500/15',
      dotColor: 'bg-amber-400',
      delay: 'cascade-4',
      onClick: () => {
        const tbl = document.getElementById('model-detail-table');
        if (tbl) tbl.scrollIntoView({ behavior: 'smooth' });
      }
    }
  ];

  return (
    <div className="space-y-6">
      {/* Title */}
      <div className="text-left animate-tab-in">
        <h1 className="text-2xl font-black tracking-tight">{t('ov_title', lang)}</h1>
        <p className="text-xs text-base-content/60 mt-1">{t('ov_sub', lang)}</p>
      </div>

      {/* KPI Cards: 2x2 grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 animate-fade-in-up cascade-1">
        {kpiCards.map((card, i) => (
          <div
            key={i}
            onClick={card.onClick}
            onDoubleClick={card.onDoubleClick}
            className={`card glass-card flex items-center gap-3 p-4 rounded-2xl border border-base-content/5 transition-all duration-300 hover:scale-[1.03] active:scale-[0.97] cursor-pointer hover:border-primary/20 hover:bg-base-200/35 animate-fade-in-up ${card.delay}`}
          >
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center border shadow-sm shrink-0 ${card.colorClass}`}>
              {card.icon}
            </div>
            <div className="flex-1 min-w-0">
              <span className="text-[10px] font-bold text-base-content/45 uppercase tracking-wider block truncate">{card.title}</span>
              <span className="text-lg font-black tracking-tight text-base-content mt-0.5 block leading-none truncate">{card.value}</span>
              <span className="text-[9px] font-bold text-base-content/40 flex items-center gap-1 mt-1">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${card.dotColor} animate-pulse`}></span>
                <span className="truncate">{card.subtext}</span>
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Charts Row: Line chart (60%) + Donut (40%) */}
      <div className="grid grid-cols-1 lg:grid-cols-[1.618fr_1fr] gap-6 items-start animate-fade-in-up cascade-2">
        {/* Daily line chart */}
        <div className="card glass-card p-5 rounded-3xl text-left">
          <h3 className="font-extrabold text-sm mb-4 text-base-content/85">{t('ov_daily_stats', lang)}</h3>
          <div className="h-72 w-full">
            <Line data={lineData} options={lineOptions} />
          </div>
        </div>

        {/* Model ratio donut chart */}
        <div className="card glass-card p-5 rounded-3xl text-left border border-base-content/5">
          <h3 className="font-extrabold text-[11px] uppercase tracking-wider text-base-content/50 mb-4">{t('ov_model_ratio', lang)}</h3>
          <div className="h-72 w-full flex items-center justify-center">
            {summary.length > 0 ? (
              <Doughnut data={donutData} options={donutOptions} />
            ) : (
              <span className="text-xs text-base-content/40">{t('no_data', lang)}</span>
            )}
          </div>
        </div>
      </div>

      {/* Model detailed table */}
      <div id="model-detail-table" className="card glass-card rounded-3xl overflow-hidden text-left border border-base-content/5 animate-fade-in-up cascade-3">
        <div className="p-5 border-b border-base-content/5 bg-base-200/15">
          <h3 className="font-extrabold text-sm text-base-content/95">{t('ov_model_detail', lang)}</h3>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table table-zebra w-full text-xs">
            <thead>
              <tr className="border-b border-base-content/5 text-base-content/60 bg-base-200/30">
                <th className="font-bold">{t('th_model', lang)}</th>
                <th className="font-bold">{t('th_prompt_tokens', lang)}</th>
                <th className="font-bold">{t('th_completion_tokens', lang)}</th>
                <th className="font-bold">{t('th_total_tokens', lang)}</th>
                <th className="font-bold">{t('th_requests', lang)}</th>
              </tr>
            </thead>
            <tbody>
              {summary.length > 0 ? (
                summary.map((r, i) => (
                  <tr key={i} className="border-b border-base-content/5 hover:bg-base-200/50">
                    <td className="font-bold">{r.model_alias}</td>
                    <td>{fmt(r.p || 0)}</td>
                    <td>{fmt(r.c || 0)}</td>
                    <td className="font-bold text-primary">{fmt(r.t || 0)}</td>
                    <td>{(r.req || 0).toLocaleString()}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="5" className="text-center py-6 text-base-content/40 font-medium">
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
