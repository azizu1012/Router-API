import { useState, useEffect, useCallback, useMemo } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Alert from '../components/Alert';
import Modal from '../components/Modal';
import { Layers, Trash2, Edit3, Plus, RefreshCw, Zap } from 'lucide-react';

/* Field metadata is keyed into i18n rather than carrying Vietnamese strings,
   so the whole form follows the language switch. */
const FIELD_META = {
  alias: { labelKey: 'md_field_alias', descKey: 'md_field_alias_desc', placeholder: 'gemini-flash-35', editDisabled: true },
  display: { labelKey: 'md_field_display', descKey: 'md_field_display_desc', placeholder: 'Gemini Flash 3.5' },
  model_id: { labelKey: 'md_field_model_id', descKey: 'md_field_model_id_desc', placeholder: 'gemini-3.5-flash' },
  rpm: { labelKey: 'th_rpm', descKey: 'md_field_rpm_hint', placeholder: '10', type: 'number' },
  tpm: { labelKey: 'th_tpm', descKey: 'md_field_tpm_hint', placeholder: '1000000', type: 'number' },
  rpd: { labelKey: 'th_rpd', descKey: 'md_field_rpd_hint', placeholder: '1000', type: 'number' },
  priority: { labelKey: 'md_field_priority', descKey: 'md_field_priority_desc', placeholder: '1', type: 'number' },
  context_length: { labelKey: 'md_field_context', descKey: 'md_field_context_desc', placeholder: '220000', type: 'number' },
  pool_name: { labelKey: 'md_field_pool', descKey: 'md_field_pool_desc', type: 'select' },
};

/* A disabled field says why it is disabled in the hint line under it, not only
   in a tooltip that a keyboard user may never open. */
