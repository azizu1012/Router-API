import React, { useState, useEffect, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import { createPortal } from 'react-dom';
import Loading from '../components/Loading';
import { Layers, Save, Trash2, Edit3, Plus, X, RefreshCw, Eye, EyeOff, Info, HelpCircle } from 'lucide-react';

function Tooltip({ text }) {
  return (
    <span className="tooltip tooltip-right" data-tip={text}>
      <HelpCircle className="w-3 h-3 text-base-content/30 hover:text-primary cursor-help transition-colors" />
    </span>
  );
}

const FIELD_META = {
  alias: {
    label: 'Alias',
    desc: 'Tên model hiển thị cho client. VD: gemini-flash-35',
    placeholder: 'gemini-flash-35',
    editDisabled: true,
  },
  display: {
    label: 'Display Name',
    desc: 'Tên thân thiện hiển thị trong dashboard. VD: Gemini Flash 3.5',
    placeholder: 'Gemini Flash 3.5',
    editDisabled: false,
  },
  model_id: {
    label: 'Backing Model ID',
    desc: 'Model ID thật gửi lên API Gemini. VD: gemini-3.5-flash',
    placeholder: 'gemini-3.5-flash',
    editDisabled: false,
  },
  rpm: {
    label: 'RPM',
    desc: 'Requests Per Minute',
    placeholder: '10',
    type: 'number',
  },
  tpm: {
    label: 'TPM',
    desc: 'Tokens Per Minute',
    placeholder: '1000000',
    type: 'number',
  },
  rpd: {
    label: 'RPD',
    desc: 'Requests Per Day',
    placeholder: '1000',
    type: 'number',
  },
  priority: {
    label: 'Priority',
    desc: 'Độ ưu tiên khi chọn model',
    placeholder: '1',
    type: 'number',
  },
  context_length: {
    label: 'Context Length',
    desc: 'Độ dài context tối đa (token)',
    placeholder: '220000',
    type: 'number',
  },
  pool_name: {
    label: 'Pool',
    desc: 'Gán model vào pool để load balance với các thành viên khác.',
    type: 'select',
    editDisabled: false,
  },
};

function FormField({ field, value, onChange, disabled, placeholder, type, editing, options }) {
  const meta = FIELD_META[field];
  if (!meta) return null;
  const isDisabled = editing && meta.editDisabled;

  if (meta.type === 'select') {
    return (
      <div className="form-control w-full">
        <label className="label py-1">
          <span className="label-text text-[11px] font-bold text-base-content/60 uppercase flex items-center gap-1">
            {meta.label}
            <Tooltip text={meta.desc} />
          </span>
        </label>
        <select
          value={value ?? ''}
          onChange={e => onChange(field, e.target.value)}
          disabled={disabled || isDisabled}
          className={`select select-bordered select-sm text-xs w-full ${isDisabled ? 'bg-base-200/50 cursor-not-allowed' : ''}`}
        >
          <option value="">— Không thuộc pool —</option>
          {(options || []).map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </div>
    );
  }

  const inputProps = {
    value: value ?? '',
    onChange: e => onChange(field, meta.type === 'number' ? (parseInt(e.target.value) || 0) : e.target.value),
    placeholder: placeholder || meta.placeholder,
    disabled: disabled || isDisabled,
    className: `input input-bordered input-sm text-xs w-full ${isDisabled ? 'bg-base-200/50 cursor-not-allowed text-base-content/40' : ''}`,
  };
  if (meta.type === 'number') {
    inputProps.type = 'number';
  }

  return (
    <div className="form-control w-full">
      <label className="label py-1">
        <span className="label-text text-[11px] font-bold text-base-content/60 uppercase flex items-center gap-1">
          {meta.label}
          <Tooltip text={meta.desc} />
        </span>
      </label>
      <input {...inputProps} />
    </div>
  );
}

function EditModelModal({ model, onClose, onSaved, token, lang, poolOptions }) {
  const [form, setForm] = useState({
    alias: '', display: '', model_id: '', rpm: 10, tpm: 1000000, rpd: 1000,
    hidden: false, priority: 1, context_length: 220000, pool_name: '', rpd_enabled: false,
    account_id: '',
  });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState({ text: '', type: '' });

  useEffect(() => {
    if (model) {
      setForm({
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
    }
  }, [model]);

  const updateField = (field, val) => setForm(prev => ({ ...prev, [field]: val }));

  const handleSave = async (e) => {
    e.preventDefault();
    if (!form.alias) { setMsg({ text: 'Alias là bắt buộc', type: 'error' }); return; }
    setSaving(true);
    setMsg({ text: '⏳ Đang lưu...', type: 'info' });
    try {
      await api('/dashboard/admin/models/save', {
        method: 'POST',
        body: JSON.stringify(form),
      }, token);
      setMsg({ text: '✅ Đã lưu model ' + form.alias, type: 'success' });
      setTimeout(() => { onSaved(); onClose(); }, 600);
    } catch (err) {
      setMsg({ text: '❌ Lưu thất bại: ' + err.message, type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  if (!model) return null;

  return createPortal(
    <div className="modal modal-open z-[9999] fixed inset-0 flex items-center justify-center">
      <div className="modal-overlay fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose}></div>
      <div className="modal-box max-w-lg bg-base-100 border border-base-content/10 relative z-10 p-6 rounded-2xl shadow-2xl max-h-[90vh] overflow-y-auto">
        <h3 className="font-extrabold text-lg mb-4 text-left">Sửa model: {model.alias}</h3>

        <form onSubmit={handleSave} className="space-y-2 text-left">
          <div className="text-[10px] font-extrabold uppercase tracking-wider text-base-content/40 pb-1 border-b border-base-content/5 mb-3">
            Identity
          </div>
          <FormField field="alias" value={form.alias} onChange={updateField} editing disabled={saving} />
          <FormField field="display" value={form.display} onChange={updateField} editing disabled={saving} />
          <FormField field="model_id" value={form.model_id} onChange={updateField} editing disabled={saving} />
          {model.source === 'custom_endpoint' && model.account_name && (
            <div className="form-control w-full">
              <label className="label py-1">
                <span className="label-text text-[11px] font-bold text-base-content/60 uppercase flex items-center gap-1">Account</span>
              </label>
              <div className="text-xs font-mono bg-base-200/50 px-3 py-2 rounded-lg">{model.account_name}</div>
            </div>
          )}

          <div className="text-[10px] font-extrabold uppercase tracking-wider text-base-content/40 pb-1 border-b border-base-content/5 mb-3 mt-4">
            Rate Limits
          </div>
          <div className="grid grid-cols-3 gap-3">
            <FormField field="rpm" value={form.rpm} onChange={updateField} editing disabled={saving} />
            <FormField field="tpm" value={form.tpm} onChange={updateField} editing disabled={saving} />
            <FormField field="rpd" value={form.rpd} onChange={updateField} editing disabled={saving} />
          </div>

          <div className="text-[10px] font-extrabold uppercase tracking-wider text-base-content/40 pb-1 border-b border-base-content/5 mb-3 mt-4">
            Advanced
          </div>
          <div className="grid grid-cols-2 gap-3">
            <FormField field="priority" value={form.priority} onChange={updateField} editing disabled={saving} />
            <FormField field="context_length" value={form.context_length} onChange={updateField} editing disabled={saving} />
          </div>
          <FormField field="pool_name" value={form.pool_name} onChange={updateField} editing disabled={saving} options={poolOptions} />

          <div className="flex flex-wrap items-center gap-4 pt-1">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" className="checkbox checkbox-xs checkbox-primary"
                checked={form.hidden}
                onChange={e => updateField('hidden', e.target.checked)}
                disabled={saving} />
              <span className="text-[11px] font-bold text-base-content/70 flex items-center gap-1">
                Hidden
                <Tooltip text="Ẩn model khỏi danh sách /v1/models" />
              </span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" className="checkbox checkbox-xs checkbox-primary"
                checked={form.rpd_enabled}
                onChange={e => updateField('rpd_enabled', e.target.checked)}
                disabled={saving} />
              <span className="text-[11px] font-bold text-base-content/70 flex items-center gap-1">
                RPD Enabled
                <Tooltip text="Bật giới hạn RPD cho model này." />
              </span>
            </label>
          </div>

          {msg.text && (
            <div className={`text-xs font-semibold p-3 rounded-lg border ${
              msg.type === 'success' ? 'bg-success/10 text-success border-success/20' :
              msg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-info/10 text-info border-info/20'
            }`}>
              {msg.text}
            </div>
          )}

          <div className="modal-action gap-2 justify-end pt-2 border-t border-base-content/5 mt-5">
            <button type="button" onClick={onClose} disabled={saving} className="btn btn-sm btn-ghost normal-case font-bold">Huỷ</button>
            <button type="submit" disabled={saving} className="btn btn-sm btn-primary normal-case font-bold px-4 gap-1">
              {saving ? <><span className="loading loading-spinner loading-xs"></span> Đang lưu...</> : <><Save className="w-3.5 h-3.5" /> Lưu thay đổi</>}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}

function PoolCell({ model, poolOptions, token, onRefresh }) {
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
      alert('Lỗi: ' + err.message);
    } finally {
      setAssigning(false);
    }
  };

  if (model.source === 'custom_endpoint') {
    return (
      <div className="flex items-center gap-1">
        <select
          value={model.pool_name || ''}
          onChange={e => handleChange(e.target.value)}
          disabled={assigning}
          className="select select-ghost select-xs text-[10px] font-mono max-w-[130px] bg-transparent"
        >
          <option value="">—</option>
          {poolOptions.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        {assigning && <span className="loading loading-spinner loading-xs"></span>}
      </div>
    );
  }

  return <span className="text-xs font-mono">{model.pool_name || '-'}</span>;
}

export default function ModelsTab() {
  const { tabData, token, lang } = useApp();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState({ text: '', type: '' });

  const [showAddForm, setShowAddForm] = useState(false);
  const [addForm, setAddForm] = useState({
    alias: '', display: '', model_id: '', rpm: 10, tpm: 1000000, rpd: 1000,
    hidden: false, priority: 1, context_length: 220000, pool_name: '',
    account_id: '',
  });
  const [adding, setAdding] = useState(false);
  const [addMsg, setAddMsg] = useState({ text: '', type: '' });

  const endpoints = data?.endpoints || tabData.ep || [];

  const [selectedEpInfo, setSelectedEpInfo] = useState(null);
  const endpointModelOptions = [];
  for (const ep of endpoints) {
    if (!ep.enabled) continue;
    const accountName = ep.account_name || '';
    for (const mid of (ep.enabled_models || [])) {
      endpointModelOptions.push({
        epName: ep.name,
        modelId: mid,
        accountName,
        accountId: ep.account_id || '',
        label: `${ep.name} → ${mid}`,
      });
    }
  }

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

  const [editModel, setEditModel] = useState(null);
  const [editOpen, setEditOpen] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api('/dashboard/admin/models', {}, token);
      setData(res);
    } catch (err) {
      setMsg({ text: 'Lỗi tải: ' + err.message, type: 'error' });
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const showMsg = (text, type) => {
    setMsg({ text, type });
    setTimeout(() => setMsg({ text: '', type: '' }), 3000);
  };

  const resetAddForm = () => {
    setSelectedEpInfo(null);
    setAddForm({
      alias: '', display: '', model_id: '', rpm: 10, tpm: 1000000, rpd: 1000,
      hidden: false, priority: 1, context_length: 220000, pool_name: '',
      account_id: '',
    });
    setAddMsg({ text: '', type: '' });
  };

  const updateAddField = (field, val) => setAddForm(prev => ({ ...prev, [field]: val }));

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!addForm.alias) { setAddMsg({ text: 'Alias là bắt buộc', type: 'error' }); return; }
    setAdding(true);
    setAddMsg({ text: '⏳ Đang thêm...', type: 'info' });
    try {
      await api('/dashboard/admin/models/save', {
        method: 'POST',
        body: JSON.stringify(addForm),
      }, token);
      setAddMsg({ text: '✅ Đã thêm model ' + addForm.alias, type: 'success' });
      setTimeout(() => {
        resetAddForm();
        setShowAddForm(false);
        fetchData();
      }, 800);
    } catch (err) {
      setAddMsg({ text: '❌ Thêm thất bại: ' + err.message, type: 'error' });
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (model) => {
    if (model.source === 'custom_endpoint') {
      const actions = [];
      if (model.pool_name) actions.push('xoá pool assignment');
      actions.push('xoá model khỏi endpoint');
      if (!confirm(`Xoá custom endpoint model "${model.alias}" khỏi hệ thống?\n(Hành động: ${actions.join(' + ')} )`)) return;
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
        showMsg('Đã xoá ' + model.alias + ' khỏi endpoint', 'success');
        fetchData();
      } catch (err) {
        showMsg('Xoá thất bại: ' + err.message, 'error');
      }
      return;
    }

    if (!confirm(`Xoá model "${model.alias}" khỏi DB và env?`)) return;
    try {
      await api('/dashboard/admin/models/delete', {
        method: 'POST',
        body: JSON.stringify({ alias: model.alias }),
      }, token);
      showMsg('Đã xoá ' + model.alias, 'success');
      fetchData();
    } catch (err) {
      showMsg('Xoá thất bại: ' + err.message, 'error');
    }
  };

  const openEdit = (model) => {
    setEditModel(model);
    setEditOpen(true);
  };

  if (loading) return <Loading message="Đang tải model config..." />;

  const models = data?.models || [];
  const poolOptions = (data?.pools || []).map(p => p.name);

  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 animate-tab-in">
        <div className="text-left">
          <h1 className="text-2xl font-black tracking-tight flex items-center gap-2">
            <Layers className="w-6 h-6 text-primary" />
            Quản lý Model Config
          </h1>
          <p className="text-xs text-base-content/60 mt-1">
            Chỉnh RPM/TPM/RPD, gán pool, thêm/xoá model. Lưu DB trước, sync env sau.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={fetchData} className="btn btn-ghost btn-sm gap-1 font-bold">
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
          <button onClick={() => { resetAddForm(); setShowAddForm(v => !v); }}
            className="btn btn-primary btn-sm gap-2 font-bold shadow-lg shadow-primary/25 hover:scale-[1.03] active:scale-[0.97] transition-all">
            <Plus className="w-4 h-4" /> Thêm Model
          </button>
        </div>
      </div>

      {msg.text && (
        <div className={`text-xs font-semibold p-3 rounded-lg border ${
          msg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-success/10 text-success border-success/20'
        }`}>
          {msg.text}
        </div>
      )}

      {showAddForm && (
        <div className="card glass-card p-5 rounded-2xl text-left border border-primary/20 animate-fade-in-up">
          <h3 className="font-extrabold text-sm mb-4 flex items-center gap-2">
            <Plus className="w-4 h-4 text-primary" />
            Thêm Model Mới
          </h3>
          <form onSubmit={handleAdd} className="space-y-3">
            <div className="form-control w-full">
              <label className="label py-1">
                <span className="label-text text-[11px] font-bold text-base-content/60 uppercase flex items-center gap-1">
                  Chọn từ Custom Endpoint
                  <Tooltip text="Chọn model từ custom endpoint đã kết nối. Thông tin sẽ tự động điền vào các field bên dưới." />
                </span>
              </label>
              <select
                onChange={e => pickFromEndpoint(e.target.value)}
                disabled={adding}
                className="select select-bordered select-sm text-xs w-full"
              >
                <option value="">— Chọn endpoint + model —</option>
                {endpointModelOptions.length > 0 ? endpointModelOptions.map(opt => (
                  <option key={opt.epName + '|' + opt.modelId} value={opt.epName + '|' + opt.modelId}>
                    {opt.label}{opt.accountName ? ` (${opt.accountName})` : ''}
                  </option>
                )) : (
                  <option value="" disabled>Không có custom endpoint nào</option>
                )}
              </select>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <FormField field="alias" value={addForm.alias} onChange={updateAddField} disabled={adding} />
              <FormField field="display" value={addForm.display} onChange={updateAddField} disabled={adding} />
              <FormField field="model_id" value={addForm.model_id} onChange={updateAddField} disabled={adding} />
            </div>

            {selectedEpInfo && (
              <div className="bg-base-200/30 rounded-lg px-3 py-2 text-xs">
                <span className="font-bold text-base-content/60">Account: </span>
                <span className="font-mono">{selectedEpInfo.accountName || selectedEpInfo.accountId || '-'}</span>
              </div>
            )}

            <div className="text-[10px] font-extrabold uppercase tracking-wider text-base-content/40 pb-1 border-b border-base-content/5 mb-1">
              Rate Limits
            </div>
            <div className="grid grid-cols-3 gap-4">
              <FormField field="rpm" value={addForm.rpm} onChange={updateAddField} disabled={adding} />
              <FormField field="tpm" value={addForm.tpm} onChange={updateAddField} disabled={adding} />
              <FormField field="rpd" value={addForm.rpd} onChange={updateAddField} disabled={adding} />
            </div>

            <div className="text-[10px] font-extrabold uppercase tracking-wider text-base-content/40 pb-1 border-b border-base-content/5 mb-1 mt-3">
              Advanced
            </div>
            <div className="grid grid-cols-2 gap-4">
              <FormField field="priority" value={addForm.priority} onChange={updateAddField} disabled={adding} />
              <FormField field="context_length" value={addForm.context_length} onChange={updateAddField} disabled={adding} />
            </div>
            <FormField field="pool_name" value={addForm.pool_name} onChange={updateAddField} disabled={adding} options={poolOptions} />

            <div className="flex flex-wrap items-center gap-4 pt-1">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" className="checkbox checkbox-xs checkbox-primary"
                  checked={addForm.hidden}
                  onChange={e => updateAddField('hidden', e.target.checked)}
                  disabled={adding} />
                <span className="text-[11px] font-bold text-base-content/70 flex items-center gap-1">
                  Hidden
                  <Tooltip text="Ẩn model khỏi danh sách /v1/models" />
                </span>
              </label>
            </div>

            {addMsg.text && (
              <div className={`text-xs font-semibold p-3 rounded-lg border ${
                addMsg.type === 'success' ? 'bg-success/10 text-success border-success/20' :
                addMsg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-info/10 text-info border-info/20'
              }`}>
                {addMsg.text}
              </div>
            )}

            <div className="flex gap-2 justify-end pt-2 border-t border-base-content/5 mt-4">
              <button type="button" onClick={() => setShowAddForm(false)} disabled={adding}
                className="btn btn-sm btn-ghost normal-case font-bold">Huỷ</button>
              <button type="submit" disabled={adding}
                className="btn btn-sm btn-primary normal-case font-bold px-4 gap-1">
                {adding ? <><span className="loading loading-spinner loading-xs"></span> Đang thêm...</> : <><Plus className="w-3.5 h-3.5" /> Thêm</>}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Models Table */}
      <div className="flex justify-between items-center bg-base-200/20 px-4 py-3 rounded-xl border border-base-content/5 animate-fade-in-up cascade-1">
        <span className="text-xs font-bold text-base-content/70 flex items-center gap-1.5">
          <Layers className="w-4 h-4 text-indigo-400" />
          Danh sách Model
        </span>
        <span className="badge badge-primary font-bold text-xs">{models.length} models</span>
      </div>

      <div className="card glass-card rounded-2xl overflow-hidden text-left border border-base-content/5 animate-fade-in-up cascade-2">
        <div className="overflow-x-auto w-full">
          <table className="table table-zebra w-full text-xs">
            <thead>
              <tr className="border-b border-base-content/5 text-base-content/60 bg-base-200/35">
                <th className="font-bold min-w-[100px]">Alias</th>
                <th className="font-bold min-w-[90px]">Display</th>
                <th className="font-bold min-w-[75px]">Source</th>
                <th className="font-bold min-w-[90px]">Model ID</th>
                <th className="font-bold min-w-[80px]">Pool</th>
                <th className="font-bold min-w-[60px]">Account</th>
                <th className="font-bold min-w-[45px]">RPM</th>
                <th className="font-bold min-w-[55px]">TPM</th>
                <th className="font-bold min-w-[45px]">RPD</th>
                <th className="font-bold min-w-[40px]">DB</th>
                <th className="font-bold min-w-[80px]">Hành động</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.source + '|' + m.alias} className={`border-b border-base-content/5 hover:bg-base-200/50 ${m.hidden ? 'opacity-50' : ''}`}>
                  <td className="font-mono font-bold text-xs">{m.alias}</td>
                  <td className="text-xs text-base-content/80 truncate max-w-[150px]">{m.display}</td>
                  <td>
                    <span className={`badge badge-xs font-bold ${m.source === 'gemini' ? 'badge-primary' : 'badge-ghost'}`}>
                      {m.source === 'gemini' ? 'Gemini' : m.endpoint || 'custom'}
                    </span>
                  </td>
                  <td className="font-mono text-xs text-base-content/50">{m.model_id}</td>
                  <td>
                    <PoolCell model={m} poolOptions={poolOptions} token={token} onRefresh={fetchData} />
                  </td>
                  <td className="text-xs text-base-content/70">
                    {m.source === 'custom_endpoint' ? (
                      <span className="font-mono">{m.account_name || m.account_id || '-'}</span>
                    ) : '-'}
                  </td>
                  <td className="font-mono text-xs">
                    {m.db_rpm != null ? <><span className="text-primary">{m.db_rpm}</span><span className="text-base-content/30"> / {m.rpm}</span></> : m.rpm || '-'}
                  </td>
                  <td className="font-mono text-xs">
                    {m.db_tpm != null ? <><span className="text-primary">{m.db_tpm}</span><span className="text-base-content/30"> / {m.tpm}</span></> : m.tpm || '-'}
                  </td>
                  <td className="font-mono text-xs">
                    {m.db_rpd != null ? <><span className="text-primary">{m.db_rpd}</span><span className="text-base-content/30"> / {m.rpd}</span></> : m.rpd || '-'}
                  </td>
                  <td>
                    {m.in_db
                      ? <span className="badge badge-xs badge-success font-bold">DB</span>
                      : <span className="badge badge-xs badge-ghost font-bold">env</span>}
                  </td>
                  <td>
                    <div className="flex items-center gap-0.5">
                      <button onClick={() => openEdit(m)} className="btn btn-ghost btn-xs btn-square text-primary hover:bg-primary/15" title="Edit model">
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleDelete(m)} className="btn btn-ghost btn-xs btn-square text-error hover:bg-error/15" title="Delete model">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Edit Modal */}
      {editOpen && (
        <EditModelModal
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