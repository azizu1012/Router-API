import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Trash2, Edit3, RefreshCw, Power } from 'lucide-react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Alert from '../components/Alert';

const blankTarget = () => ({ model: '', endpoint: '', weight: 1, priority: 1 });
const blankForm = () => ({ alias_name: '', label: '', strategy: 'waterfall', enabled: true, targets: [blankTarget()] });

function decodeTargets(alias) {
  try {
    const value = typeof alias.targets === 'string' ? JSON.parse(alias.targets || '[]') : alias.targets;
    if (Array.isArray(value) && value.length) return value.map((target, index) => ({
      model: target.model || target.target || '',
      endpoint: target.endpoint || target.target_endpoint || '',
      weight: Number(target.weight) || 1,
      priority: Number(target.priority) || index + 1
    }));
  } catch { /* fall back to legacy fields */ }
  return [{ model: alias.target_model || '', endpoint: alias.target_endpoint || '', weight: 1, priority: 1 }];
}

function parseTargets(alias) {
  try {
    const value = typeof alias.targets === 'string' ? JSON.parse(alias.targets || '[]') : alias.targets;
    return Array.isArray(value) ? value : [];
  } catch { return []; }
}

export default function ComboModelsTab() {
  const { token, lang } = useApp();
  const [aliases, setAliases] = useState([]);
  const [endpoints, setEndpoints] = useState([]);
  const [internalTargetModels, setInternalTargetModels] = useState([
    'gemini-flash',
    'gemini-flash-lite',
    'gemini-4-hydrogen',
    'gemini-flash-25',
    'gemma-4-26b',
  ]);
  const [form, setForm] = useState(blankForm());
  const [editing, setEditing] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [togglingId, setTogglingId] = useState(null);
  const [message, setMessage] = useState({ text: '', type: '' });

  // Stable identity: `load` lists it as a dependency, so a fresh closure per
  // render would rebuild the loader on every render and refetch in a loop.
  const notify = useCallback((text, type = 'info') => {
    setMessage({ text, type });
    if (type === 'success') setTimeout(() => setMessage({ text: '', type: '' }), 3500);
  }, []);

  const load = useCallback(async (isCurrent = () => true) => {
    setLoading(true);
    try {
      const [aliasData, endpointData, poolData] = await Promise.all([
        api('/api/me/aliases', { method: 'GET' }, token),
        api('/api/me/endpoints', { method: 'GET' }, token),
        api('/api/model-pools', { method: 'GET' }, token).catch(() => null),
      ]);
      if (!isCurrent()) return;
      const aliasList = Array.isArray(aliasData) ? aliasData : (aliasData.aliases || []);
      setAliases(aliasList.filter(alias => parseTargets(alias).length > 0));
      const endpointList = Array.isArray(endpointData) ? endpointData : (endpointData.endpoints || endpointData.ep || []);
      setEndpoints(endpointList);
      if (Array.isArray(poolData?.target_models) && poolData.target_models.length > 0) {
        setInternalTargetModels(poolData.target_models);
      }
    } catch (err) {
      if (!isCurrent()) return;
      notify(`${t('cm_load_err', lang)}: ${err.message}`, 'error');
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [token, lang, notify]);

  useEffect(() => {
    let alive = true;
    (async () => { await load(() => alive); })();
    return () => { alive = false; };
  }, [load]);

  /* Decoding each alias's targets on every render re-parses JSON for every
     card on the page; the list only changes when `aliases` does. */
  const decoded = useMemo(
    () => aliases.map(alias => ({ alias, targets: decodeTargets(alias) })),
    [aliases]
  );

  const updateTarget = (index, field, value) => setForm(prev => ({
    ...prev,
    targets: prev.targets.map((target, i) => i === index ? { ...target, [field]: value } : target)
  }));

  const getModelsForEndpoint = endpointName => {
    const endpoint = endpoints.find(item => item.name === endpointName);
    if (endpoint) {
      const models = endpoint.enabled_models?.length ? endpoint.enabled_models : endpoint.models;
      if (models?.length) return models;
    }
    return internalTargetModels;
  };

  const normalizedTargets = form.targets.filter(target => target.model.trim()).map((target, index) => ({
    model: target.model.trim(),
    endpoint: target.endpoint || null,
    priority: Number(target.priority) || index + 1,
    weight: Number(target.weight) || 1
  }));

  const renderModelOptions = endpointName => getModelsForEndpoint(endpointName).map(model => (
    <option key={model} value={model}>{model}</option>
  ));

  const reset = () => { setForm(blankForm()); setEditing(null); setShowForm(false); };

  const submit = async event => {
    event.preventDefault();
    if (!form.alias_name.trim() || !normalizedTargets.length) {
      notify(t('cm_need_target', lang), 'error');
      return;
    }
    const primary = normalizedTargets[0];
    const payload = {
      alias_name: form.alias_name.trim(),
      target_model: primary.model,
      target_endpoint: primary.endpoint,
      label: form.label.trim(),
      enabled: form.enabled,
      targets: normalizedTargets,
      strategy: form.strategy
    };
    setSaving(true);
    try {
      const url = editing ? `/api/me/aliases/${editing.alias_id}` : '/api/me/aliases';
      await api(url, { method: editing ? 'PATCH' : 'POST', body: JSON.stringify(payload) }, token);
      notify(editing ? t('cm_saved', lang) : t('cm_created', lang), 'success');
      reset();
      load();
    } catch (err) {
      notify(`${t('cm_save_err', lang)}: ${err.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const edit = alias => {
    setEditing(alias);
    setForm({
      alias_name: alias.alias_name || '',
      label: alias.label || '',
      strategy: alias.strategy || 'waterfall',
      enabled: Boolean(alias.enabled),
      targets: decodeTargets(alias),
    });
    setShowForm(true);
  };

  const remove = async alias => {
    if (!window.confirm(t('cm_delete_confirm', lang))) return;
    try {
      await api(`/api/me/aliases/${alias.alias_id}`, { method: 'DELETE' }, token);
      notify(t('cm_deleted', lang), 'success');
      load();
    } catch (err) { notify(`${t('cm_delete_err', lang)}: ${err.message}`, 'error'); }
  };

  const toggle = async alias => {
    setTogglingId(alias.alias_id);
    try {
      await api(`/api/me/aliases/${alias.alias_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: !alias.enabled })
      }, token);
      load();
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setTogglingId(null);
    }
  };

  if (loading) return <Loading message={t('cm_loading', lang)} />;

  return (
    <div className="space-y-5">
      <PageHeader
        title={t('cm_title', lang)}
        subtitle={t('cm_sub', lang)}
        actions={
          <>
            <Button variant="ghost" icon={RefreshCw} onClick={load}>{t('cm_refresh', lang)}</Button>
            <Button icon={Plus} onClick={() => { setForm(blankForm()); setEditing(null); setShowForm(true); }}>
              {t('cm_create', lang)}
            </Button>
          </>
        }
      />

      {message.text && <Alert type={message.type} message={message.text} onClose={() => setMessage({ text: '', type: '' })} />}

      {showForm && (
        <Card padding="lg" className="animate-fade-in-up">
          <form onSubmit={submit} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <label className="block">
                <span className="label-micro block mb-1.5">{t('cm_alias', lang)}</span>
                <input className="input input-sm w-full text-xs" placeholder={t('cm_alias', lang)}
                       value={form.alias_name} onChange={e => setForm({ ...form, alias_name: e.target.value })} required />
              </label>
              <label className="block">
                <span className="label-micro block mb-1.5">{t('cm_label', lang)}</span>
                <input className="input input-sm w-full text-xs" placeholder={t('cm_label', lang)}
                       value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} />
              </label>
              <label className="block">
                <span className="label-micro block mb-1.5">{t('th_status', lang)}</span>
                <select className="select select-sm w-full text-xs" value={form.strategy}
                        onChange={e => setForm({ ...form, strategy: e.target.value })}>
                  <option value="waterfall">{t('cm_strategy_waterfall', lang)}</option>
                  <option value="load_balance">{t('cm_strategy_lb', lang)}</option>
                </select>
              </label>
            </div>

            <div className="space-y-2">
              {form.targets.map((target, index) => (
                <div key={index} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center">
                  <select className="select select-sm text-xs md:col-span-5 font-mono" value={target.model}
                          onChange={e => updateTarget(index, 'model', e.target.value)} required={index === 0}>
                    <option value="">{t('cm_pick_model', lang)}</option>
                    {renderModelOptions(target.endpoint)}
                  </select>
                  <select className="select select-sm text-xs md:col-span-4" value={target.endpoint}
                          onChange={e => updateTarget(index, 'endpoint', e.target.value)}>
                    <option value="">{t('cm_pool', lang)}</option>
                    {endpoints.map(endpoint => (
                      <option key={endpoint.name} value={endpoint.name}>{endpoint.name}</option>
                    ))}
                  </select>
                  <input className="input input-sm text-xs md:col-span-2 tabular-nums" type="number" min="1"
                         placeholder={t('cm_weight', lang)} value={target.weight}
                         onChange={e => updateTarget(index, 'weight', e.target.value)} />
                  <button type="button" className="btn btn-ghost btn-sm btn-square justify-self-start md:justify-self-end"
                          style={{ color: 'var(--c-critical)' }}
                          disabled={form.targets.length === 1}
                          aria-label={t('cm_delete', lang)}
                          onClick={() => setForm(prev => ({ ...prev, targets: prev.targets.filter((_, i) => i !== index) }))}>
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="outline" icon={Plus}
                      onClick={() => setForm(prev => ({ ...prev, targets: [...prev.targets, blankTarget()] }))}>
                {t('cm_add_target', lang)}
              </Button>
              <span className="flex-1" />
              <Button type="button" variant="ghost" onClick={reset}>{t('btn_cancel', lang)}</Button>
              <Button type="submit" loading={saving}>
                {editing ? t('cm_save', lang) : t('cm_create', lang)}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {!aliases.length ? (
        <Card padding="xl" className="text-center">
          <p className="text-xs" style={{ color: 'var(--c-ink-3)' }}>{t('cm_empty', lang)}</p>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {decoded.map(({ alias, targets }) => (
            <Card key={alias.alias_id} padding="md" className="flex flex-col">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold truncate" style={{ color: 'var(--c-ink)' }}>
                    {alias.alias_name}
                  </h2>
                  <p className="text-[0.6875rem] mt-0.5 truncate" style={{ color: 'var(--c-ink-3)' }}>
                    {alias.label || t('cm_title', lang)} · {alias.strategy || 'waterfall'}
                  </p>
                </div>
                <Badge variant={alias.enabled ? 'success' : 'ghost'} dot={alias.enabled}>
                  {alias.enabled ? t('cm_on', lang) : t('cm_off', lang)}
                </Badge>
              </div>

              <div className="flex flex-wrap gap-1.5 my-3">
                {targets.map((target, index) => (
                  <span key={`${alias.alias_id}-${index}`}
                        className="font-mono text-[0.6875rem] px-1.5 py-0.5 rounded"
                        style={{ background: 'var(--c-surface-2)', color: 'var(--c-ink-2)' }}>
                    {target.model}{target.weight > 1 ? ` ×${target.weight}` : ''}
                  </span>
                ))}
              </div>

              <div className="flex items-center gap-0.5 mt-auto">
                <Button variant="ghost" size="xs" icon={Edit3} onClick={() => edit(alias)}>
                  {t('cm_edit', lang)}
                </Button>
                <Button variant="ghost" size="xs" icon={Power}
                        loading={togglingId === alias.alias_id}
                        onClick={() => toggle(alias)}>
                  {alias.enabled ? t('cm_off', lang) : t('cm_on', lang)}
                </Button>
                <Button variant="ghost" size="xs" icon={Trash2} onClick={() => remove(alias)}
                        style={{ color: 'var(--c-critical)' }}>
                  {t('cm_delete', lang)}
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}