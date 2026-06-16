import React, { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { useWebSocket } from '../utils/useWebSocket';
import { ShieldCheck, Network, DollarSign, Activity, HelpCircle, ArrowRight, Zap, RefreshCw, Layers, Server } from 'lucide-react';
import Loading from '../components/Loading';

export default function PoolStructureTab() {
  const { tabData, lang, token } = useApp();
  const muData = tabData.mu;
  const [liveStats, setLiveStats] = useState(null);

  const wsHook = useWebSocket(token);

  useEffect(() => {
    if (!wsHook.connected) return;
    const unsub = wsHook.subscribe('stats:overview', (msg) => {
      if (msg.type === 'stats_snapshot') setLiveStats(msg);
    });
    return unsub;
  }, [wsHook.connected]);

  if (!muData) {
    return <Loading message={t('loading', lang) || 'Đang tải...'} />;
  }

  const savings = muData.savings || {};
  const pools = muData.pools || [];

  // Helper to parse comma separated backing models into array
  const getPoolMembers = (modelsStr) => {
    if (!modelsStr) return [];
    return modelsStr.split(',').map(m => m.trim());
  };

  // Helper to resolve display text/friendly name for backing model
  const getModelFriendlyName = (modelId) => {
    const names = {
      'gemini-3.5-flash': 'Gemini 3.5 Flash (Latest)',
      'gemini-3-flash-preview': 'Gemini 3.0 Flash (Preview)',
      'gemini-2.5-flash': 'Gemini 2.5 Flash',
      'gemini-3.1-flash-lite': 'Gemini 3.1 Flash Lite',
      'gemini-2.5-flash-lite': 'Gemini 2.5 Flash Lite',
    };
    return names[modelId] || modelId;
  };

  return (
    <div className="space-y-6">
      {/* Title */}
      <div className="text-left animate-tab-in">
        <h1 className="text-2xl font-black tracking-tight flex items-center gap-2">
          <Network className="w-6 h-6 text-primary" />
          Cấu trúc Tài nguyên & Tiết kiệm
        </h1>
        <p className="text-xs text-base-content/60 mt-1">
          Báo cáo thống kê hiệu quả tiết kiệm chi phí nhờ Smart Caching và sơ đồ luồng định tuyến (Model Pools) thời gian thực.
        </p>
      </div>

      {/* Cost Savings Analytics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 animate-fade-in-up cascade-1">
        {/* Standard Cost */}
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-base-content/60 uppercase">Chi phí gốc (Standard Cost)</span>
            <div className="w-8 h-8 rounded-lg bg-base-content/10 text-base-content flex items-center justify-center">
              <DollarSign className="w-4 h-4" />
            </div>
          </div>
          <span className="text-2xl font-black">${(savings.standard_cost || 0).toFixed(4)}</span>
          <span className="text-xs text-base-content/50 mt-1">Ước tính theo giá Claude</span>
        </div>

        {/* Cached Cost */}
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-base-content/60 uppercase">Chi phí Caching (Cached Cost)</span>
            <div className="w-8 h-8 rounded-lg bg-cyan-500/10 text-cyan-400 flex items-center justify-center">
              <DollarSign className="w-4 h-4" />
            </div>
          </div>
          <span className="text-2xl font-black text-cyan-400">${(savings.cached_cost || 0).toFixed(4)}</span>
          <span className="text-xs text-base-content/50 mt-1">Lượng token đã cache</span>
        </div>

        {/* Gemini actual cost */}
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-base-content/60 uppercase">Chi phí Gemini (Gemini Cost)</span>
            <div className="w-8 h-8 rounded-lg bg-warning/10 text-warning flex items-center justify-center">
              <DollarSign className="w-4 h-4" />
            </div>
          </div>
          <span className="text-2xl font-black text-warning">${(savings.gemini_cost || 0).toFixed(4)}</span>
          <span className="text-xs text-base-content/50 mt-1">Thực chi tại Gemini API</span>
        </div>

        {/* Net Savings */}
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left border border-success/25 bg-success/5 shadow-lg shadow-success/5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-success uppercase">Tổng tiết kiệm (Net Savings)</span>
            <div className="w-8 h-8 rounded-lg bg-success/20 text-success flex items-center justify-center">
              <ShieldCheck className="w-4 h-4" />
            </div>
          </div>
          <span className="text-2xl font-black text-success">${(savings.net_savings || 0).toFixed(4)}</span>
          <span className="text-xs text-success/80 mt-1">Đã tối ưu hóa thông minh</span>
        </div>
      </div>

      {/* Visual Pools Section */}
      <div className="space-y-6 animate-fade-in-up cascade-2">
        <h2 className="text-sm font-extrabold uppercase tracking-wider text-base-content/40 text-left">
          Sơ đồ Định tuyến Mạng lưới Pool (Pool Routing Flow)
        </h2>

        {pools.map((p, poolIdx) => {
          const members = getPoolMembers(p.models);
          // Look up live statistics for this pool
          const poolLive = liveStats?.models?.[p.name] || {};
          const poolRpmPct = poolLive.rpm_limit > 0 ? Math.round((1 - poolLive.rpm_remaining / poolLive.rpm_limit) * 100) : 0;
          
          return (
            <div key={p.name} className="card glass-card p-6 rounded-3xl border border-base-content/5 relative overflow-hidden">
              {/* Background gradient trace for visual aesthetics */}
              <div className="absolute top-0 right-0 w-64 h-64 bg-primary/5 rounded-full blur-3xl pointer-events-none"></div>

              {/* Pool Card Header */}
              <div className="flex flex-wrap items-center justify-between gap-4 border-b border-base-content/5 pb-4 mb-6">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center border border-primary/20">
                    <Layers className="w-5 h-5 animate-pulse" />
                  </div>
                  <div className="text-left">
                    <h3 className="font-extrabold text-base text-base-content/90">{p.name === 'gemini-flash' ? 'Gemini Flash Pool' : 'Gemini Lite Pool'}</h3>
                    <span className="text-[10px] font-mono bg-base-200 px-2 py-0.5 rounded border border-base-content/5 text-base-content/60 uppercase">Alias: {p.name}</span>
                  </div>
                </div>

                {/* Pool capacity summaries */}
                <div className="flex gap-4 text-xs font-semibold">
                  <div className="text-right">
                    <span className="text-[10px] text-base-content/40 block uppercase">Tổng Giới Hạn RPM</span>
                    <span className="font-bold text-base-content/80">{p.rpm}</span>
                  </div>
                  <div className="text-right border-l border-base-content/10 pl-4">
                    <span className="text-[10px] text-base-content/40 block uppercase">Tổng Giới Hạn TPM</span>
                    <span className="font-bold text-base-content/80">{p.tpm}</span>
                  </div>
                  <div className="text-right border-l border-base-content/10 pl-4">
                    <span className="text-[10px] text-base-content/40 block uppercase">Hoạt Động</span>
                    <span className="badge badge-sm badge-success font-extrabold uppercase text-[9px]">Active</span>
                  </div>
                </div>
              </div>

              {/* Flow Graph Diagram (Client -> Pool Gatekeeper -> Backing Models) */}
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.5fr_2.5fr] gap-4 items-center relative z-10">
                
                {/* Column 1: Client Gateway */}
                <div className="flex flex-col items-center justify-center p-4 bg-base-200/20 border border-base-content/5 rounded-2xl h-44 relative">
                  <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-ping absolute -top-1 -right-1"></div>
                  <Activity className="w-8 h-8 text-emerald-400 mb-2 animate-pulse" />
                  <span className="text-[10px] font-bold text-base-content/40 uppercase tracking-widest">Client API Gateway</span>
                  <code className="text-xs font-mono font-bold mt-2 text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-full border border-emerald-500/20">POST /v1/chat/completions</code>
                  
                  {/* SVG Right Connection Point */}
                  <div className="hidden lg:block absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 w-4 h-4 rounded-full bg-base-300 border border-base-content/10 z-20">
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 m-1"></div>
                  </div>
                </div>

                {/* Column 2: Pool Gatekeeper (Balancer) */}
                <div className="flex flex-col justify-center p-5 bg-gradient-to-tr from-primary/10 to-indigo-500/5 border border-primary/25 rounded-2xl h-44 relative text-left">
                  {/* Flow Connector Line from Left */}
                  <div className="hidden lg:block absolute left-0 top-1/2 -translate-y-1/2 -translate-x-full h-0.5 bg-gradient-to-r from-emerald-500 to-primary w-full -z-10">
                    <div className="w-2 h-2 rounded-full bg-white absolute top-1/2 -translate-y-1/2 left-0 animate-ping"></div>
                  </div>

                  <div className="flex items-center justify-between mb-3">
                    <span className="text-[10px] font-bold text-primary uppercase tracking-widest flex items-center gap-1">
                      <Zap className="w-3.5 h-3.5" /> Pool Gatekeeper
                    </span>
                    {poolLive.active_requests !== undefined && (
                      <span className="badge badge-xs badge-ghost border-primary/20 font-bold text-[9px] text-primary">{poolLive.active_requests} active</span>
                    )}
                  </div>
                  
                  <span className="text-sm font-black text-base-content/95 font-mono block mb-1">{p.name}</span>
                  
                  {/* Realtime RPM Dial */}
                  {poolLive.rpm_limit !== undefined ? (
                    <div className="space-y-1.5 mt-2">
                      <div className="flex justify-between text-[9px] font-bold">
                        <span className="text-base-content/50">RPM Load</span>
                        <span className="text-primary font-mono">{poolLive.rpm_limit - poolLive.rpm_remaining} / {poolLive.rpm_limit}</span>
                      </div>
                      <div className="w-full h-1.5 bg-base-300/50 rounded-full overflow-hidden">
                        <div 
                          className={`h-full rounded-full transition-all duration-500 ${poolRpmPct > 80 ? 'bg-error' : poolRpmPct > 50 ? 'bg-warning' : 'bg-primary'}`} 
                          style={{width: `${poolRpmPct}%`}}
                        ></div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-[10px] text-base-content/40 italic mt-3">Waiting for websocket sync...</div>
                  )}

                  {/* SVG Right Connection Point */}
                  <div className="hidden lg:block absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 w-4 h-4 rounded-full bg-base-300 border border-base-content/10 z-20">
                    <div className="w-1.5 h-1.5 rounded-full bg-primary m-1"></div>
                  </div>
                </div>

                {/* Column 3: Member Upstreams (Backing models) */}
                <div className="flex flex-col gap-3 relative text-left py-2">
                  {/* Flow Connector Line from Left */}
                  <div className="hidden lg:block absolute left-0 top-1/2 -translate-y-1/2 -translate-x-full h-0.5 bg-gradient-to-r from-primary to-indigo-500 w-full -z-10"></div>

                  {members.map((m, mIdx) => {
                    // Fetch live stats for individual member model
                    const mLive = liveStats?.models?.[m] || {};
                    const mRpmPct = mLive.rpm_limit > 0 ? Math.round((1 - mLive.rpm_remaining / mLive.rpm_limit) * 100) : 0;
                    
                    const isFrozen = mLive.rpd_limit > 0 && mLive.rpd_used >= mLive.rpd_limit;
                    const statusColor = isFrozen ? 'text-error bg-error/10 border-error/20' : 'text-success bg-success/10 border-success/20';
                    const statusDot = isFrozen ? 'bg-error' : 'bg-success';
                    const statusText = isFrozen ? 'Cooldown (Quota)' : 'Healthy';

                    return (
                      <div 
                        key={m} 
                        className="p-3 bg-base-200/35 border border-base-content/5 rounded-2xl flex items-center justify-between gap-4 transition-all duration-300 hover:border-primary/20 hover:scale-[1.01]"
                      >
                        <div className="flex items-center gap-2.5 min-w-0">
                          <Server className="w-4 h-4 text-indigo-400 shrink-0" />
                          <div className="min-w-0">
                            <span className="text-[11px] font-bold text-base-content/85 block truncate" title={getModelFriendlyName(m)}>
                              {getModelFriendlyName(m)}
                            </span>
                            <code className="text-[9px] font-mono text-base-content/50 block truncate">{m}</code>
                          </div>
                        </div>

                        {/* Live status gauges for member */}
                        <div className="flex items-center gap-3 shrink-0">
                          {mLive.rpm_limit !== undefined ? (
                            <div className="text-right font-mono text-[10px]">
                              <span className="text-[8px] text-base-content/40 uppercase block leading-none">RPM Load</span>
                              <span className="font-bold text-base-content/70">{mLive.rpm_limit - mLive.rpm_remaining}/{mLive.rpm_limit}</span>
                            </div>
                          ) : (
                            <span className="text-[9px] text-base-content/40 italic">Waiting...</span>
                          )}

                          <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[9px] font-bold ${statusColor}`}>
                            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot} ${!isFrozen && 'animate-pulse'}`}></span>
                            <span>{statusText}</span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

              </div>

            </div>
          );
        })}
      </div>

      {/* Styled custom CSS for flowing animations */}
      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes flowing-dash {
          to {
            stroke-dashoffset: -20;
          }
        }
        .flowing-line {
          stroke-dasharray: 4, 4;
          animation: flowing-dash 1s linear infinite;
        }
      `}} />
    </div>
  );
}
