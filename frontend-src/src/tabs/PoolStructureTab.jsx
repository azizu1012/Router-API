import React, { useState, useEffect, useRef } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { ShieldCheck, Network, DollarSign, Activity, HelpCircle, ArrowRight, Zap, RefreshCw, Layers, Server, Cpu } from 'lucide-react';
import Loading from '../components/Loading';

function PoolFlowGraph({
  pool,
  liveStats,
  hoveredPool,
  setHoveredPool,
  hoveredModel,
  setHoveredModel,
  lang,
}) {
  const members = pool.members || (pool.models ? pool.models.split(',').map(m => ({alias: m.trim(), model_id: m.trim()})) : []);
  const poolLive = liveStats?.models?.[pool.name] || {};
  const poolRpmPct = poolLive.rpm_limit > 0 ? Math.round((1 - poolLive.rpm_remaining / poolLive.rpm_limit) * 100) : 0;
  const isCurrentPoolHovered = hoveredPool === pool.name;

  const containerRef = useRef(null);
  const gatewayPortRef = useRef(null);
  const balancerPortLeftRef = useRef(null);
  const balancerPortRightRef = useRef(null);
  const memberPortRefs = useRef({});

  const [coords, setCoords] = useState(null);

  const updateCoords = () => {
    if (!containerRef.current) return;
    const containerRect = containerRef.current.getBoundingClientRect();

    const getRelativeCenter = (el) => {
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      return {
        x: rect.left - containerRect.left + rect.width / 2,
        y: rect.top - containerRect.top + rect.height / 2,
      };
    };

    const gPort = getRelativeCenter(gatewayPortRef.current);
    const bPortL = getRelativeCenter(balancerPortLeftRef.current);
    const bPortR = getRelativeCenter(balancerPortRightRef.current);

    const mPorts = {};
    members.forEach((m) => {
      const alias = m.alias || m;
      const el = memberPortRefs.current[alias];
      if (el) {
        const pt = getRelativeCenter(el);
        if (pt) {
          mPorts[alias] = pt;
        }
      }
    });

    if (gPort && bPortL && bPortR) {
      setCoords({
        gateway: gPort,
        balancerLeft: bPortL,
        balancerRight: bPortR,
        members: mPorts,
      });
    }
  };

  useEffect(() => {
    updateCoords();

    const el = containerRef.current;
    if (!el) return;

    const observer = new ResizeObserver(() => {
      updateCoords();
    });
    observer.observe(el);

    window.addEventListener('resize', updateCoords);
    const timeoutId = setTimeout(updateCoords, 100);

    return () => {
      observer.disconnect();
      window.removeEventListener('resize', updateCoords);
      clearTimeout(timeoutId);
    };
  }, [pool.models, liveStats]);

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
    <div
      className={`card glass-card p-6 rounded-3xl border transition-all duration-500 relative overflow-hidden ${
        isCurrentPoolHovered ? 'border-primary/45 shadow-[0_0_20px_rgba(99,102,241,0.1)]' : 'border-base-content/5'
      }`}
      onMouseEnter={() => setHoveredPool(pool.name)}
      onMouseLeave={() => setHoveredPool(null)}
    >
      {/* Background gradient trace for visual aesthetics */}
      <div className="absolute top-0 right-0 w-64 h-64 bg-primary/5 rounded-full blur-3xl pointer-events-none"></div>

      {/* Pool Card Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-base-content/5 pb-4 mb-6">
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center border transition-all duration-300 ${
            isCurrentPoolHovered ? 'bg-primary/20 text-primary border-primary/40' : 'bg-primary/10 text-primary border-primary/20'
          }`}>
            <Layers className="w-5 h-5 animate-pulse" />
          </div>
          <div className="text-left">
            <h3 className="font-extrabold text-base text-base-content/90">
              {pool.name === 'gemini-flash' ? 'Gemini Flash Pool' : 'Gemini Lite Pool'}
            </h3>
            <span className="text-[10px] font-mono bg-base-200 px-2 py-0.5 rounded border border-base-content/5 text-base-content/60 uppercase">
              Alias: {pool.name}
            </span>
          </div>
        </div>

        {/* Pool capacity summaries */}
        <div className="flex gap-4 text-xs font-semibold">
          <div className="text-right">
            <span className="text-[10px] text-base-content/40 block uppercase">RPM khả dụng</span>
            <span className="font-mono font-bold text-base-content/80">
              {poolLive.rpm_remaining !== undefined ? `${poolLive.rpm_remaining} / ${poolLive.rpm_limit}` : pool.rpm}
            </span>
          </div>
          <div className="text-right border-l border-base-content/10 pl-4">
            <span className="text-[10px] text-base-content/40 block uppercase">TPM khả dụng (Token)</span>
            <span className="font-mono font-bold text-primary">
              {poolLive.tpm_remaining !== undefined ? `${fmt(poolLive.tpm_remaining)} / ${fmt(poolLive.tpm_limit)}` : pool.tpm}
            </span>
          </div>
          <div className="text-right border-l border-base-content/10 pl-4">
            <span className="text-[10px] text-base-content/40 block uppercase">Hoạt Động</span>
            <span className="badge badge-sm badge-success font-extrabold uppercase text-[9px]">Active</span>
          </div>
        </div>
      </div>

      {/* Sci-Fi Flow Graph Diagram */}
      <div ref={containerRef} className="relative flex flex-col lg:grid lg:grid-cols-[220px_1fr_300px] gap-8 lg:gap-16 items-center">

        {/* Connecting SVG Canvas */}
        <div className="hidden lg:block absolute inset-0 pointer-events-none z-10">
          <svg className="w-full h-full" style={{ overflow: 'visible' }}>
            <defs>
              <linearGradient id={`grad-to-pool-${pool.name}`} x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#10b981" />
                <stop offset="100%" stopColor="#8b5cf6" />
              </linearGradient>
              <filter id="glow-filter" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
              </filter>
            </defs>

            {coords && (
              <g>
                {/* Path 1: Client -> Pool Balancer (Left connection) */}
                {(() => {
                  const gPort = coords.gateway;
                  const bPortL = coords.balancerLeft;
                  const cpX1 = (gPort.x + bPortL.x) / 2;
                  const path1D = `M ${gPort.x},${gPort.y} C ${cpX1},${gPort.y} ${cpX1},${bPortL.y} ${bPortL.x},${bPortL.y}`;
                  return (
                    <>
                      <path
                        d={path1D}
                        fill="none"
                        stroke={`url(#grad-to-pool-${pool.name})`}
                        strokeWidth={isCurrentPoolHovered ? 3 : 2}
                        className="transition-all duration-300"
                        style={{ filter: isCurrentPoolHovered ? 'url(#glow-filter)' : 'none' }}
                      />
                      <path
                        d={path1D}
                        fill="none"
                        stroke="#ffffff"
                        strokeWidth={1.5}
                        strokeDasharray="6, 8"
                        className="flowing-line"
                        style={{ animationDuration: poolLive.active_requests > 0 ? '0.5s' : '1.5s' }}
                      />
                    </>
                  );
                })()}

                {/* Path 2 & 3: Pool Balancer -> Member Upstreams */}
                {members.map((m) => {
                  const alias = m.alias || m;
                  const mPort = coords.members[alias];
                  if (!mPort) return null;
                  const bPortR = coords.balancerRight;
                  const cpX2 = (bPortR.x + mPort.x) / 2;
                  const path2D = `M ${bPortR.x},${bPortR.y} C ${cpX2},${bPortR.y} ${cpX2},${mPort.y} ${mPort.x},${mPort.y}`;

                  const mLive = liveStats?.models?.[alias] || {};
                  const isFrozen = mLive.rpd_limit > 0 && mLive.rpd_used >= mLive.rpd_limit;
                  const isModelHovered = hoveredModel === alias;

                  const color = isFrozen ? '#f43f5e' : (isModelHovered || isCurrentPoolHovered ? '#8b5cf6' : '#6366f1');
                  const strokeW = isModelHovered ? 3.5 : (isCurrentPoolHovered ? 2.5 : 1.5);

                  return (
                    <g key={alias}>
                      {/* Main Bezier Connecting Path */}
                      <path
                        d={path2D}
                        fill="none"
                        stroke={color}
                        strokeWidth={strokeW}
                        className="transition-all duration-300"
                        style={{ filter: isModelHovered ? 'url(#glow-filter)' : 'none', opacity: isFrozen ? 0.35 : 1 }}
                      />
                      {/* Flow animation helper if model is active and not frozen */}
                      {!isFrozen && (
                        <path
                          d={path2D}
                          fill="none"
                          stroke={isModelHovered ? '#a855f7' : '#c084fc'}
                          strokeWidth={1.5}
                          strokeDasharray="4, 10"
                          className="flowing-line"
                          style={{
                            animationDuration: mLive.active_requests > 0 ? '0.6s' : '1.8s',
                            opacity: isCurrentPoolHovered || isModelHovered ? 1 : 0.4
                          }}
                        />
                      )}
                    </g>
                  );
                })}
              </g>
            )}
          </svg>
        </div>

        {/* Node 1: Client Gateway */}
        <div className="w-full flex flex-col items-center justify-center p-5 bg-base-200/40 border border-base-content/10 rounded-2xl h-36 relative z-20 shadow-md transition-all duration-300 hover:border-emerald-500/40 hover:shadow-[0_0_15px_rgba(16,185,129,0.1)]">
          <div className="absolute top-2 left-2 flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping"></span>
            <span className="text-[8px] font-mono text-emerald-400 uppercase font-semibold">GATEWAY ONLINE</span>
          </div>
          <Activity className="w-8 h-8 text-emerald-400 mb-2 animate-pulse" />
          <span className="text-[9px] font-bold text-base-content/40 uppercase tracking-widest">Client API Gateway</span>
          <code className="text-[10px] font-mono font-bold mt-2 text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">POST /v1/...</code>

          {/* Port indicator anchor with pulsing visual effect */}
          <div className="hidden lg:block absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 z-30">
            <div ref={gatewayPortRef} className="w-2.5 h-2.5 rounded-full border border-emerald-400 bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)] relative flex items-center justify-center">
              {poolLive.active_requests > 0 && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              )}
            </div>
          </div>
        </div>

        {/* Node 2: Pool Gatekeeper (Balancer) */}
        <div className="w-full flex justify-center items-center">
          <div
            className={`w-full max-w-[240px] flex flex-col justify-center p-5 rounded-2xl h-36 relative text-left border transition-all duration-300 z-20 ${
              isCurrentPoolHovered
                ? 'bg-gradient-to-tr from-primary/15 to-indigo-500/10 border-primary/45 shadow-inner shadow-primary/10'
                : 'bg-base-200/40 border-base-content/10 hover:border-primary/45 hover:shadow-[0_0_15px_rgba(99,102,241,0.1)]'
            }`}
          >
            {/* Port indicator anchors with dynamic ping animations */}
            <div className="hidden lg:block absolute left-0 top-1/2 -translate-y-1/2 -translate-x-1/2 z-30">
              <div ref={balancerPortLeftRef} className="w-2.5 h-2.5 rounded-full border border-primary bg-primary shadow-[0_0_8px_rgba(99,102,241,0.8)] relative flex items-center justify-center">
                {poolLive.active_requests > 0 && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                )}
              </div>
            </div>
            <div className="hidden lg:block absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 z-30">
              <div ref={balancerPortRightRef} className="w-2.5 h-2.5 rounded-full border border-primary bg-primary shadow-[0_0_8px_rgba(99,102,241,0.8)] relative flex items-center justify-center">
                {poolLive.active_requests > 0 && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between mb-3">
              <span className="text-[9px] font-bold text-primary uppercase tracking-widest flex items-center gap-1.5">
                <Zap className="w-3.5 h-3.5 text-primary" /> Pool Balancer
              </span>
              {poolLive.active_requests !== undefined && (
                <span className="badge badge-xs bg-primary/20 border-primary/30 font-mono font-bold text-[8px] text-primary">{poolLive.active_requests} ACTIVE</span>
              )}
            </div>

            <span className="text-sm font-black text-base-content/95 font-mono block mb-1">{pool.name}</span>

            {/* Realtime RPM Dial */}
            {poolLive.rpm_limit !== undefined ? (
              <div className="space-y-1.5 mt-2">
                <div className="flex justify-between text-[9px] font-bold">
                  <span className="text-base-content/50">RPM Load</span>
                  <span className="text-primary font-mono">{poolLive.rpm_limit - poolLive.rpm_remaining} / {poolLive.rpm_limit}</span>
                </div>
                <div className="w-full h-1.5 bg-base-content/20 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${poolRpmPct > 80 ? 'bg-error' : poolRpmPct > 50 ? 'bg-warning' : 'bg-primary'}`}
                    style={{width: `${poolRpmPct}%`}}
                  ></div>
                </div>
              </div>
            ) : (
              <div className="text-[10px] text-base-content/40 italic mt-3 flex items-center gap-2">
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                Syncing real-time feed...
              </div>
            )}
          </div>
        </div>

        {/* Node 3: Member Upstreams (Backing models) */}
        <div className="w-full flex flex-col gap-3 relative z-20 py-2">
          {members.map((m) => {
            const alias = m.alias || m;
            const modelId = m.model_id || alias;
            // Fetch live stats for individual member model
            const mLive = liveStats?.models?.[alias] || {};
            const mRpmPct = mLive.rpm_limit > 0 ? Math.round((1 - mLive.rpm_remaining / mLive.rpm_limit) * 100) : 0;

            const isFrozen = mLive.rpd_limit > 0 && mLive.rpd_used >= mLive.rpd_limit;
            const statusColor = isFrozen ? 'text-error bg-error/10 border-error/20' : 'text-success bg-success/10 border-success/20';
            const statusDot = isFrozen ? 'bg-error' : 'bg-success';
            const statusText = isFrozen ? 'COOLDOWN' : 'HEALTHY';
            const isCurrentModelHovered = hoveredModel === alias;

            return (
              <div
                key={alias}
                className={`p-3 rounded-2xl flex items-center justify-between gap-4 transition-all duration-300 border h-16 relative ${
                  isCurrentModelHovered
                    ? 'bg-base-200/60 border-purple-500/40 shadow-md scale-[1.02]'
                    : 'bg-base-200/25 border-base-content/5 hover:border-purple-500/30'
                }`}
                onMouseEnter={() => setHoveredModel(alias)}
                onMouseLeave={() => setHoveredModel(null)}
              >
                {/* Port indicator anchor with dynamic ping animation */}
                <div className="hidden lg:block absolute left-0 top-1/2 -translate-y-1/2 -translate-x-1/2 z-30">
                  <div
                    ref={(el) => { memberPortRefs.current[alias] = el; }}
                    className={`w-2.5 h-2.5 rounded-full border ${
                      isFrozen ? 'border-error bg-error shadow-[0_0_8px_rgba(244,63,94,0.8)]' : 'border-purple-500 bg-purple-500 shadow-[0_0_8px_rgba(139,92,246,0.8)]'
                    } relative flex items-center justify-center`}
                  >
                    {!isFrozen && mLive.active_requests > 0 && (
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-500 opacity-75"></span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2.5 min-w-0">
                  <div className={`p-1.5 rounded-lg border transition-all duration-300 ${
                    isCurrentModelHovered ? 'bg-purple-500/20 border-purple-500/35 text-purple-400' : 'bg-base-300 border-base-content/5 text-base-content/60'
                  }`}>
                    <Server className="w-4 h-4 shrink-0" />
                  </div>
                  <div className="min-w-0 text-left">
                    <span className="text-[11px] font-bold text-base-content/85 block truncate" title={getModelFriendlyName(modelId)}>
                      {getModelFriendlyName(modelId)}
                    </span>
                    <code className="text-[9px] font-mono text-base-content/40 block truncate">{modelId}</code>
                  </div>
                </div>

                {/* Live status gauges for member */}
                <div className="flex items-center gap-3 shrink-0">
                  {mLive.rpm_limit !== undefined ? (
                    <div className="flex flex-col gap-0.5 text-right font-mono text-[9px] min-w-[70px]">
                      <div>
                        <span className="text-[7px] text-base-content/40 uppercase font-semibold mr-1">RPM:</span>
                        <span className="font-extrabold text-base-content/70">{mLive.rpm_limit - mLive.rpm_remaining}/{mLive.rpm_limit}</span>
                      </div>
                      {mLive.tpm_limit > 0 && mLive.tpm_limit < 999999999 && (
                        <div>
                          <span className="text-[7px] text-base-content/40 uppercase font-semibold mr-1">TPM:</span>
                          <span className="font-extrabold text-primary">{fmt(mLive.tpm_remaining)}/{fmt(mLive.tpm_limit)}</span>
                        </div>
                      )}
                      {mLive.rpd_limit > 0 && mLive.rpd_limit < 999999 && (
                        <div>
                          <span className="text-[7px] text-base-content/40 uppercase font-semibold mr-1">RPD:</span>
                          <span className="font-extrabold text-warning">{mLive.rpd_limit - mLive.rpd_used}/{mLive.rpd_limit}</span>
                        </div>
                      )}
                    </div>
                  ) : (
                    <span className="text-[8px] text-base-content/30 font-mono animate-pulse">SYNCING...</span>
                  )}

                  <div className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-[8px] font-black ${statusColor}`}>
                    <span className={`w-1 h-1 rounded-full shrink-0 ${statusDot} ${!isFrozen && 'animate-pulse'}`}></span>
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
}

export default function PoolStructureTab() {
  const { tabData, lang, token, wsHook } = useApp();
  const muData = tabData.mu;
  const [liveStats, setLiveStats] = useState(null);
  const [hoveredPool, setHoveredPool] = useState(null);
  const [hoveredModel, setHoveredModel] = useState(null);

  useEffect(() => {
    if (!wsHook || !wsHook.connected) return;
    const unsub = wsHook.subscribe('stats:overview', (msg) => {
      if (msg.type === 'stats_snapshot') setLiveStats(msg);
    });
    return unsub;
  }, [wsHook, wsHook?.connected]);

  if (!muData) {
    return <Loading message={t('loading', lang) || 'Đang tải...'} />;
  }

  const savings = muData.savings || {};
  const pools = muData.pools || [];

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
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left transition-all hover:scale-[1.02] duration-300">
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
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left transition-all hover:scale-[1.02] duration-300">
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
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left transition-all hover:scale-[1.02] duration-300">
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
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left border border-success/25 bg-success/5 shadow-lg shadow-success/5 transition-all hover:scale-[1.02] duration-300">
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

        {pools.map((p) => (
          <PoolFlowGraph
            key={p.name}
            pool={p}
            liveStats={liveStats}
            hoveredPool={hoveredPool}
            setHoveredPool={setHoveredPool}
            hoveredModel={hoveredModel}
            setHoveredModel={setHoveredModel}
            lang={lang}
          />
        ))}
      </div>

      {/* Styled custom CSS for flowing animations */}
      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes flowing-dash {
          to {
            stroke-dashoffset: -20;
          }
        }
        .flowing-line {
          stroke-dasharray: 4, 6;
          animation: flowing-dash 1.2s linear infinite;
        }
      `}} />
    </div>
  );
}
