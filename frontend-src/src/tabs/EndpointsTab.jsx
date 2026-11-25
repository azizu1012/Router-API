import { useState } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import { Plus, Trash2, Network, RefreshCw, Power, Info, Terminal as TerminalIcon, Key } from 'lucide-react';
import LogHistoryModal from '../components/LogHistoryModal';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Alert from '../components/Alert';

/* Endpoint cards. The one status signal here is enabled/disabled, and it keeps
   a word beside its dot; everything else is ink on surface. */
export default function EndpointsTab() {
  const { tabData, token, lang, refreshTab } = useApp();
  const endpoints = tabData.ep || [];
  const accounts = tabData.ac || [];

  const [newName, setNewName] = useState('');
  const [newUrl, setNewUrl] = useState('');
  const [newKey, setNewKey] = useState('');
  const [newFormat, setNewFormat] = useState('openai');
  const [showAddForm, setShowAddForm] = useState(false);
  const [addMsg, setAddMsg] = useState({ text: '', type: '' });
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [epLoadingStates, setEpLoadingStates] = useState({});
  const [epMsgStates, setEpMsgStates] = useState({});
  const [selectedEndpoint, setSelectedEndpoint] = useState(null);

  if (!tabData.ep) return <Loading message={t('loading', lang)} />;

  const handleAddEndpoint = async (e) => {
    e.preventDefault();
    if (!newName.trim() || !newUrl.trim()) return;
    setIsSubmitting(true);
    setAddMsg({ text: t('ep_adding', lang), type: 'info' });
    try {
      await api('/dashboard/admin/endpoints/add', {
        method: 'POST',
        body: JSON.stringify({
          name: newName.trim(),
          base_url: newUrl.trim(),
          auth_key: newKey.trim() || undefined,
          api_format: newFormat,
        })
      }, token);
      setNewName(''); setNewUrl(''); setNewKey(''); setNewFormat('openai');
      setAddMsg({ text: t('msg_ep_added', lang), type: 'success' });
      refreshTab();
      setShowAddForm(false);
      setTimeout(() => setAddMsg({ text: '', type: '' }), 4000);
    } catch (err) {
      setAddMsg({ text: `${t('msg_ep_error', lang)}${err.message}`, type: 'error' });
    } finally {
      setIsSubmitting(false);
    }
  };

  const setEpMsg = (epName, text, type) => {
    setEpMsgStates(prev => ({ ...prev, [epName]: { text, type } }));
    if (type === 'success') {
      setTimeout(() => setEpMsgStates(prev => ({ ...prev, [epName]: { text: '', type: '' } })), 3000);
    }
  };

  const handleToggleEp = async (epName, isEnabled) => {
    const action = isEnabled ? 'disable' : 'enable';
    try {
      await api('/dashboard/admin/endpoints/toggle', {
        method: 'POST',
        body: JSON.stringify({ name: epName, action })
      }, token);
      refreshTab();
    } catch (err) { setEpMsg(epName, `${t('ep_toggle_err', lang)}: ${err.message}`, 'error'); }
  };

  const handleRefreshModels = async (epName) => {
    setEpLoadingStates(prev => ({ ...prev, [epName]: true }));
    setEpMsg(epName, t('ep_fetching', lang), 'info');
    try {
      const data = await api('/dashboard/admin/endpoints/refresh', {
        method: 'POST',
        body: JSON.stringify({ name: epName })
      }, token);
      setEpMsg(epName, t('ep_fetch_ok', lang).replace('{n}', data.count), 'success');
      refreshTab();
    } catch (err) {
      setEpMsg(epName, `${t('ep_fetch_err', lang)}: ${err.message}`, 'error');
    } finally {
      setEpLoadingStates(prev => ({ ...prev, [epName]: false }));
    }
  };

  const handleDeleteEp = async (epName) => {
    if (!window.confirm(t('ep_delete_confirm', lang).replace('{name}', epName))) return;
    try {
      await api('/dashboard/admin/endpoints/delete', {
        method: 'POST',
        body: JSON.stringify({ name: epName })
      }, token);
      refreshTab();
    } catch (err) { setEpMsg(epName, `${t('ep_delete_err', lang)}: ${err.message}`, 'error'); }
  };

  const handleAccountAssign = async (epName, accountName) => {
    try {
      await api('/dashboard/admin/endpoints/assign', {
        method: 'POST',
        body: JSON.stringify({ name: epName, account_id: accountName })
      }, token);
      setEpMsg(epName, `${t('ep_assign_ok', lang)} ${accountName || t('ep_assign_none', lang)}`, 'success');
      refreshTab();
    } catch (err) {
      setEpMsg(epName, `${t('ep_assign_err', lang)}: ${err.message}`, 'error');
    }
  };

  const handleModelToggle = async (epName, modelId, isChecked) => {
    try {
      await api('/dashboard/admin/endpoints/toggle-model', {
        method: 'POST',
        body: JSON.stringify({ name: epName, model_id: modelId, enabled: isChecked })
      }, token);
      const label = isChecked ? t('ep_model_on', lang) : t('ep_model_off', lang);
      setEpMsg(epName, `${label} ${modelId}`, 'success');
      refreshTab();
    } catch (err) {
      setEpMsg(epName, `${t('ep_model_err', lang)}: ${err.message}`, 'error');
    }
  };

  /* Grouped by the last 8 chars of a shared auth key: endpoints minted from the
     same upstream credential fail and recover together, so they are shown
     together. Order of `endpoints` is preserved inside each group. */
  const groups = new Map();
  endpoints.forEach(ep => {
    const k = ep.auth_key ? ep.auth_key.slice(-8) : 'unassigned';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(ep);
  });

  return (
    <div className="space-y-5">
      <PageHeader
        title={t('ep_title', lang)}
        subtitle={t('ep_sub', lang)}
        actions={<Button icon={Plus} onClick={() => setShowAddForm(v => !v)}>{t('btn_add', lang)}</Button>}
      />

      {showAddForm && (
        <Card padding="lg" className="animate-fade-in-up">
          <h3 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink)' }}>
            {t('ep_add_title', lang)}
          </h3>
          <form onSubmit={handleAddEndpoint} className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="block">
              <span className="label-micro block mb-1.5">{t('ep_new_name', lang)}</span>
              <input type="text" required value={newName} onChange={(e) => setNewName(e.target.value)}
                     placeholder="OpenRouter_Main" className="input input-sm text-xs w-full" autoComplete="off" />
            </label>
            <label className="block">
              <span className="label-micro block mb-1.5">{t('ep_new_url', lang)}</span>
              <input type="url" required value={newUrl} onChange={(e) => setNewUrl(e.target.value)}
                     placeholder="https://api.openrouter.ai/v1" className="input input-sm text-xs w-full" />
            </label>
            <label className="block">
              <span className="label-micro block mb-1.5">{t('ep_new_key', lang)}</span>
              <input type="password" value={newKey} onChange={(e) => setNewKey(e.target.value)}
                     placeholder="sk-or-..." className="input input-sm text-xs w-full" autoComplete="off" spellCheck="false" />
            </label>
            <label className="block">
              <span className="label-micro block mb-1.5">{t('ep_new_format', lang)}</span>
              <select value={newFormat} onChange={(e) => setNewFormat(e.target.value)}
                      className="select select-sm text-xs w-full">
                <option value="openai">OpenAI — /chat/completions</option>
                <option value="anthropic">Anthropic — /v1/messages</option>
              </select>
              <span className="block text-[0.6875rem] mt-1 leading-snug" style={{ color: 'var(--c-ink-3)' }}>
                {t('ep_format_hint', lang)}
              </span>
            </label>
            <div className="sm:col-span-2 flex items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setShowAddForm(false)}>{t('btn_cancel', lang)}</Button>
              <Button type="submit" loading={isSubmitting}>{t('btn_add', lang)}</Button>
            </div>
          </form>
          {addMsg.text && <div className="mt-3"><Alert type={addMsg.type} message={addMsg.text} /></div>}
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 animate-fade-in-up cascade-1">
        <span className="flex items-center gap-1.5 label-micro">
          <Network className="w-3.5 h-3.5" />
          {t('ep_list_count', lang)}
        </span>
        <span className="text-[0.6875rem] tabular-nums px-1.5 py-0.5 rounded"
              style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink-3)' }}>
          {endpoints.length}
        </span>
      </div>

      <div className="space-y-5 animate-fade-in-up cascade-2">
        {endpoints.length === 0 ? (
          <Card padding="xl" className="text-center">
            <Network className="w-9 h-9 mx-auto mb-2" style={{ color: 'var(--c-ink-3)', opacity: 0.4 }} />
            <p className="text-xs" style={{ color: 'var(--c-ink-3)' }}>{t('no_endpoints', lang)}</p>
          </Card>
        ) : (
          [...groups.entries()].map(([keyHash, group]) => (
            <div key={keyHash} className="space-y-3">
              {keyHash !== 'unassigned' && (
                <div className="flex items-center gap-2 text-[0.6875rem] font-medium"
                     style={{ color: 'var(--c-ink-3)' }}>
                  <Key className="w-3.5 h-3.5" />
                  <span className="font-mono">{t('ep_shared_key', lang)}: …{keyHash}</span>
                  <span className="tabular-nums">{group.length}</span>
                </div>
              )}

              {group.map((ep) => {
                const isEnabled = ep.enabled !== false;
                const models = ep.models || [];
                const enabledModels = ep.enabled_models || [];
                const accountName = ep.account_name || '';
                const assignedAccountId = ep.account_id || '';
                const isLoading = !!epLoadingStates[ep.name];
                const msg = epMsgStates[ep.name] || { text: '', type: '' };

                return (
                  <Card key={ep.name} padding="lg" className="space-y-4">
                    <div className="flex items-start justify-between flex-wrap gap-3">
                      <div className="flex items-center gap-2 min-w-0 flex-wrap">
                        <span className="w-1.5 h-1.5 rounded-full shrink-0"
                              style={{ background: isEnabled ? 'var(--c-good)' : 'var(--c-ink-3)' }} />
                        <strong className="text-sm font-semibold truncate" style={{ color: 'var(--c-ink)' }}>
                          {ep.name}
                        </strong>
                        <Badge variant={isEnabled ? 'success' : 'ghost'}>
                          {isEnabled ? t('ep_enable', lang) : t('ep_disable', lang)}
                        </Badge>
                        <code className="text-[0.6875rem] font-mono truncate max-w-[16rem]"
                              style={{ color: 'var(--c-ink-3)' }} title={ep.base_url}>
                          {ep.base_url}
                        </code>
                        <select
                          value={ep.api_format || 'openai'}
                          onChange={async (e) => {
                            const fmt = e.target.value;
                            try {
                              await api('/dashboard/admin/endpoints/format', {
                                method: 'POST',
                                body: JSON.stringify({ name: ep.name, api_format: fmt })
                              }, token);
                              setEpMsg(ep.name, `${t('ep_format_changed', lang)} ${fmt}`, 'success');
                              refreshTab();
                            } catch (err) {
                              setEpMsg(ep.name, `${t('ep_format_err', lang)}: ${err.message}`, 'error');
                            }
                          }}
                          className="select select-xs text-[0.6875rem] shrink-0"
                          aria-label={t('ep_new_format', lang)}
                          title={t('ep_new_format', lang)}
                        >
                          <option value="openai">OpenAI</option>
                          <option value="anthropic">Anthropic</option>
                        </select>
                        {accountName && (
                          <Badge variant="info" size="sm">{accountName}</Badge>
                        )}
                      </div>

                      <div className="flex items-center gap-0.5">
                        <Button variant="ghost" size="xs" icon={TerminalIcon}
                                onClick={() => setSelectedEndpoint(ep.name)}>
                          {t('ep_logs', lang)}
                        </Button>
                        <Button variant="ghost" size="xs" icon={RefreshCw} loading={isLoading}
                                onClick={() => handleRefreshModels(ep.name)}>
                          {t('ep_fetch', lang)}
                        </Button>
                        <Button variant="ghost" size="xs" icon={Power}
                                style={{ color: isEnabled ? 'var(--c-critical)' : 'var(--c-good)' }}
                                onClick={() => handleToggleEp(ep.name, isEnabled)}>
                          {isEnabled ? t('ep_disable', lang) : t('ep_enable', lang)}
                        </Button>
                        <button onClick={() => handleDeleteEp(ep.name)}
                                className="btn btn-ghost btn-xs btn-square"
                                style={{ color: 'var(--c-critical)' }}
                                title={t('cm_delete', lang)} aria-label={t('cm_delete', lang)}>
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 pt-3"
                         style={{ borderTop: '1px solid var(--c-line)' }}>
                      <label className="block lg:col-span-1">
                        <span className="label-micro block mb-1.5">{t('lbl_assigned_account', lang)}</span>
                        <select
                          value={accountName || assignedAccountId}
                          onChange={(e) => handleAccountAssign(ep.name, e.target.value)}
                          className="select select-sm text-xs w-full"
                        >
                          <option value="">{t('ep_unassigned', lang)}</option>
                          {accounts.filter(a => a.enabled).map((a) => (
                            <option key={a.name} value={a.name}>{a.name} ({a.tier})</option>
                          ))}
                        </select>
                      </label>

                      <div className="lg:col-span-2">
                        <div className="flex items-center gap-1.5 mb-1.5">
                          <span className="label-micro">{t('ep_models_count', lang)}</span>
                          <span className="text-[0.6875rem] tabular-nums"
                                style={{ color: 'var(--c-ink-3)' }}>{models.length}</span>
                        </div>
                        {models.length > 0 ? (
                          <details className="rounded-lg group" open={models.length <= 8}>
                            <summary className="cursor-pointer select-none text-[0.6875rem] font-medium px-2.5 py-2 rounded-lg list-none flex items-center justify-between"
                                     style={{ background: 'var(--c-surface-2)', color: 'var(--c-ink-2)' }}>
                              {t('ep_models_show', lang)}
                              <span className="transition-transform group-open:rotate-180" aria-hidden="true">▾</span>
                            </summary>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 mt-2 max-h-60 overflow-y-auto pr-1">
                              {models.map((mid) => {
                                const isModelEnabled = enabledModels.includes(mid);
                                return (
                                  <div key={mid}
                                       className="flex items-center justify-between gap-2 p-2 rounded-lg text-xs"
                                       style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
                                    <span className="font-mono truncate font-medium" title={mid}
                                          style={{ color: isModelEnabled ? 'var(--c-ink)' : 'var(--c-ink-3)' }}>
                                      {mid}
                                    </span>
                                    <input type="checkbox" checked={isModelEnabled}
                                           onChange={(e) => handleModelToggle(ep.name, mid, e.target.checked)}
                                           className="toggle toggle-xs toggle-success shrink-0"
                                           aria-label={mid} />
                                  </div>
                                );
                              })}
                            </div>
                          </details>
                        ) : (
                          <div className="flex items-center gap-2 text-[0.6875rem] px-2.5 py-2 rounded-lg"
                               style={{ background: 'var(--c-surface-2)', color: 'var(--c-ink-3)' }}>
                            <Info className="w-3.5 h-3.5 shrink-0" />
                            {t('ep_models_empty', lang)}
                          </div>
                        )}
                      </div>
                    </div>

                    {msg.text && <Alert type={msg.type} message={msg.text} />}
                  </Card>
                );
              })}
            </div>
          ))
        )}
      </div>

      {selectedEndpoint && (
        <LogHistoryModal
          endpoint={selectedEndpoint}
          token={token}
          onClose={() => setSelectedEndpoint(null)}
        />
      )}
    </div>
  );
}