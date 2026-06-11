import React from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { BarChart3, Users, Award, Zap } from 'lucide-react';

const CLR = [
  'bg-primary',
  'bg-secondary',
  'bg-accent',
  'bg-success',
  'bg-info',
  'bg-warning',
  'bg-error',
  'bg-indigo-500',
  'bg-emerald-500',
  'bg-amber-500'
];

const CLR_TEXT = [
  'text-primary',
  'text-secondary',
  'text-accent',
  'text-success',
  'text-info',
  'text-warning',
  'text-error',
  'text-indigo-400',
  'text-emerald-400',
  'text-amber-400'
];

export default function TokenAnalysisTab() {
  const { tabData, lang } = useApp();
  const ovData = tabData.ov;
  
  if (!ovData) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-3">
        <span className="loading loading-spinner loading-md text-primary"></span>
        <span className="text-sm font-semibold opacity-70">{t('loading', lang)}</span>
      </div>
    );
  }

  const topKeys = ovData.top_keys || [];

  if (topKeys.length === 0) {
    return (
      <div className="card glass-card p-8 text-center text-base-content/50 rounded-2xl">
        <div className="text-4xl mb-2">📭</div>
        <p className="font-semibold">{t('no_data', lang)}</p>
      </div>
    );
  }

  // Find max tokens for relative width calculation
  const maxTokens = Math.max(...topKeys.map(k => k.t || 1));

  return (
    <div className="space-y-6">
      {/* Title */}
      <div className="text-left">
        <h1 className="text-2xl font-black tracking-tight">{t('nav_us', lang) || 'Phân tích tiêu thụ'}</h1>
        <p className="text-xs text-base-content/60 mt-1">Bảng xếp hạng tài khoản con và các khóa API tiêu hao nhiều tài nguyên nhất trong 30 ngày qua</p>
      </div>

      {/* Overview Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="card glass-card p-5 rounded-2xl flex flex-row items-center gap-4 text-left">
          <div className="w-12 h-12 bg-primary/10 text-primary rounded-xl flex items-center justify-center">
            <Award className="w-6 h-6" />
          </div>
          <div>
            <span className="text-xs font-bold text-base-content/60 uppercase">Top Consumer</span>
            <h3 className="text-lg font-black truncate max-w-[200px] mt-0.5">{topKeys[0]?.account_name || '—'}</h3>
            <span className="text-xs text-base-content/50">{fmt(topKeys[0]?.t || 0)} tokens</span>
          </div>
        </div>

        <div className="card glass-card p-5 rounded-2xl flex flex-row items-center gap-4 text-left">
          <div className="w-12 h-12 bg-secondary/10 text-secondary rounded-xl flex items-center justify-center">
            <Users className="w-6 h-6" />
          </div>
          <div>
            <span className="text-xs font-bold text-base-content/60 uppercase">Active Accounts</span>
            <h3 className="text-lg font-black mt-0.5">{topKeys.length}</h3>
            <span className="text-xs text-base-content/50">in last 30 days</span>
          </div>
        </div>

        <div className="card glass-card p-5 rounded-2xl flex flex-row items-center gap-4 text-left">
          <div className="w-12 h-12 bg-accent/10 text-accent rounded-xl flex items-center justify-center">
            <Zap className="w-6 h-6" />
          </div>
          <div>
            <span className="text-xs font-bold text-base-content/60 uppercase">Total Leaderboard Usage</span>
            <h3 className="text-lg font-black mt-0.5">{fmt(topKeys.reduce((a, b) => a + (b.t || 0), 0))}</h3>
            <span className="text-xs text-base-content/50">{topKeys.reduce((a, b) => a + (b.req || 0), 0).toLocaleString()} total requests</span>
          </div>
        </div>
      </div>

      {/* Leaderboard Table Card */}
      <div className="card glass-card p-6 rounded-2xl text-left border border-base-content/5">
        <h3 className="font-extrabold text-sm mb-6 flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-primary" />
          Top tiêu dùng Token (Token Leaderboard)
        </h3>
        
        <div className="space-y-6">
          {topKeys.map((k, i) => {
            const pct = Math.round(((k.t || 0) / maxTokens) * 100) || 0;
            const barBg = CLR[i % CLR.length];
            const textCol = CLR_TEXT[i % CLR_TEXT.length];

            return (
              <div key={i} className="space-y-2 hover:bg-base-200/5 p-2 rounded-xl transition-all duration-200">
                <div className="flex justify-between items-center flex-wrap gap-2">
                  <div className="flex items-center gap-3">
                    <span className={`w-6 h-6 rounded-lg ${barBg}/15 ${textCol} font-extrabold flex items-center justify-center text-xs border border-white/5`}>
                      {i + 1}
                    </span>
                    <span className="font-bold text-sm text-base-content">{k.account_name}</span>
                    <code className="text-xs text-primary/80 bg-primary/5 px-2 py-0.5 rounded border border-primary/10 font-semibold select-all">
                      {k.full_key}
                    </code>
                  </div>
                  <div className="text-xs font-semibold text-base-content/70">
                    <span className="text-base-content font-bold">{fmt(k.t || 0)}</span> tokens · <span className="font-bold">{k.req?.toLocaleString()}</span> {t('requests_count', lang)}
                  </div>
                </div>

                <div className="relative w-full h-3 bg-base-200/50 rounded-full overflow-hidden border border-white/5">
                  <div 
                    className={`h-full ${barBg} rounded-full transition-all duration-500 ease-out`}
                    style={{ width: `${pct}%` }}
                  ></div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
