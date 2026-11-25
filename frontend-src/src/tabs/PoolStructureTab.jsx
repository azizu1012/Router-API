import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { api } from '../utils/api';
import { ShieldCheck, Network, DollarSign, Activity, ArrowRight, Zap, RefreshCw, Layers, Server, Edit3, X, Check } from 'lucide-react';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Alert from '../components/Alert';
import { useThemeTokens } from '../utils/themeTokens';

function PoolFlowGraph({
  pool,
  liveStats,
  hoveredPool,
  setHoveredPool,
  hoveredModel,
  setHoveredModel,
  lang,
  editMode,
  endpoints,
  onPoolAssign,
  assigning,
  selectKey,
}) {
  const members = useMemo(
    () => pool.members || (pool.models ? pool.models.split(',').map(m => ({ alias: m.trim(), model_id: m.trim() })) : []),
    [pool.members, pool.models],
  );
  const poolLive = liveStats?.models?.[pool.name] || {};
  const rpmUsed = poolLive.rpm_limit > 0 ? poolLive.rpm_limit - poolLive.rpm_remaining : 0;
  const poolRpmPct = poolLive.rpm_limit > 0 ? Math.round((rpmUsed / poolLive.rpm_limit) * 100) : 0;
  const isCurrentPoolHovered = hoveredPool === pool.name;
  const pal = useThemeTokens();

  const containerRef = useRef(null);
  const gatewayPortRef = useRef(null);
  const balancerPortLeftRef = useRef(null);
  const balancerPortRightRef = useRef(null);
  const memberPortRefs = useRef({});

  const [coords, setCoords] = useState(null);

  const updateCoords = useCallback(() => {
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
  }, [members]);

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
  }, [updateCoords]);

  /* Friendly names for the ids where the id itself reads badly. Everything else
     is derived from the id, so a model added to .env tomorrow renders as a name
     instead of falling through to a raw `gemini-3.9-flash`. */
  const MODEL_NAME_OVERRIDES = {
    'gemini-3.5-flash': 'Gemini 3.5 Flash (Latest)',
    'gemini-3-flash-preview': 'Gemini 3.0 Flash (Preview)',
    'gemini-2.5-flash': 'Gemini 2.5 Flash',
    'gemini-3.1-flash-lite': 'Gemini 3.1 Flash Lite',
    'gemini-2.5-flash-lite': 'Gemini 2.5 Flash Lite',
  };
  const ACRONYMS = { it: 'IT', ai: 'AI', vl: 'VL' };
  const titleToken = (tok) => {
    if (ACRONYMS[tok]) return ACRONYMS[tok];
    // 26b -> 26B, a4b -> A4B: the size suffix is capitalised, the number kept.
    if (/^\d+[a-z]+$/.test(tok)) return tok.replace(/[a-z]+$/, s => s.toUpperCase());
    if (/^[a-z]\d+[a-z]+$/.test(tok)) return tok.replace(/[a-z]+$/, s => s.toUpperCase()).replace(/^[a-z]/, s => s.toUpperCase());
    return tok.charAt(0).toUpperCase() + tok.slice(1);
  };
  const getModelFriendlyName = (modelId) => {
    if (!modelId) return '';
    if (MODEL_NAME_OVERRIDES[modelId]) return MODEL_NAME_OVERRIDES[modelId];
    return modelId.split('-').map(titleToken).join(' ');
  };

  const poolLabel = pool.name === 'gemini-flash' ? 'Gemini Flash Pool' : 'Gemini Lite Pool';
  const portSize = 'w-2 h-2 rounded-full';

  /* Below `lg` the three nodes stack, so the SVG connectors have nowhere to go.
     Instead of hiding them (which silently dropped the topology), each member
     row keeps a left-hand rule that reads as the same edge. */
  return (
    <Card padding="md">
      <div
        onMouseEnter={() => setHoveredPool(pool.name)}
        onMouseLeave={() => setHoveredPool(null)}
      >
        {/* ── Header ── */}
        <div className="flex flex-wrap items-start justify-between gap-3 pb-4 mb-5"
             style={{ borderBottom: '1px solid var(--c-line)' }}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                 style={{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }}>
              <Layers className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <h3 className="text-sm font-semibold" style={{ color: 'var(--c-ink)' }}>{poolLabel}</h3>
              <p className="text-[0.6875rem] font-mono mt-0.5" style={{ color: 'var(--c-ink-3)' }}>
                {t('pst_alias', lang)}: {pool.name}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <div>
              <span className="label-micro block">{t('pst_rpm_left', lang)}</span>
              <span className="text-xs font-medium tabular-nums" style={{ color: 'var(--c-ink-2)' }}>
                {poolLive.rpm_remaining !== undefined
                  ? `${poolLive.rpm_remaining.toLocaleString()} / ${poolLive.rpm_limit.toLocaleString()}`
                  : (pool.rpm ?? '—')}
              </span>
            </div>
            <div className="pl-5" style={{ borderLeft: '1px solid var(--c-line)' }}>
              <span className="label-micro block">{t('pst_tpm_left', lang)}</span>
              <span className="text-xs font-medium tabular-nums" style={{ color: 'var(--c-accent)' }}>
                {poolLive.tpm_remaining !== undefined
                  ? `${fmt(poolLive.tpm_remaining)} / ${fmt(poolLive.tpm_limit)}`
                  : (pool.tpm ?? '—')}
              </span>
            </div>
            <div className="pl-5" style={{ borderLeft: '1px solid var(--c-line)' }}>
              <span className="label-micro block">{t('pst_status', lang)}</span>
              <Badge variant="success" size="xs" dot>{t('pst_active', lang)}</Badge>
            </div>
          </div>
        </div>

        {/* ── Flow graph ── */}
        <div ref={containerRef} className="relative flex flex-col lg:grid lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,19rem)] gap-6 lg:gap-12 items-center">

          {/* Connectors. Only meaningful in the three-column layout. */}
          <div className="hidden lg:block absolute inset-0 pointer-events-none z-10">
            <svg className="w-full h-full" style={{ overflow: 'visible' }}>
              <defs>
                <linearGradient id={`grad-to-pool-${pool.name}`} x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor={pal?.good || 'currentColor'} />
                  <stop offset="100%" stopColor={pal?.accent || 'currentColor'} />
                </linearGradient>
                <filter id={`glow-${pool.name}`} x="-20%" y="-20%" width="140%" height="140%">
                  <feGaussianBlur stdDeviation="2.5" result="blur" />
                  <feComposite in="SourceGraphic" in2="blur" operator="over" />
                </filter>
              </defs>

              {coords && (
                <g>
                  {/* Edge 1: gateway → balancer */}
                  {(() => {
                    const gPort = coords.gateway;
                    const bPortL = coords.balancerLeft;
                    const cpX = (gPort.x + bPortL.x) / 2;
                    const d = `M ${gPort.x},${gPort.y} C ${cpX},${gPort.y} ${cpX},${bPortL.y} ${bPortL.x},${bPortL.y}`;
                    return (
                      <>
                        <path d={d} fill="none" stroke={`url(#grad-to-pool-${pool.name})`}
                              strokeWidth={isCurrentPoolHovered ? 2.5 : 2}
                              style={{ transition: 'stroke-width 180ms ease' }} />
                        <path d={d} fill="none" stroke={pal?.surface || 'transparent'}
                              strokeWidth={1.5} strokeDasharray="6, 10"
                              className={`flowing-line ${poolLive.active_requests > 0 ? 'flow-fast' : ''}`}
                              style={{ opacity: 0.9 }} />
                      </>
                    );
                  })()}

                  {/* Edges 2..n: balancer → members */}
                  {members.map((m) => {
                    const alias = m.alias || m;
                    const mPort = coords.members[alias];
                    if (!mPort) return null;
                    const bPortR = coords.balancerRight;
                    const cpX = (bPortR.x + mPort.x) / 2;
                    const d = `M ${bPortR.x},${bPortR.y} C ${cpX},${bPortR.y} ${cpX},${mPort.y} ${mPort.x},${mPort.y}`;

                    const mLive = liveStats?.models?.[alias] || {};
                    const isFrozen = mLive.rpd_limit > 0 && mLive.rpd_used >= mLive.rpd_limit;
                    const isModelHovered = hoveredModel === alias;

                    const stroke = isFrozen
                      ? pal?.critical
                      : (isModelHovered || isCurrentPoolHovered ? pal?.accent : pal?.ink3);
                    const strokeW = isModelHovered ? 2.5 : (isCurrentPoolHovered ? 2 : 1.5);

                    return (
                      <g key={alias}>
                        <path
                          d={d}
                          fill="none"
                          stroke={stroke}
                          strokeWidth={strokeW}
                          className="transition-all duration-200"
                          style={{
                            filter: isModelHovered ? `url(#glow-${pool.name})` : 'none',
                            opacity: isFrozen ? 0.4 : 1,
                          }}
                        />
                        {!isFrozen && (
                          <path
                            d={d}
                            fill="none"
                            stroke={pal?.surface || 'transparent'}
                            strokeWidth={1.5}
                            strokeDasharray="4, 10"
                            className={`flowing-line ${mLive.active_requests > 0 ? 'flow-fast' : ''}`}
                            style={{ opacity: isCurrentPoolHovered || isModelHovered ? 1 : 0.5 }}
                          />
                        )}
                      </g>
                    );
                  })}
                </g>
              )}
            </svg>
          </div>

          {/* Node 1: gateway */}
          <div className="w-full h-32 flex flex-col items-center justify-center relative z-20 rounded-xl"
               style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
            <div className="absolute top-2 left-2 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full animate-breathing" style={{ background: 'var(--c-good)' }} />
              <span className="label-micro" style={{ color: 'var(--c-good)' }}>{t('pst_gateway_online', lang)}</span>
            </div>
            <Activity className="w-6 h-6 mb-2" style={{ color: 'var(--c-good)' }} />
            <span className="text-[0.6875rem] font-medium" style={{ color: 'var(--c-ink-3)' }}>{t('pst_gateway', lang)}</span>
            <code className="text-[0.6875rem] font-mono font-medium mt-2 px-2 py-0.5 rounded-md"
                  style={{ color: 'var(--c-good)', background: 'color-mix(in srgb, var(--c-good) 10%, transparent)' }}>
              POST /v1/…
            </code>

            <div className="hidden lg:block absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 z-30">
              <div ref={gatewayPortRef} className={portSize}
                   style={{ background: 'var(--c-good)', boxShadow: '0 0 0 2px var(--c-surface)' }} />
            </div>
          </div>

          {/* Node 2: balancer */}
          <div className="w-full flex justify-center items-center">
            <div className="w-full max-w-[15rem] h-32 flex flex-col justify-center p-4 relative z-20 rounded-xl"
                 style={{
                   background: 'var(--c-surface-2)',
                   border: `1px solid ${isCurrentPoolHovered ? 'var(--c-line-strong)' : 'var(--c-line)'}`,
                   transition: 'border-color 180ms ease',
                 }}>
              <div className="hidden lg:block absolute left-0 top-1/2 -translate-y-1/2 -translate-x-1/2 z-30">
                <div ref={balancerPortLeftRef} className={portSize}
                     style={{ background: 'var(--c-accent)', boxShadow: '0 0 0 2px var(--c-surface)' }} />
              </div>
              <div className="hidden lg:block absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 z-30">
                <div ref={balancerPortRightRef} className={portSize}
                     style={{ background: 'var(--c-accent)', boxShadow: '0 0 0 2px var(--c-surface)' }} />
              </div>

              <div className="flex items-center justify-between mb-2.5 gap-2">
                <span className="label-micro flex items-center gap-1.5" style={{ color: 'var(--c-accent)' }}>
                  <Zap className="w-3 h-3" /> {t('pst_balancer', lang)}
                </span>
                {poolLive.active_requests !== undefined && (
                  <span className="text-[0.625rem] font-medium tabular-nums px-1.5 py-0.5 rounded"
                        style={{ color: 'var(--c-accent)', background: 'var(--c-accent-soft)' }}>
                    {poolLive.active_requests} {t('pst_active', lang).toLowerCase()}
                  </span>
                )}
              </div>

              <span className="text-sm font-semibold font-mono block mb-1" style={{ color: 'var(--c-ink)' }}>
                {pool.name}
              </span>

              {poolLive.rpm_limit !== undefined ? (
                <div className="space-y-1.5 mt-1">
                  <div className="flex justify-between text-[0.625rem] tabular-nums" style={{ color: 'var(--c-ink-3)' }}>
                    <span>{t('pst_rpm_load', lang)}</span>
                    <span style={{ color: 'var(--c-ink-2)' }}>{rpmUsed.toLocaleString()} / {poolLive.rpm_limit.toLocaleString()}</span>
                  </div>
                  <div className="meter" aria-hidden="true">
                    <span style={{
                      width: `${poolRpmPct}%`,
                      background: poolRpmPct > 80 ? 'var(--c-critical)' : poolRpmPct > 50 ? 'var(--c-warn)' : 'var(--c-accent)',
                    }} />
                  </div>
                </div>
              ) : (
                <div className="text-[0.6875rem] italic mt-2 flex items-center gap-2" style={{ color: 'var(--c-ink-3)' }}>
                  <RefreshCw className="w-3 h-3 animate-spin" />
                  {t('pst_syncing', lang)}
                </div>
              )}
            </div>
          </div>

          {/* Node 3: members */}
          <div className="w-full flex flex-col gap-2 relative z-20">
            {members.map((m) => {
              const alias = m.alias || m;
              const modelId = m.model_id || alias;
              const mLive = liveStats?.models?.[alias] || {};

              const isFrozen = mLive.rpd_limit > 0 && mLive.rpd_used >= mLive.rpd_limit;
              const isCurrentModelHovered = hoveredModel === alias;
              const statusColor = isFrozen ? 'var(--c-critical)' : 'var(--c-good)';

              return (
                <div
                  key={alias}
                  className="p-2.5 rounded-lg flex items-center justify-between gap-3 relative transition-colors duration-150"
                  style={{
                    background: isCurrentModelHovered ? 'var(--c-surface-3)' : 'var(--c-surface-2)',
                    border: `1px solid ${isCurrentModelHovered ? 'var(--c-line-strong)' : 'var(--c-line)'}`,
                  }}
                  onMouseEnter={() => setHoveredModel(alias)}
                  onMouseLeave={() => setHoveredModel(null)}
                >
                  <div className="hidden lg:block absolute left-0 top-1/2 -translate-y-1/2 -translate-x-1/2 z-30">
                    <div
                      ref={(el) => { memberPortRefs.current[alias] = el; }}
                      className={portSize}
                      style={{ background: statusColor, boxShadow: '0 0 0 2px var(--c-surface)' }}
                    />
                  </div>

                  {/* flex-1 against the shrink-0 stats block: the label gets
                      whatever is left over, and the id wraps rather than
                      trailing off in an ellipsis with no way to read it. */}
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <Server className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--c-ink-3)' }} />
                    <div className="min-w-0">
                      {/* Two lines, not one: the stats block is a fixed 162 px
                        and "Gemini 3.0 Flash (Preview)" loses 57 px to an
                        ellipsis at one line. Wrapping costs no extra row here -
                        the id below it already sets the card's height. */}
                      <span className="text-xs font-medium block leading-tight line-clamp-2" title={getModelFriendlyName(modelId)}
                            style={{ color: 'var(--c-ink)' }}>
                        {getModelFriendlyName(modelId)}
                      </span>
                      <code className="text-[0.625rem] font-mono block leading-tight break-words"
                            title={modelId} style={{ color: 'var(--c-ink-3)' }}>
                        {modelId}
                      </code>
                    </div>
                  </div>

                  <div className="flex items-center gap-2.5 shrink-0">
                    {mLive.rpm_limit !== undefined ? (
                      <div className="flex flex-col gap-0.5 text-right font-mono text-[0.625rem] tabular-nums">
                        <div>
                          <span className="label-micro mr-1">RPM</span>
                          <span style={{ color: 'var(--c-ink-2)' }}>{mLive.rpm_limit - mLive.rpm_remaining}/{mLive.rpm_limit}</span>
                        </div>
                        {mLive.tpm_limit > 0 && mLive.tpm_limit < 999999999 && (
                          <div>
                            <span className="label-micro mr-1">TPM</span>
                            <span style={{ color: 'var(--c-accent)' }}>{fmt(mLive.tpm_remaining)}/{fmt(mLive.tpm_limit)}</span>
                          </div>
                        )}
                        {mLive.rpd_limit > 0 && mLive.rpd_limit < 999999 && (
                          <div>
                            <span className="label-micro mr-1">RPD</span>
                            <span style={{ color: isFrozen ? 'var(--c-critical)' : 'var(--c-warn)' }}>
                              {mLive.rpd_limit - mLive.rpd_used}/{mLive.rpd_limit}
                            </span>
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="text-[0.625rem] animate-pulse" style={{ color: 'var(--c-ink-3)' }}>…</span>
                    )}

                    {/* Status is colour + text + dot: never colour alone. */}
                    <span className="flex items-center gap-1 text-[0.625rem] font-medium px-1.5 py-0.5 rounded"
                          style={{ color: statusColor, background: `color-mix(in srgb, ${statusColor} 12%, transparent)` }}>
                      <span className="w-1 h-1 rounded-full" style={{ background: statusColor }} />
                      {isFrozen ? t('pst_cooldown', lang) : t('pst_healthy', lang)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* ── Assignment editor ── */}
        {editMode && (
          <div className="mt-5 pt-4 space-y-3" style={{ borderTop: '1px solid var(--c-line)' }}>
            <span className="label-micro flex items-center gap-1.5">
              <Layers className="w-3 h-3" /> {t('pst_assignments', lang)}
            </span>

            <div className="flex flex-wrap gap-2">
              {pool.members
                .filter(m => m.endpoint_name)
                .map((m) => (
                  <div key={m.endpoint_name}
                       className="flex items-center gap-1.5 rounded-md px-2 py-1"
                       style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
                    <span className="text-[0.6875rem] font-medium" style={{ color: 'var(--c-accent)' }}>{m.endpoint_name}</span>
                    <ArrowRight className="w-2.5 h-2.5" style={{ color: 'var(--c-ink-3)' }} />
                    <code className="text-[0.6875rem] font-mono" style={{ color: 'var(--c-ink-2)' }}>{m.model_id}</code>
                    <Button
                      variant="ghost"
                      size="xs"
                      icon={X}
                      onClick={() => onPoolAssign(m.endpoint_name, pool.name, '')}
                      disabled={assigning === pool.name}
                      className="ml-0.5"
                      style={{ color: 'var(--c-critical)' }}
                      aria-label={t('pst_remove', lang)}
                      title={t('pst_remove', lang)}
                    >
                      <span className="sr-only">{t('pst_remove', lang)}</span>
                    </Button>
                  </div>
                ))}
              {pool.members.filter(m => m.endpoint_name).length === 0 && (
                <span className="text-[0.6875rem] italic" style={{ color: 'var(--c-ink-3)' }}>
                  {t('pst_assign_empty', lang)}
                </span>
              )}
            </div>

            {endpoints.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <span className="label-micro mr-1">{t('pst_assign_add', lang)}</span>
                <select
                  key={selectKey}
                  onChange={(e) => {
                    if (!e.target.value) return;
                    const [epName, modelId] = e.target.value.split('|');
                    onPoolAssign(epName, pool.name, modelId);
                    e.target.value = '';
                  }}
                  disabled={assigning === pool.name}
                  className="select select-sm text-xs font-mono max-w-[15rem]"
                >
                  <option value="">{t('pst_assign_choose', lang)}</option>
                  {endpoints.filter(e => e.enabled && e.enabled_models?.length).map(ep => (
                    ep.enabled_models.map(mid => (
                      <option key={ep.name + '|' + mid} value={ep.name + '|' + mid}>
                        {ep.name} → {mid}
                      </option>
                    ))
                  ))}
                </select>
                {assigning === pool.name && <span className="loading loading-spinner loading-xs" />}
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

export default function PoolStructureTab() {
  const { tabData, lang, token, wsHook } = useApp();
  const muData = tabData.mu;
  const endpoints = tabData.ep || [];

  const [liveStats, setLiveStats] = useState(null);
  const [hoveredPool, setHoveredPool] = useState(null);
  const [hoveredModel, setHoveredModel] = useState(null);
  const [editMode, setEditMode] = useState(false);
  const [assigning, setAssigning] = useState(null);
  const [selectKey, setSelectKey] = useState(0);

  useEffect(() => {
    if (!wsHook || !wsHook.connected) return;
    const unsub = wsHook.subscribe('stats:overview', (msg) => {
      if (msg.type === 'stats_snapshot') setLiveStats(msg);
    });
    return unsub;
  }, [wsHook, wsHook?.connected]);

  const [msg, setMsg] = useState({ text: '', type: '' });

  const showMsg = (text, type) => {
    setMsg({ text, type });
    setTimeout(() => setMsg({ text: '', type: '' }), 3000);
  };

  const handlePoolAssign = async (epName, poolName, modelId) => {
    setAssigning(poolName);
    try {
      await api('/dashboard/admin/endpoints/pool-assign', {
        method: 'POST',
        body: JSON.stringify({ name: epName, pool_name: poolName, model_id: modelId || '' }),
      }, token);
      showMsg(t('pst_assign_ok', lang), 'success');
      setSelectKey(k => k + 1);
    } catch (err) {
      showMsg(`${t('pst_assign_err', lang)}: ${err.message}`, 'error');
    } finally {
      setAssigning(null);
    }
  };

  if (!muData) return <Loading message={t('loading', lang)} />;

  const savings = muData.savings || {};
  const pools = muData.pools || [];

  /* Cost savings: four figures that all answer "what would this have cost".
     Standard → cached → actual → net, in that order, so the subtraction reads
     left to right. Only "net" carries accent weight. */
  const money = [
    { label: t('mu_std_cost', lang), sub: t('mu_std_cost_sub', lang), value: savings.standard_cost, color: 'var(--c-ink)', icon: DollarSign },
    { label: t('mu_cache_cost', lang), sub: t('mu_cache_cost_sub', lang), value: savings.cached_cost, color: 'var(--c-ink-2)', icon: DollarSign },
    { label: t('mu_gemini_cost', lang), sub: t('mu_gemini_cost_sub', lang), value: savings.gemini_cost, color: 'var(--c-warn)', icon: DollarSign },
    { label: t('mu_net_save', lang), sub: t('mu_net_save_sub', lang), value: savings.net_savings, color: 'var(--c-good)', icon: ShieldCheck },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t('mu_title', lang)}
        subtitle={t('mu_sub', lang)}
        actions={
          <Button
            variant={editMode ? 'primary' : 'outline'}
            size="sm"
            icon={editMode ? Check : Edit3}
            onClick={() => setEditMode(v => !v)}
          >
            {editMode ? t('pst_done', lang) : t('pst_edit', lang)}
          </Button>
        }
      />

      {msg.text && <Alert type={msg.type} message={msg.text} />}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 animate-fade-in-up">
        {money.map(({ label, sub, value, color, icon: Icon }) => (
          <Card key={label} padding="md" className="glass-card-interactive">
            <div className="flex items-start justify-between gap-2 mb-3">
              <span className="label-micro leading-snug">{label}</span>
              <Icon className="w-3.5 h-3.5 shrink-0" style={{ color }} />
            </div>
            <div className="text-[1.5rem] leading-none font-semibold tabular-nums"
                 style={{ color, letterSpacing: '-0.02em' }}>
              ${(value || 0).toFixed(4)}
            </div>
            <p className="text-[0.6875rem] mt-2 leading-relaxed" style={{ color: 'var(--c-ink-3)' }}>{sub}</p>
          </Card>
        ))}
      </div>

      <div className="space-y-4 animate-fade-in-up cascade-2">
        <h2 className="text-xs font-semibold flex items-center gap-2" style={{ color: 'var(--c-ink-2)' }}>
          <Network className="w-3.5 h-3.5" style={{ color: 'var(--c-ink-3)' }} />
          {t('pst_flow_title', lang)}
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
            editMode={editMode}
            endpoints={endpoints}
            onPoolAssign={handlePoolAssign}
            assigning={assigning}
            selectKey={selectKey}
          />
        ))}
      </div>
    </div>
  );
}