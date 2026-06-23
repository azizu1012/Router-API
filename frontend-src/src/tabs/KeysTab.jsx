import React, { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt, relt } from '../utils/format';
import { api } from '../utils/api';
import { Search, Trash2, Key, Plus, ShieldCheck, ShieldAlert, RefreshCw } from 'lucide-react';
import Loading from '../components/Loading';

export default function KeysTab() {
  const { tabData, token, lang, refreshTab } = useApp();
  const keys = tabData.ks || [];

  const [search, setSearch] = useState('');
  const [filterTier, setFilterTier] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');

  const [sortBy, setSortBy] = useState('key');
  const [sortOrder, setSortOrder] = useState('asc');

  const [newKey, setNewKey] = useState('');
  const [addMsg, setAddMsg] = useState({ text: '', type: '' });
  const [isAdding, setIsAdding] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);

  // Time ticker to update cooldown countdowns every 1s
  const [timeTicker, setTimeTicker] = useState(Date.now() / 1000);
  useEffect(() => {
    const interval = setInterval(() => {
      setTimeTicker(Date.now() / 1000);
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const handleSort = (field) => {
    if (sortBy === field) {
      setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(field);
      setSortOrder(field === 'key' || field === 'tier' ? 'asc' : 'desc');
    }
  };

  const isGeminiSearch = search.toLowerCase().trim() === 'gemini';
  const isHackerSearch = ['admin', 'root', 'hacker'].includes(search.toLowerCase().trim());

  useEffect(() => {
    if (isGeminiSearch) {
      window.dispatchEvent(new CustomEvent('spawn-custom-particles', { detail: { type: 'sparkles' } }));
      window.dispatchEvent(new CustomEvent('egg-unlocked-event', { detail: { id: 'gemini' } }));
    }
  }, [search, isGeminiSearch]);

  useEffect(() => {
    if (isHackerSearch) {
      window.dispatchEvent(new CustomEvent('egg-unlocked-event', { detail: { id: 'matrix' } }));
    }
  }, [search, isHackerSearch]);

  if (!tabData.ks) {
    return <Loading message={t('loading', lang)} />;
  }

  // Calculate Key Stats
  const healthy = keys.filter(k => k.enabled && !k.frozen).length;
  const frozen = keys.filter(k => k.frozen).length;
  const disabled = keys.filter(k => !k.enabled).length;

  // Filter keys list
  const filteredKeys = keys.filter(k => {
    const isFrozen = k.frozen_until > timeTicker;
    const statusKey = !k.enabled ? 'disabled' : isFrozen ? 'frozen' : k.consecutive_failures >= 3 ? 'degraded' : 'healthy';
    
    const matchesSearch = k.key.toLowerCase().includes(search.toLowerCase().trim());
    const matchesTier = filterTier === 'all' || k.tier === filterTier;
    const matchesStatus = filterStatus === 'all' || statusKey === filterStatus;
    
    return matchesSearch && matchesTier && matchesStatus;
  });

  const sortedKeys = [...filteredKeys].sort((a, b) => {
    let valA = a[sortBy];
    let valB = b[sortBy];

    if (sortBy === 'status') {
      const getStatusRank = (k) => {
        const isFrozen = k.frozen_until > timeTicker;
        if (!k.enabled) return 3;
        if (isFrozen) return 2;
        if (k.consecutive_failures >= 3) return 1;
        return 0; // Healthy first
      };
      valA = getStatusRank(a);
      valB = getStatusRank(b);
    } else if (sortBy === 'today') {
      valA = a.today || 0;
      valB = b.today || 0;
    } else if (sortBy === 'usage') {
      valA = a.usage || 0;
      valB = b.usage || 0;
    } else if (sortBy === 'concurrency') {
      valA = a.active_requests || 0;
      valB = b.active_requests || 0;
    } else if (sortBy === 'failures') {
      valA = a.consecutive_failures || 0;
      valB = b.consecutive_failures || 0;
    }

    if (typeof valA === 'string') {
      return sortOrder === 'asc' 
        ? valA.localeCompare(valB) 
        : valB.localeCompare(valA);
    }
    valA = valA || 0;
    valB = valB || 0;
    return sortOrder === 'asc' ? valA - valB : valB - valA;
  });

  // Handle Add Key
  const handleAddKey = async (e) => {
    e.preventDefault();
    if (!newKey.trim()) return;
    setIsAdding(true);
    setAddMsg({ text: '⏳ Đang thêm...', type: 'info' });
    try {
      await api('/dashboard/admin/keys/add', {
        method: 'POST',
        body: JSON.stringify({ api_key: newKey.trim() })
      }, token);
      
      setNewKey('');
      setAddMsg({ text: t('msg_key_added', lang), type: 'success' });
      refreshTab();
      setShowAddForm(false);
      setTimeout(() => setAddMsg({ text: '', type: '' }), 3000);
    } catch (err) {
      setAddMsg({ text: t('msg_key_error', lang) + err.message, type: 'error' });
    } finally {
      setIsAdding(false);
    }
  };

  // Handle Assign Pool
  const handleAssignPool = async (keyName, poolName) => {
    try {
      await api('/dashboard/admin/keys/pool', {
        method: 'POST',
        body: JSON.stringify({ key: keyName, pool: poolName })
      }, token);
      refreshTab();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  // Handle Delete Key
  const handleDeleteKey = async (keyName) => {
    if (!confirm(`Remove key ${keyName}?`)) return;
    try {
      await api('/dashboard/admin/keys/delete', {
        method: 'POST',
        body: JSON.stringify({ key: keyName })
      }, token);
      refreshTab();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  // Handle Toggle Key Status
  const handleToggleKey = async (keyName, currentEnabled) => {
    const newEnabled = !currentEnabled;
    try {
      await api('/dashboard/admin/keys/toggle', {
        method: 'POST',
        body: JSON.stringify({ key: keyName, enabled: newEnabled })
      }, token);
      refreshTab();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  // Handle Reset Key Failures / Cooldowns
  const handleResetKey = async (keyName) => {
    try {
      await api('/dashboard/admin/keys/reset', {
        method: 'POST',
        body: JSON.stringify({ key: keyName })
      }, token);
      refreshTab();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };


  const adminKeys = sortedKeys.filter(k => k.tier === 'admin');
  const premiumKeys = sortedKeys.filter(k => k.tier === 'premium');
  const freeKeys = sortedKeys.filter(k => k.tier === 'free');
  const otherKeys = sortedKeys.filter(k => k.tier !== 'admin' && k.tier !== 'premium' && k.tier !== 'free');
  const hasKeys = sortedKeys.length > 0;

  const SortTh = ({ field, label, className = '' }) => (
    <th
      onClick={() => handleSort(field)}
      className={`font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all select-none whitespace-nowrap ${className}`}
    >
      {label}{sortBy === field ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}
    </th>
  );

  const renderKeysTable = (keysList, title, badgeClass) => {
    if (keysList.length === 0) return null;
    return (
      <div className="card glass-card rounded-2xl overflow-hidden text-left border border-base-content/5 animate-fade-in-up">
        <div className="p-3 border-b border-base-content/5 flex items-center gap-2 bg-base-200/10">
          <h3 className="font-extrabold text-xs">{title}</h3>
          <span className={`badge badge-xs text-[9px] font-extrabold uppercase ${badgeClass}`}>
            {keysList.length}
          </span>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table table-zebra w-full text-xs">
            <thead>
              <tr className="border-b border-base-content/5 text-base-content/60 bg-base-200/35">
                <SortTh field="key" label={t('th_key_code', lang)} className="w-[28%] min-w-[180px]" />
                <SortTh field="status" label={t('th_status', lang)} className="w-[18%] min-w-[140px]" />
                <SortTh field="today" label={t('th_today', lang)} className="w-[8%] min-w-[70px]" />
                <SortTh field="usage" label={t('th_total', lang)} className="w-[9%] min-w-[70px]" />
                <SortTh field="concurrency" label={t('th_concurrency', lang)} className="w-[8%] min-w-[80px]" />
                <SortTh field="failures" label={t('th_failures', lang)} className="w-[7%] min-w-[70px]" />
                <th className="font-bold whitespace-nowrap w-[12%] min-w-[120px]">{t('lbl_pool_assign', lang)}</th>
                <th className="w-24 min-w-[96px]">Hành động</th>
              </tr>
            </thead>
            <tbody>
              {keysList.map((k, i) => {
                const isFrozen = k.frozen_until > timeTicker;
                const dotColor = !k.enabled ? 'bg-error/35 border-error/50' : isFrozen ? 'bg-warning animate-pulse' : k.consecutive_failures >= 3 ? 'bg-error' : 'bg-success';
                
                const statusText = !k.enabled ? t('st_disabled', lang) : isFrozen ? t('st_cooldown', lang) : k.consecutive_failures >= 3 ? t('st_degraded', lang) : t('st_healthy', lang);
                const statusBadgeClass = !k.enabled ? 'badge-ghost border-base-content/10 text-base-content/60' : isFrozen ? 'badge-warning/15 text-warning border-warning/30' : k.consecutive_failures >= 3 ? 'badge-error/15 text-error border-error/30' : 'badge-success/15 text-success border-success/30';
                
                const allowedPools = k.allowed_pools || [];
                const assignedPool = allowedPools.length > 0 ? allowedPools[0] : 'all';
 
                return (
                  <tr key={i} className="border-b border-base-content/5 hover:bg-base-200/50">
                    <td className="w-[28%] min-w-[180px] max-w-0">
                      <code
                        className="font-mono font-semibold text-primary block w-full overflow-hidden text-ellipsis whitespace-nowrap"
                        title={k.key}
                      >
                        {k.key}
                      </code>
                    </td>
                    <td>
                      <div className="flex items-center gap-2 whitespace-nowrap">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor}`}></span>
                        <span className={`badge badge-sm border font-semibold ${statusBadgeClass}`}>
                          {statusText}
                        </span>
                        {isFrozen && (
                          <span className="text-[10px] text-warning/90 font-bold">
                            ({relt(k.frozen_until)})
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="whitespace-nowrap">{fmt(k.today || 0)}</td>
                    <td className="whitespace-nowrap">{fmt(k.usage || 0)}</td>
                    <td className="font-bold">{k.active_requests || 0}</td>
                    <td>
                      {k.consecutive_failures > 0 ? (
                        <span className="text-error font-bold">{k.consecutive_failures}</span>
                      ) : '0'}
                    </td>
                    <td>
                      <select 
                        value={assignedPool}
                        onChange={(e) => handleAssignPool(k.key, e.target.value)}
                        className="select select-bordered select-xs text-[11px] h-7 min-h-7 w-full min-w-[110px] bg-base-200/80 focus:outline-none"
                      >
                        <option value="all">Tất cả (All)</option>
                        <option value="gemini-flash">gemini-flash</option>
                        <option value="gemini-flash-lite">flash-lite</option>
                      </select>
                    </td>
                    <td>
                      <div className="flex items-center gap-0.5">
                        <button 
                          onClick={() => handleToggleKey(k.key, k.enabled)}
                          className={`btn btn-ghost btn-xs btn-square ${k.enabled ? 'text-error hover:bg-error/15' : 'text-success hover:bg-success/15'}`}
                          title={k.enabled ? 'Disable Key' : 'Enable Key'}
                        >
                          {k.enabled ? <ShieldAlert className="w-3.5 h-3.5" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                        </button>
                        
                        {(k.consecutive_failures > 0 || isFrozen) && (
                          <button 
                            onClick={() => handleResetKey(k.key)}
                            className="btn btn-ghost btn-xs btn-square text-warning hover:bg-warning/15"
                            title="Reset Failures & Unfreeze"
                          >
                            <RefreshCw className="w-3.5 h-3.5" />
                          </button>
                        )}
 
                        <button 
                          onClick={() => handleDeleteKey(k.key)}
                          className="btn btn-ghost btn-xs btn-square text-error hover:bg-error/15"
                          title="Remove Key"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-5">
      {/* Title + Action Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 animate-tab-in">
        <div className="text-left">
          <h1 className="text-2xl font-black tracking-tight">{t('ks_title', lang)}</h1>
          <p className="text-xs text-base-content/60 mt-1">{t('ks_sub', lang)}</p>
        </div>
        <button
          onClick={() => setShowAddForm(v => !v)}
          className="btn btn-primary btn-sm gap-2 font-bold shadow-lg shadow-primary/25 hover:scale-[1.03] active:scale-[0.97] transition-all"
        >
          <Plus className="w-4 h-4" />
          {t('btn_add', lang)}
        </button>
      </div>

      {/* Collapsible Add Key Form */}
      {showAddForm && (
        <div className="card glass-card p-5 rounded-2xl text-left border border-primary/20 animate-fade-in-up">
          <h3 className="font-extrabold text-sm mb-3">{t('ks_add_key_title', lang)}</h3>
          <form onSubmit={handleAddKey} className="flex flex-col sm:flex-row gap-3">
            <input 
              type="password" 
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder={t('placeholder_auth_key', lang)}
              className="input input-bordered text-sm flex-1"
              autoComplete="off"
              spellCheck="false"
            />
            <button type="submit" disabled={isAdding} className="btn btn-primary font-bold">
              {isAdding ? <span className="loading loading-spinner loading-xs"></span> : t('btn_add', lang)}
            </button>
          </form>
          {addMsg.text && (
            <div className={`text-xs font-semibold mt-3 p-3 rounded-lg border ${
              addMsg.type === 'success' ? 'bg-success/10 text-success border-success/20' : 
              addMsg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-info/10 text-info border-info/20'
            }`}>
              {addMsg.text}
            </div>
          )}
        </div>
      )}

      {/* Stats Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 animate-fade-in-up cascade-1">
        {[
          { label: t('st_healthy', lang), value: healthy, color: 'bg-success', badge: 'text-success', border: 'border-success/20' },
          { label: t('st_cooldown', lang), value: frozen, color: 'bg-warning', badge: 'text-warning', border: 'border-warning/20' },
          { label: t('st_disabled', lang), value: disabled, color: 'bg-error', badge: 'text-error', border: 'border-error/20' },
          { label: t('ks_card_total', lang), value: keys.length, color: 'bg-primary', badge: 'text-primary', border: 'border-primary/20' },
        ].map(({ label, value, color, badge, border }) => (
          <div key={label} className={`card glass-card p-4 rounded-xl border ${border}`}>
            <div className="flex items-center gap-2 mb-2">
              <span className={`w-2 h-2 rounded-full shrink-0 ${color}`}></span>
              <span className="text-[10px] font-bold text-base-content/55 uppercase tracking-wider leading-none">{label}</span>
            </div>
            <div className={`text-2xl font-black leading-none ${badge}`}>{value}</div>
          </div>
        ))}
      </div>


      {/* Filter / Search Bar */}
      <div className="flex flex-wrap gap-3 items-center animate-fade-in-up cascade-2">
        {/* Search */}
        <div className="relative flex items-center flex-1 min-w-[160px] max-w-xs">
          <Search className="absolute left-3 w-4 h-4 text-base-content/50" />
          <input 
            type="text" 
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('placeholder_search_keys', lang)}
            className={`input input-bordered input-sm w-full pl-10 text-xs transition-all duration-300 ${
              isGeminiSearch ? 'bg-gradient-to-r from-amber-500 to-purple-600 text-white font-extrabold shadow-lg shadow-purple-500/25 border-none' :
              isHackerSearch ? 'matrix-text' : ''
            }`}
          />
        </div>

        {/* Tier filter */}
        <div className="flex items-center gap-2 text-xs">
          <span className="font-bold text-base-content/60">{t('lbl_tier', lang)}</span>
          <select value={filterTier} onChange={(e) => setFilterTier(e.target.value)} className="select select-bordered select-sm text-xs">
            <option value="all">{t('opt_all', lang)}</option>
            <option value="admin">{t('opt_admin', lang)}</option>
            <option value="premium">{t('opt_premium', lang)}</option>
            <option value="free">{t('opt_free', lang)}</option>
          </select>
        </div>
        
        {/* Status filter */}
        <div className="flex items-center gap-2 text-xs">
          <span className="font-bold text-base-content/60">{t('lbl_status', lang)}</span>
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} className="select select-bordered select-sm text-xs">
            <option value="all">{t('opt_all', lang)}</option>
            <option value="healthy">{t('opt_healthy', lang)}</option>
            <option value="frozen">{t('opt_cooldown', lang)}</option>
            <option value="disabled">{t('opt_disabled', lang)}</option>
          </select>
        </div>
      </div>

      {/* Grouped Tables – full width */}
      <div className="space-y-5">
        {!hasKeys ? (
          <div className="card glass-card p-12 text-center text-base-content/40 font-medium rounded-2xl">
            {t('no_keys_found', lang)}
          </div>
        ) : (
          <>
            {renderKeysTable(adminKeys, 'Khóa Admin (Admin Keys)', 'badge-primary')}
            {renderKeysTable(premiumKeys, 'Khóa Premium (Premium Keys)', 'badge-accent')}
            {renderKeysTable(freeKeys, 'Khóa Free (Free Keys)', 'badge-ghost border-base-content/15')}
            {renderKeysTable(otherKeys, 'Khóa Khác (Other Keys)', 'badge-ghost border-base-content/15')}
          </>
        )}
      </div>
    </div>
  );
}