function FormField({ field, value, onChange, disabled, lang, editing, options }) {
  const meta = FIELD_META[field];
  if (!meta) return null;
  const locked = Boolean(editing && meta.editDisabled);
  const off = disabled || locked;
  const label = t(meta.labelKey, lang);
  const hint = t(meta.descKey, lang);

  if (meta.type === 'select') {
    return (
      <label className="block">
        <span className="label-micro block mb-1.5">{label}</span>
        <select
          value={value ?? ''}
          onChange={e => onChange(field, e.target.value)}
          disabled={off}
          className="select select-sm text-xs w-full"
        >
          <option value="">{t('md_no_pool', lang)}</option>
          {(options || []).map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        <span className="block text-[0.6875rem] leading-snug mt-1.5" style={{ color: 'var(--c-ink-3)' }}>
          {hint}
        </span>
      </label>
    );
  }

  return (
    <label className="block">
      <span className="label-micro block mb-1.5">{label}</span>
      <input
        value={value ?? ''}
        onChange={e => onChange(field, meta.type === 'number' ? (parseInt(e.target.value, 10) || 0) : e.target.value)}
        placeholder={meta.placeholder}
        disabled={off}
        type={meta.type === 'number' ? 'number' : 'text'}
        inputMode={meta.type === 'number' ? 'numeric' : undefined}
        className={`input input-sm text-xs w-full ${meta.type === 'number' ? 'font-mono tabular-nums' : ''} ${locked ? 'opacity-60' : ''}`}
      />
      <span className="block text-[0.6875rem] leading-snug mt-1.5" style={{ color: 'var(--c-ink-3)' }}>
        {locked ? `${hint} · ${t('md_field_alias', lang)}` : hint}
      </span>
    </label>
  );
}

function GroupLabel({ children }) {
  return (
    <div className="label-micro pt-1 pb-1.5" style={{ borderBottom: '1px solid var(--c-line)' }}>
      {children}
    </div>
  );
}

const BLANK = {
  alias: '', display: '', model_id: '', rpm: 10, tpm: 1000000, rpd: 1000,
  hidden: false, priority: 1, context_length: 220000, pool_name: '', account_id: '',
};

/* The row's values seed the form once, at mount. The parent remounts this
   component per model (see `key` at the call site), so there is nothing to
   re-sync and no effect that could fire against a half-written form. */
const seedFrom = (model) => ({
  alias: model.alias || '',
  display: model.display || '',
  model_id: model.model_id || '',
  rpm: model.rpm || 10,
  tpm: model.tpm || 1000000,
  rpd: model.rpd || 1000,
  hidden: !!model.hidden,
  priority: model.priority || 1,
  context_length: model.context_length || 220000,
  pool_name: model.pool_name || '',
  rpd_enabled: model.db_rpd != null,
  account_id: model.account_id || '',
});

function EditModelModal({ model, onClose, onSaved, token, lang, poolOptions }) {
  const [form, setForm] = useState(() => seedFrom(model));
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState({ text: '', type: '' });

  const updateField = (field, val) => setForm(prev => ({ ...prev, [field]: val }));

  const handleSave = async (e) => {
    e.preventDefault();
    if (!form.alias) { setMsg({ text: t('md_alias_required', lang), type: 'error' }); return; }
    setSaving(true);
    try {
      await api('/dashboard/admin/models/save', {
        method: 'POST',
        body: JSON.stringify(form),
      }, token);
      onSaved();
      onClose();
    } catch (err) {
      setMsg({ text: `${t('md_save_err', lang)}${err.message}`, type: 'error' });
      setSaving(false);
    }
  };

  return (
    <Modal
      isOpen
      onClose={saving ? () => {} : onClose}
      title={`${t('md_edit_title', lang)}${model.alias}`}
      size="lg"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
            {t('btn_cancel', lang)}
          </Button>
          <Button type="submit" form="model-edit-form" icon={null} loading={saving}>
            {t('md_save_changes', lang)}
          </Button>
        </>
      }
    >
      <form id="model-edit-form" onSubmit={handleSave} className="space-y-3">
        <GroupLabel>{t('md_identity', lang)}</GroupLabel>
        <FormField field="alias" value={form.alias} onChange={updateField} lang={lang} editing disabled={saving} />
        <FormField field="display" value={form.display} onChange={updateField} lang={lang} editing disabled={saving} />
        <FormField field="model_id" value={form.model_id} onChange={updateField} lang={lang} editing disabled={saving} />
        {model.source === 'custom_endpoint' && model.account_name && (
          <div>
            <span className="label-micro block mb-1.5">{t('md_account', lang)}</span>
            <div className="text-xs font-mono px-3 py-2 rounded-lg"
                 style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)', color: 'var(--c-ink-2)' }}>
              {model.account_name}
            </div>
          </div>
        )}

        <GroupLabel>{t('md_rate_limits', lang)}</GroupLabel>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <FormField field="rpm" value={form.rpm} onChange={updateField} lang={lang} editing disabled={saving} />
          <FormField field="tpm" value={form.tpm} onChange={updateField} lang={lang} editing disabled={saving} />
          <FormField field="rpd" value={form.rpd} onChange={updateField} lang={lang} editing disabled={saving} />
        </div>

        <GroupLabel>{t('md_advanced', lang)}</GroupLabel>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <FormField field="priority" value={form.priority} onChange={updateField} lang={lang} editing disabled={saving} />
          <FormField field="context_length" value={form.context_length} onChange={updateField} lang={lang} editing disabled={saving} />
        </div>
        <FormField field="pool_name" value={form.pool_name} onChange={updateField} lang={lang} editing disabled={saving} options={poolOptions} />

        <div className="flex flex-wrap items-center gap-5 pt-1">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" className="toggle toggle-xs"
                   checked={form.hidden}
                   onChange={e => updateField('hidden', e.target.checked)}
                   disabled={saving} />
            <span className="text-xs font-medium" style={{ color: 'var(--c-ink-2)' }}>{t('md_hidden', lang)}</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" className="toggle toggle-xs"
                   checked={form.rpd_enabled}
                   onChange={e => updateField('rpd_enabled', e.target.checked)}
                   disabled={saving} />
            <span className="text-xs font-medium" style={{ color: 'var(--c-ink-2)' }}>{t('md_rpd_mode', lang)}</span>
          </label>
        </div>
        <p className="text-[0.6875rem] leading-snug" style={{ color: 'var(--c-ink-3)' }}>
          {t('md_hidden_hint', lang)} · {t('md_rpd_mode_hint', lang)}
        </p>

        {msg.text && <Alert type={msg.type} message={msg.text} />}
      </form>
    </Modal>
  );
}

/* Pool assignment for a custom-endpoint model. The failure is shown in the row
   that failed, not in an alert() that names neither the model nor the pool. */
