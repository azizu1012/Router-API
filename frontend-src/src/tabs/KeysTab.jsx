import { Fragment, useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt, relt } from '../utils/format';
import { api } from '../utils/api';
import { Search, Trash2, Plus, ShieldCheck, ShieldAlert, RefreshCw } from 'lucide-react';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Alert from '../components/Alert';
import Card from '../components/Card';

/* Status is the one place colour is allowed to shout — and it never shouts
   alone: every state below pairs its colour with a word. */
const STATUS = {
  healthy: { dot: 'var(--c-good)', text: 'st_healthy', variant: 'success' },
  degraded: { dot: 'var(--c-critical)', text: 'st_degraded', variant: 'error' },
  frozen: { dot: 'var(--c-warn)', text: 'st_cooldown', variant: 'warning' },
  disabled: { dot: 'var(--c-ink-3)', text: 'st_disabled', variant: 'ghost' },
};

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
  const [timeTicker, setTimeTicker] = useState(() => Date.now() / 1000);
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

  if (!tabData.ks) return <Loading message={t('loading', lang)} />;

  const statusOf = (k) => {
    const isFrozen = k.frozen_until > timeTicker;
    if (!k.enabled) return 'disabled';
    if (isFrozen) return 'frozen';
    if (k.consecutive_failures >= 3) return 'degraded';
    return 'healthy';
  };

  const healthy = keys.filter(k => statusOf(k) === 'healthy').length;
  const frozen = keys.filter(k => statusOf(k) === 'frozen').length;
  const disabled = keys.filter(k => statusOf(k) === 'disabled').length;

  const filteredKeys = keys.filter(k => {
    const statusKey = statusOf(k);
    const matchesSearch = k.key.toLowerCase().includes(search.toLowerCase().trim());
    const matchesTier = filterTier === 'all' || k.tier === filterTier;
    const matchesStatus = filterStatus === 'all' || statusKey === filterStatus;
    return matchesSearch && matchesTier && matchesStatus;
  });

  const RANK = { healthy: 0, degraded: 1, frozen: 2, disabled: 3 };
  const sortedKeys = [...filteredKeys].sort((a, b) => {
    let valA = a[sortBy];
    let valB = b[sortBy];

    if (sortBy === 'status') {
      valA = RANK[statusOf(a)];
      valB = RANK[statusOf(b)];
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
      return sortOrder === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
    }
    valA = valA || 0;
    valB = valB || 0;
    return sortOrder === 'asc' ? valA - valB : valB - valA;
  });

  const handleAddKey = async (e) => {
    e.preventDefault();
    if (!newKey.trim()) return;
    setIsAdding(true);
    setAddMsg({ text: t('ks_adding', lang), type: 'info' });
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

  const handleAssignPool = async (keyName, poolName) => {
    try {
      await api('/dashboard/admin/keys/pool', {
        method: 'POST',
        body: JSON.stringify({ key: keyName, pool: poolName })
      }, token);
      refreshTab();
    } catch (err) {
      setAddMsg({ text: `${t('ks_assign_err', lang)}: ${err.message}`, type: 'error' });
    }
  };

  const handleDeleteKey = async (keyName) => {
    if (!window.confirm(t('ks_remove_confirm', lang))) return;
    try {
      await api('/dashboard/admin/keys/delete', {
        method: 'POST',
        body: JSON.stringify({ key: keyName })
      }, token);
      refreshTab();
    } catch (err) {
      setAddMsg({ text: `${t('err_generic', lang)}: ${err.message}`, type: 'error' });
    }
  };

  const handleToggleKey = async (keyName, currentEnabled) => {
    try {
      await api('/dashboard/admin/keys/toggle', {
        method: 'POST',
        body: JSON.stringify({ key: keyName, enabled: !currentEnabled })
      }, token);
      refreshTab();
    } catch (err) {
      setAddMsg({ text: `${t('err_generic', lang)}: ${err.message}`, type: 'error' });
    }
  };

  const handleResetKey = async (keyName) => {
    try {
      await api('/dashboard/admin/keys/reset', {
        method: 'POST',
        body: JSON.stringify({ key: keyName })
      }, token);
      refreshTab();
    } catch (err) {
      setAddMsg({ text: `${t('err_generic', lang)}: ${err.message}`, type: 'error' });
    }
  };

  const groups = [
    { tier: 'admin', rows: sortedKeys.filter(k => k.tier === 'admin'), label: 'ks_group_admin' },
    { tier: 'premium', rows: sortedKeys.filter(k => k.tier === 'premium'), label: 'ks_group_premium' },
    { tier: 'free', rows: sortedKeys.filter(k => k.tier === 'free'), label: 'ks_group_free' },
    {
      tier: 'other',
      rows: sortedKeys.filter(k => !['admin', 'premium', 'free'].includes(k.tier)),
      label: 'ks_group_other',
    },
  ];

  const stats = [
    { key: 'healthy', value: healthy, color: 'var(--c-good)' },
    { key: 'frozen', value: frozen, color: 'var(--c-warn)' },
    { key: 'disabled', value: disabled, color: 'var(--c-ink-3)' },
  ];

  const SortTh = ({ field, label, className = '' }) => (
    <th
      onClick={() => handleSort(field)}
      className={`cursor-pointer select-none ${className}`}
      aria-sort={sortBy === field ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        <span aria-hidden="true" className="tabular-nums opacity-60">
          {sortBy === field ? (sortOrder === 'asc' ? '▲' : '▼') : ''}
        </span>
      </span>
    </th>
  );

  const renderKeysTable = (rows, labelKey) => {
    if (rows.length === 0) return null;
    return (
      <div className="glass-card rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5"
             style={{ borderBottom: '1px solid var(--c-line)' }}>
          <h3 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{t(labelKey, lang)}</h3>
          <span className="text-[0.6875rem] tabular-nums px-1.5 py-0.5 rounded"
                style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink-3)' }}>
            {rows.length}
          </span>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table w-full">
            <thead>
              <tr>
                <SortTh field="key" label={t('th_key_code', lang)} className="min-w-[13rem]" />
                <SortTh field="status" label={t('th_status', lang)} className="min-w-[10rem]" />
                <SortTh field="today" label={t('th_today', lang)} className="min-w-[5rem]" />
                <SortTh field="usage" label={t('th_total', lang)} className="min-w-[5rem]" />
                <SortTh field="concurrency" label={t('th_concurrency', lang)} className="min-w-[5rem]" />
                <SortTh field="failures" label={t('th_failures', lang)} className="min-w-[5rem]" />
                <th className="min-w-[9rem]">{t('lbl_pool_assign', lang)}</th>
                <th className="text-right min-w-[6rem]">{t('th_actions', lang)}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((k) => {
                const status = statusOf(k);
                const cfg = STATUS[status];
                const isFrozen = status === 'frozen';
                const allowedPools = k.allowed_pools || [];
                const assignedPool = allowedPools.length > 0 ? allowedPools[0] : 'all';

                return (
                  <tr key={k.key}>
                    <td className="max-w-0">
                      <code className="font-mono text-[0.6875rem] block w-full overflow-hidden text-ellipsis whitespace-nowrap"
                            style={{ color: 'var(--c-accent)' }} title={k.key}>
                        {k.key}
                      </code>
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5 whitespace-nowrap">
                        <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: cfg.dot }} />
                        <Badge variant={cfg.variant} size="xs">{t(cfg.text, lang)}</Badge>
                        {isFrozen && (
                          <span className="text-[0.625rem] tabular-nums" style={{ color: 'var(--c-warn)' }}>
                            {relt(k.frozen_until)}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="whitespace-nowrap tabular-nums">{fmt(k.today || 0)}</td>
                    <td className="whitespace-nowrap tabular-nums">{fmt(k.usage || 0)}</td>
                    <td className="tabular-nums">{k.active_requests || 0}</td>
                    <td>
                      {k.consecutive_failures > 0 ? (
                        <span className="tabular-nums font-medium" style={{ color: 'var(--c-critical)' }}>
                          {k.consecutive_failures}
                        </span>
                      ) : <span style={{ color: 'var(--c-ink-3)' }}>0</span>}
                    </td>
                    <td>
                      <select
                        value={assignedPool}
                        onChange={(e) => handleAssignPool(k.key, e.target.value)}
                        className="select select-xs text-[0.6875rem] w-full min-w-[8rem]"
                        aria-label={t('lbl_pool_assign', lang)}
                      >
                        <option value="all">{t('ks_pool_all', lang)}</option>
                        <option value="gemini-flash">gemini-flash</option>
                        <option value="gemini-flash-lite">flash-lite</option>
                      </select>
                    </td>
                    <td>
                      <div className="flex items-center justify-end gap-0.5">
                        <button
                          onClick={() => handleToggleKey(k.key, k.enabled)}
                          className="btn btn-ghost btn-xs btn-square"
                          style={{ color: k.enabled ? 'var(--c-critical)' : 'var(--c-good)' }}
                          aria-label={k.enabled ? t('ks_disable', lang) : t('ks_enable', lang)}
                          title={k.enabled ? t('ks_disable', lang) : t('ks_enable', lang)}
                        >
                          {k.enabled ? <ShieldAlert className="w-3.5 h-3.5" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                        </button>

                        {(k.consecutive_failures > 0 || isFrozen) && (
                          <button
                            onClick={() => handleResetKey(k.key)}
                            className="btn btn-ghost btn-xs btn-square"
                            style={{ color: 'var(--c-warn)' }}
                            aria-label={t('ks_reset', lang)}
                            title={t('ks_reset', lang)}
                          >
                            <RefreshCw className="w-3.5 h-3.5" />
                          </button>
                        )}

                        <button
                          onClick={() => handleDeleteKey(k.key)}
                          className="btn btn-ghost btn-xs btn-square"
                          style={{ color: 'var(--c-critical)' }}
                          aria-label={t('ks_remove', lang)}
                          title={t('ks_remove', lang)}
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
      <PageHeader
        title={t('ks_title', lang)}
        subtitle={t('ks_sub', lang)}
        actions={
          <Button size="sm" icon={Plus} onClick={() => setShowAddForm(v => !v)}>
            {t('btn_add', lang)}
          </Button>
        }
      />

      {showAddForm && (
        <Card padding="md" className="animate-fade-in-up">
          <h3 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink)' }}>
            {t('ks_add_key_title', lang)}
          </h3>
          <form onSubmit={handleAddKey} className="flex flex-col sm:flex-row gap-2">
            <input
              type="password"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder={t('placeholder_auth_key', lang)}
              className="input text-sm flex-1"
              autoComplete="off"
              spellCheck="false"
            />
            <Button type="submit" loading={isAdding}>{t('btn_add', lang)}</Button>
          </form>
          {addMsg.text && <div className="mt-3"><Alert type={addMsg.type} message={addMsg.text} /></div>}
        </Card>
      )}

      {/* One row of counts. Colour marks the state; the label names it. */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 animate-fade-in-up cascade-1">
        {stats.map(({ key, value, color }) => (
          <Card key={key} padding="md" className="glass-card-interactive">
            <div className="flex items-center gap-1.5 mb-2">
              <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: color }} />
              <span className="label-micro">{t(STATUS[key].text, lang)}</span>
            </div>
            <div className="text-[1.5rem] leading-none font-semibold tabular-nums" style={{ color }}>
              {value}
            </div>
          </Card>
        ))}
        <Card padding="md" className="glass-card-interactive">
          <div className="flex items-center gap-1.5 mb-2">
            <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: 'var(--c-accent)' }} />
            <span className="label-micro">{t('ks_card_total', lang)}</span>
          </div>
          <div className="text-[1.5rem] leading-none font-semibold tabular-nums" style={{ color: 'var(--c-ink)' }}>
            {keys.length}
          </div>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2 items-center animate-fade-in-up cascade-2">
        <div className="relative flex items-center flex-1 min-w-[12rem] max-w-xs">
          <Search className="absolute left-3 w-3.5 h-3.5" style={{ color: 'var(--c-ink-3)' }} />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('placeholder_search_keys', lang)}
            className={`input input-sm w-full pl-9 text-xs ${isHackerSearch ? 'matrix-text' : ''}`}
            style={isGeminiSearch ? { borderColor: 'var(--c-warn)', color: 'var(--c-warn)' } : undefined}
          />
        </div>

        <select value={filterTier} onChange={(e) => setFilterTier(e.target.value)}
                className="select select-sm text-xs" aria-label={t('lbl_tier', lang)}>
          <option value="all">{t('lbl_tier', lang)}: {t('opt_all', lang)}</option>
          <option value="admin">{t('opt_admin', lang)}</option>
          <option value="premium">{t('opt_premium', lang)}</option>
          <option value="free">{t('opt_free', lang)}</option>
        </select>

        <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}
                className="select select-sm text-xs" aria-label={t('lbl_status', lang)}>
          <option value="all">{t('lbl_status', lang)}: {t('opt_all', lang)}</option>
          <option value="healthy">{t('opt_healthy', lang)}</option>
          <option value="frozen">{t('opt_cooldown', lang)}</option>
          <option value="disabled">{t('opt_disabled', lang)}</option>
        </select>
      </div>

      <div className="space-y-4">
        {sortedKeys.length === 0 ? (
          <Card padding="xl" className="text-center">
            <p className="text-xs" style={{ color: 'var(--c-ink-3)' }}>{t('no_keys_found', lang)}</p>
          </Card>
        ) : groups.map(g => <Fragment key={g.tier}>{renderKeysTable(g.rows, g.label)}</Fragment>)}
      </div>
    </div>
  );
}