function PoolCell({ model, poolOptions, token, onRefresh, lang, onError }) {
  const [assigning, setAssigning] = useState(false);

  const handleChange = async (poolName) => {
    if (!model.endpoint) return;
    setAssigning(true);
    try {
      await api('/dashboard/admin/endpoints/pool-assign', {
        method: 'POST',
        body: JSON.stringify({ name: model.endpoint, pool_name: poolName, model_id: poolName ? model.alias : '' }),
      }, token);
      onRefresh();
    } catch (err) {
      onError(`${t('md_pool_assign_err', lang)}${err.message}`);
    } finally {
      setAssigning(false);
    }
  };

  if (model.source === 'custom_endpoint') {
    return (
      <span className="inline-flex items-center gap-1">
        <select
          value={model.pool_name || ''}
          onChange={e => handleChange(e.target.value)}
          disabled={assigning}
          className="select select-xs text-[0.6875rem] font-mono max-w-[6.5rem]"
          aria-label={t('md_field_pool', lang)}
        >
          <option value="">—</option>
          {poolOptions.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        {assigning && <span className="loading loading-spinner loading-xs" />}
      </span>
    );
  }

  return (
      <span className="text-xs font-mono whitespace-nowrap truncate inline-block max-w-full align-bottom"
            title={model.pool_name || ''} style={{ color: 'var(--c-ink-2)' }}>
        {model.pool_name || '—'}
      </span>
    );
}

function PoolMembersPanel({ pools, models, onToggle, busyAlias, lang }) {
  const healthByAlias = useMemo(() => {
    const map = {};
    for (const m of models || []) map[m.alias] = m;
    return map;
  }, [models]);

  if (!(pools || []).length) return null;

  return (
    <div className="space-y-3">
      {pools.map(p => {
        const members = p.members || [];
        const activeCount = (p.active_members || []).length;
        return (
          <Card key={p.name} padding="none" className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5"
                 style={{ background: 'var(--c-surface-2)', borderBottom: '1px solid var(--c-line)' }}>
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-sm font-semibold font-mono truncate" style={{ color: 'var(--c-ink)' }}>
                  {p.name}
                </span>
                <Badge variant={activeCount ? 'success' : 'error'} dot={false}>
                  {t('md_pool_ratio', lang).replace('{a}', activeCount).replace('{b}', members.length)}
                </Badge>
              </div>
              <span className="text-[0.6875rem] leading-snug max-w-[26rem]"
                    style={{ color: 'var(--c-ink-3)' }}>
                {t('md_pool_off_note', lang)}
              </span>
            </div>
            <div>
              {members.map(m => {
                const info = healthByAlias[m] || {};
                const on = info.enabled !== false;
                const busy = busyAlias === m;
                return (
                  <div key={m}
                       className="flex flex-wrap items-center justify-between gap-3 px-4 py-2"
                       style={{
                         borderTop: '1px solid var(--c-line)',
                         background: on ? 'transparent' : 'var(--c-surface-2)',
                       }}>
                    <div className="flex items-baseline gap-2 min-w-0">
                      <span className="font-mono text-xs font-medium truncate"
                            style={{
                              color: on ? 'var(--c-ink)' : 'var(--c-ink-3)',
                              textDecoration: on ? 'none' : 'line-through',
                            }}>
                        {m}
                      </span>
                      {info.display && (
                        <span className="text-[0.6875rem] truncate hidden sm:inline" style={{ color: 'var(--c-ink-3)' }}>
                          {info.display}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-[0.6875rem] font-mono tabular-nums" style={{ color: 'var(--c-ink-3)' }}>
                        {on ? `${info.rpm ?? '?'} RPM` : t('md_disabled', lang)}
                      </span>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          className="toggle toggle-xs"
                          checked={on}
                          disabled={busy}
                          onChange={e => onToggle(m, e.target.checked)}
                          aria-label={`${m} — ${on ? t('md_on', lang) : t('md_off', lang)}`}
                        />
                        <span className="text-[0.6875rem] font-medium w-[1.75rem]"
                              style={{ color: on ? 'var(--c-ink-2)' : 'var(--c-ink-3)' }}>
                          {on ? t('md_on', lang) : t('md_off', lang)}
                        </span>
                      </label>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

export default function ModelsTab() {
  const { tabData, token, lang } = useApp();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState({ text: '', type: '' });

  const [showAddForm, setShowAddForm] = useState(false);
  const [addForm, setAddForm] = useState(BLANK);
  const [adding, setAdding] = useState(false);
  const [addMsg, setAddMsg] = useState({ text: '', type: '' });

  const [selectedEpInfo, setSelectedEpInfo] = useState(null);
  const [editModel, setEditModel] = useState(null);
  const [editOpen, setEditOpen] = useState(false);
  const [busyAlias, setBusyAlias] = useState(null);

  const endpoints = useMemo(() => data?.endpoints || tabData.ep || [], [data?.endpoints, tabData.ep]);

  const showMsg = useCallback((text, type) => {
    setMsg({ text, type });
    if (type === 'success') setTimeout(() => setMsg({ text: '', type: '' }), 4000);
  }, []);

  /* `isCurrent` is passed only by the mount effect. The refresh button, the
     save handler and PoolCell call it bare — they want whatever comes back. */
  const fetchData = useCallback(async (isCurrent = () => true) => {
    setLoading(true);
    try {
      const res = await api('/dashboard/admin/models', {}, token);
      if (!isCurrent()) return;
      setData(res);
    } catch (err) {
      if (!isCurrent()) return;
      setMsg({ text: `${t('stg_load_err', lang)}${err.message}`, type: 'error' });
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [token, lang]);

  useEffect(() => {
    let alive = true;
    (async () => { await fetchData(() => alive); })();
    return () => { alive = false; };
  }, [fetchData]);

  /* Every enabled model on every enabled endpoint, flattened once so the
     "pick from endpoint" select does not rescan the endpoint list each key. */
  const endpointModelOptions = useMemo(() => {
    const out = [];
    for (const ep of endpoints) {
      if (ep.enabled === false) continue;
      for (const mid of (ep.enabled_models || [])) {
        out.push({
          epName: ep.name,
          modelId: mid,
          accountName: ep.account_name || '',
          accountId: ep.account_id || '',
          label: `${ep.name} → ${mid}`,
        });
      }
    }
    return out;
  }, [endpoints]);

  const pickFromEndpoint = (val) => {
    if (!val) return;
    const [epName, modelId] = val.split('|');
    const info = endpointModelOptions.find(o => o.epName === epName && o.modelId === modelId);
    setSelectedEpInfo(info || null);
    setAddForm(prev => ({
      ...prev,
      alias: modelId,
      display: `${epName} / ${modelId}`,
      model_id: modelId,
      account_id: info?.accountId || '',
    }));
  };

  const handleToggleMember = async (alias, enabled) => {
    setBusyAlias(alias);
    try {
      const res = await api('/dashboard/admin/pools/toggle-member', {
        method: 'POST',
        body: JSON.stringify({ alias, enabled }),
      }, token);
      const active = res.active_members || [];
      const rest = active.filter(m => m !== alias);
      showMsg(
        `${enabled ? t('md_on', lang) : t('md_off', lang)} ${alias}`
        + (res.pool ? ` — ${res.pool}: ${t('md_pool_ratio', lang).replace('{a}', active.length).replace('{b}', '?')}${rest.length ? ` (${rest.join(', ')})` : ''}` : ''),
        'success',
      );
      fetchData();
    } catch (err) {
      showMsg(`${t('md_pool_assign_err', lang)}${err.message}`, 'error');
    } finally {
      setBusyAlias(null);
    }
  };

  const resetAddForm = () => {
    setSelectedEpInfo(null);
    setAddForm(BLANK);
    setAddMsg({ text: '', type: '' });
  };

  const updateAddField = (field, val) => setAddForm(prev => ({ ...prev, [field]: val }));

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!addForm.alias) { setAddMsg({ text: t('md_alias_required', lang), type: 'error' }); return; }
    setAdding(true);
    setAddMsg({ text: t('md_adding', lang), type: 'info' });
    try {
      await api('/dashboard/admin/models/save', {
        method: 'POST',
        body: JSON.stringify(addForm),
      }, token);
      resetAddForm();
      setShowAddForm(false);
      showMsg(`${t('md_added', lang)}${addForm.alias}`, 'success');
      fetchData();
    } catch (err) {
      setAddMsg({ text: `${t('md_add_err', lang)}${err.message}`, type: 'error' });
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (model) => {
    if (model.source === 'custom_endpoint') {
      const actions = [];
      if (model.pool_name) actions.push(t('md_action_unassign', lang));
      actions.push(t('md_action_detach', lang));
      const body = `${t('md_delete_custom_title', lang).replace('{alias}', model.alias)}\n${t('md_delete_custom_actions', lang).replace('{actions}', actions.join(' + '))}`;
      if (!window.confirm(body)) return;
      try {
        if (model.pool_name) {
          await api('/dashboard/admin/endpoints/pool-assign', {
            method: 'POST',
            body: JSON.stringify({ name: model.endpoint, pool_name: model.pool_name, model_id: '' }),
          }, token);
        }
        await api('/dashboard/admin/endpoints/toggle-model', {
          method: 'POST',
          body: JSON.stringify({ name: model.endpoint, model_id: model.alias, enabled: false }),
        }, token);
        showMsg(`${t('md_deleted', lang)}${model.alias}`, 'success');
        fetchData();
      } catch (err) {
        showMsg(`${t('md_delete_err', lang)}${err.message}`, 'error');
      }
      return;
    }

    if (!window.confirm(t('md_delete_confirm', lang).replace('{alias}', model.alias))) return;
    try {
      await api('/dashboard/admin/models/delete', {
        method: 'POST',
        body: JSON.stringify({ alias: model.alias }),
      }, token);
      showMsg(`${t('md_deleted', lang)}${model.alias}`, 'success');
      fetchData();
    } catch (err) {
      showMsg(`${t('md_delete_err', lang)}${err.message}`, 'error');
    }
  };

  const openEdit = (model) => {
    setEditModel(model);
    setEditOpen(true);
  };

  if (loading) return <Loading message={t('md_loading', lang)} />;

  const models = data?.models || [];
  const pools = data?.pools || [];
  const poolOptions = pools.map(p => p.name);

  return (
    <div className="space-y-5">
      <PageHeader
        title={t('md_title', lang)}
        subtitle={t('md_sub', lang)}
        actions={
          <>
            <Button variant="ghost" icon={RefreshCw} onClick={fetchData}>{t('cm_refresh', lang)}</Button>
            <Button icon={Plus} onClick={() => { resetAddForm(); setShowAddForm(v => !v); }}>
              {t('md_add', lang)}
            </Button>
          </>
        }
      />

      {msg.text && <Alert type={msg.type} message={msg.text} onClose={() => setMsg({ text: '', type: '' })} />}

      {showAddForm && (
        <Card padding="lg" className="animate-fade-in-up">
          <h2 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink)' }}>
            {t('md_add_title', lang)}
          </h2>
          <form onSubmit={handleAdd} className="space-y-3">
            <label className="block">
              <span className="label-micro block mb-1.5">{t('md_pick_ep', lang)}</span>
              <select
                onChange={e => pickFromEndpoint(e.target.value)}
                disabled={adding}
                className="select select-sm text-xs w-full font-mono"
                defaultValue=""
              >
                <option value="">{t('md_pick_ep_placeholder', lang)}</option>
                {endpointModelOptions.length > 0 ? endpointModelOptions.map(opt => (
                  <option key={opt.epName + '|' + opt.modelId} value={opt.epName + '|' + opt.modelId}>
                    {opt.label}{opt.accountName ? ` (${opt.accountName})` : ''}
                  </option>
                )) : (
                  <option value="" disabled>{t('md_pick_ep_empty', lang)}</option>
                )}
              </select>
              <span className="block text-[0.6875rem] leading-snug mt-1.5" style={{ color: 'var(--c-ink-3)' }}>
                {t('md_pick_ep_hint', lang)}
              </span>
            </label>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <FormField field="alias" value={addForm.alias} onChange={updateAddField} lang={lang} disabled={adding} />
              <FormField field="display" value={addForm.display} onChange={updateAddField} lang={lang} disabled={adding} />
              <FormField field="model_id" value={addForm.model_id} onChange={updateAddField} lang={lang} disabled={adding} />
            </div>

            {selectedEpInfo && (
              <div className="rounded-lg px-3 py-2 text-[0.6875rem]"
                   style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
                <span className="label-micro">{t('md_account', lang)}</span>{' '}
                <span className="font-mono" style={{ color: 'var(--c-ink-2)' }}>
                  {selectedEpInfo.accountName || selectedEpInfo.accountId || '—'}
                </span>
              </div>
            )}

            <GroupLabel>{t('md_rate_limits', lang)}</GroupLabel>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <FormField field="rpm" value={addForm.rpm} onChange={updateAddField} lang={lang} disabled={adding} />
              <FormField field="tpm" value={addForm.tpm} onChange={updateAddField} lang={lang} disabled={adding} />
              <FormField field="rpd" value={addForm.rpd} onChange={updateAddField} lang={lang} disabled={adding} />
            </div>

            <GroupLabel>{t('md_advanced', lang)}</GroupLabel>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <FormField field="priority" value={addForm.priority} onChange={updateAddField} lang={lang} disabled={adding} />
              <FormField field="context_length" value={addForm.context_length} onChange={updateAddField} lang={lang} disabled={adding} />
            </div>
            <FormField field="pool_name" value={addForm.pool_name} onChange={updateAddField} lang={lang} disabled={adding} options={poolOptions} />

            <label className="flex items-center gap-2 cursor-pointer pt-1">
              <input type="checkbox" className="toggle toggle-xs"
                     checked={addForm.hidden}
                     onChange={e => updateAddField('hidden', e.target.checked)}
                     disabled={adding} />
              <span className="text-xs font-medium" style={{ color: 'var(--c-ink-2)' }}>{t('md_hidden', lang)}</span>
              <span className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>— {t('md_hidden_hint', lang)}</span>
            </label>

            {addMsg.text && <Alert type={addMsg.type} message={addMsg.text} />}

            <div className="flex flex-wrap items-center justify-end gap-2 pt-3"
                 style={{ borderTop: '1px solid var(--c-line)' }}>
              <Button type="button" variant="ghost" onClick={() => setShowAddForm(false)} disabled={adding}>
                {t('btn_cancel', lang)}
              </Button>
              <Button type="submit" icon={Plus} loading={adding}>
                {t('md_add', lang)}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {/* Pool members — each pool is a block, not a table row, because the
          toggle applies instantly and the pool is the unit it acts on. */}
      <div className="space-y-3 animate-fade-in-up cascade-1">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="flex items-center gap-1.5 label-micro">
            <Zap className="w-3.5 h-3.5" />
            {t('md_pool_members', lang)}
          </span>
          <span className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
            {t('md_pool_members_hint', lang)}
          </span>
        </div>
        <PoolMembersPanel
          pools={pools}
          models={models}
          onToggle={handleToggleMember}
          busyAlias={busyAlias}
          lang={lang}
        />
      </div>

      {/* Models table */}
      <div className="space-y-3 animate-fade-in-up cascade-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="flex items-center gap-1.5 label-micro">
            <Layers className="w-3.5 h-3.5" />
            {t('md_models_list', lang)}
          </span>
          <span className="text-[0.6875rem] tabular-nums px-1.5 py-0.5 rounded"
                style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink-3)' }}>
            {t('md_models_count', lang).replace('{n}', String(models.length))}
          </span>
        </div>

        <div className="glass-card rounded-xl overflow-hidden text-left">
          <div className="overflow-x-auto w-full">
            <table className="table table-pin w-full text-xs">
              {/* Twelve columns in a 1134 px pane is more than fits, and
                  three of them were names for the same model: the display
                  name, the client-facing alias, and the upstream id. Alias and
                  display now share one identity cell (display on top, alias
                  beneath in mono), which is the same shape the pool graph
                  uses. The upstream id keeps its own column - for a custom
                  endpoint it is a genuinely different name, not a restatement. */}
              <thead>
                <tr>
                  <th className="min-w-[9rem]">{t('th_alias', lang)}</th>
                  <th className="min-w-[5rem]">{t('th_source', lang)}</th>
                  <th className="min-w-[8.5rem]">{t('th_model', lang)}</th>
                  <th className="min-w-[5rem] whitespace-nowrap">{t('th_pool', lang)}</th>
                  <th className="min-w-[4rem]">{t('th_account', lang)}</th>
                  <th className="min-w-[4.5rem] text-right">{t('th_rpm', lang)}</th>
                  <th className="min-w-[6rem] text-right">{t('th_tpm', lang)}</th>
                  <th className="min-w-[5.5rem] text-right">{t('th_rpd', lang)}</th>
                  <th className="min-w-[4rem]">{t('th_db', lang)}</th>
                  <th className="min-w-[5.5rem] whitespace-nowrap">{t('th_status', lang)}</th>
                  <th className="min-w-[5.5rem] text-right">{t('th_actions', lang)}</th>
                </tr>
              </thead>
              <tbody>
                {models.length === 0 && (
                  <tr>
                    <td colSpan="11" className="text-center py-6 text-xs" style={{ color: 'var(--c-ink-3)' }}>
                      {t('no_data', lang)}
                    </td>
                  </tr>
                )}
                {models.map(m => (
                  <tr key={m.source + '|' + m.alias} style={{ opacity: m.hidden ? 0.6 : 1 }}>
                    <td className="max-w-0">
                      <span className="block truncate font-medium" title={m.display || m.alias}
                            style={{ color: 'var(--c-ink)' }}>
                        {m.display || m.alias}
                      </span>
                      <span className="block truncate font-mono text-[0.6875rem]" title={m.alias}
                            style={{ color: 'var(--c-ink-3)' }}>
                        {m.alias}
                      </span>
                    </td>
                    <td>
                      <Badge variant={m.source === 'gemini' ? 'primary' : 'ghost'}>
                        {m.source === 'gemini' ? 'Gemini' : (m.endpoint || 'custom')}
                      </Badge>
                    </td>
                    <td className="font-mono max-w-0">
                      <span className="block truncate" title={m.model_id || ''} style={{ color: 'var(--c-ink-3)' }}>
                        {m.model_id}
                      </span>
                    </td>
                    <td>
                      <PoolCell
                        model={m}
                        poolOptions={poolOptions}
                        token={token}
                        onRefresh={fetchData}
                        lang={lang}
                        onError={(text) => showMsg(text, 'error')}
                      />
                    </td>
                    <td className="font-mono max-w-0">
                      <span className="block truncate" title={m.account_name || m.account_id || ''}
                            style={{ color: 'var(--c-ink-2)' }}>
                        {m.source === 'custom_endpoint' ? (m.account_name || m.account_id || '—') : '—'}
                      </span>
                    </td>
                    <td className="text-right font-mono tabular-nums whitespace-nowrap">
                      {m.db_rpm != null ? (
                        <>
                          <span style={{ color: 'var(--c-ink)' }}>{m.db_rpm}</span>
                          <span style={{ color: 'var(--c-ink-3)' }}> / {m.rpm}</span>
                        </>
                      ) : (m.rpm || '—')}
                    </td>
                    <td className="text-right font-mono tabular-nums whitespace-nowrap">
                      {m.db_tpm != null ? (
                        <>
                          <span style={{ color: 'var(--c-ink)' }}>{m.db_tpm}</span>
                          <span style={{ color: 'var(--c-ink-3)' }}> / {m.tpm}</span>
                        </>
                      ) : (m.tpm || '—')}
                    </td>
                    <td className="text-right font-mono tabular-nums whitespace-nowrap">
                      {m.db_rpd != null ? (
                        <>
                          <span style={{ color: 'var(--c-ink)' }}>{m.db_rpd}</span>
                          <span style={{ color: 'var(--c-ink-3)' }}> / {m.rpd}</span>
                        </>
                      ) : (m.rpd || '—')}
                    </td>
                    <td>
                      {m.in_db
                        ? <Badge variant="primary">DB</Badge>
                        : <Badge variant="ghost">env</Badge>}
                    </td>
                    <td>
                      {m.source === 'gemini' && m.enabled === false
                        ? <Badge variant="error" dot>{t('st_disabled', lang)}</Badge>
                        : <Badge variant="success" dot>{t('st_enabled', lang)}</Badge>}
                    </td>
                    <td className="text-right">
                      <div className="inline-flex items-center gap-0.5">
                        <Button variant="ghost" size="xs" icon={Edit3} onClick={() => openEdit(m)}
                                aria-label={`${t('md_edit', lang)} ${m.alias}`} title={t('md_edit', lang)}>
                          <span className="sr-only">{t('md_edit', lang)}</span>
                        </Button>
                        <Button variant="ghost" size="xs" icon={Trash2} onClick={() => handleDelete(m)}
                                style={{ color: 'var(--c-critical)' }}
                                aria-label={`${t('md_delete', lang)} ${m.alias}`} title={t('md_delete', lang)}>
                          <span className="sr-only">{t('md_delete', lang)}</span>
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {editOpen && editModel && (
        <EditModelModal
          key={editModel.alias}
          model={editModel}
          onClose={() => { setEditOpen(false); setEditModel(null); }}
          onSaved={fetchData}
          token={token}
          lang={lang}
          poolOptions={poolOptions}
        />
      )}
    </div>
  );
}