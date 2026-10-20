import React, { useEffect, useState } from 'react';
import { Plus, Trash2, Edit3, RefreshCw, Power } from 'lucide-react';
import { useApp } from '../context/AppContext';
import { api } from '../utils/api';
import Loading from '../components/Loading';

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
  } catch (_) { /* fall back to legacy fields */ }
  return [{ model: alias.target_model || '', endpoint: alias.target_endpoint || '', weight: 1, priority: 1 }];
}

export default function ComboModelsTab() {
  const { token } = useApp();
  const [aliases, setAliases] = useState([]);
  const [endpoints, setEndpoints] = useState([]);
  const [form, setForm] = useState(blankForm());
  const [editing, setEditing] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState({ text: '', type: '' });

  const notify = (text, type = 'info') => {
    setMessage({ text, type });
    if (type === 'success') setTimeout(() => setMessage({ text: '', type: '' }), 3500);
  };

  const load = async () => {
    setLoading(true);
    try {
      const [aliasData, endpointData] = await Promise.all([
        api('/api/me/aliases', { method: 'GET' }, token),
        api('/api/me/endpoints', { method: 'GET' }, token)
      ]);
      const aliasList = Array.isArray(aliasData) ? aliasData : (aliasData.aliases || []);
      setAliases(aliasList.filter(alias => {
        try {
          const targets = typeof alias.targets === 'string' ? JSON.parse(alias.targets || '[]') : alias.targets;
          return Array.isArray(targets) && targets.length > 0;
        } catch (_) { return false; }
      }));
      const endpointList = Array.isArray(endpointData) ? endpointData : (endpointData.endpoints || endpointData.ep || []);
      setEndpoints(endpointList);
    } catch (err) {
      notify(`❌ Không tải được combo: ${err.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [token]);

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
    return ['gemini-flash', 'gemini-flash-lite', 'gemini-pro'];
  };

  const normalizedTargets = form.targets.filter(target => target.model.trim()).map((target, index) => ({
    model: target.model.trim(),
    endpoint: target.endpoint || null,
    priority: Number(target.priority) || index + 1,
    weight: Number(target.weight) || 1
  }));

  const selectTargetModel = (index, value) => updateTarget(index, 'model', value);

  const renderModelOptions = endpointName => getModelsForEndpoint(endpointName).map(model => (
    <option key={model} value={model}>{model}</option>
  ));


  const reset = () => { setForm(blankForm()); setEditing(null); setShowForm(false); };

  const submit = async event => {
    event.preventDefault();
    if (!form.alias_name.trim() || !normalizedTargets.length) {
      notify('❌ Cần nhập tên combo và ít nhất một target model.', 'error');
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
    try {
      const url = editing ? `/api/me/aliases/${editing.alias_id}` : '/api/me/aliases';
      await api(url, { method: editing ? 'PATCH' : 'POST', body: JSON.stringify(payload) }, token);
      notify(editing ? '✅ Đã cập nhật combo.' : '✅ Đã tạo combo.', 'success');
      reset();
      load();
    } catch (err) {
      notify(`❌ Lỗi lưu combo: ${err.message}`, 'error');
    }
  };

  const edit = alias => {
    setEditing(alias);
    setForm({ alias_name: alias.alias_name || '', label: alias.label || '', strategy: alias.strategy || 'waterfall', enabled: Boolean(alias.enabled), targets: decodeTargets(alias) });
    setShowForm(true);
  };

  const remove = async alias => {
    if (!window.confirm(`Xóa combo "${alias.alias_name}"?`)) return;
    try {
      await api(`/api/me/aliases/${alias.alias_id}`, { method: 'DELETE' }, token);
      notify('✅ Đã xóa combo.', 'success');
      load();
    } catch (err) { notify(`❌ Lỗi xóa combo: ${err.message}`, 'error'); }
  };

  if (loading) return <Loading message="Đang tải Combo Model Mix..." />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h1 className="text-2xl font-black">Combo Model Mix</h1><p className="text-sm text-base-content/60">Tự tạo alias dùng nhiều model với fallback hoặc load balance.</p></div>
        <div className="flex gap-2"><button className="btn btn-ghost btn-sm" onClick={load}><RefreshCw className="w-4 h-4" /> Làm mới</button><button className="btn btn-primary btn-sm" onClick={() => { setForm(blankForm()); setEditing(null); setShowForm(true); }}><Plus className="w-4 h-4" /> Tạo combo</button></div>
      </div>
      {message.text && <div className={`alert ${message.type === 'error' ? 'alert-error' : 'alert-info'}`}>{message.text}</div>}
      {showForm && <form onSubmit={submit} className="card glass-card p-5 space-y-4 border border-primary/20">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3"><input className="input input-bordered" placeholder="Tên combo alias" value={form.alias_name} onChange={e => setForm({ ...form, alias_name: e.target.value })} required /><input className="input input-bordered" placeholder="Mô tả (tùy chọn)" value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} /><select className="select select-bordered" value={form.strategy} onChange={e => setForm({ ...form, strategy: e.target.value })}><option value="waterfall">Waterfall fallback</option><option value="load_balance">Load balance theo weight</option></select></div>
        <div className="space-y-2">{form.targets.map((target, index) => <div key={index} className="grid grid-cols-1 md:grid-cols-12 gap-2"><select className="select select-bordered select-sm md:col-span-4 font-mono" value={target.model} onChange={e => selectTargetModel(index, e.target.value)} required={index === 0}><option value="">Chọn target model</option>{renderModelOptions(target.endpoint)}</select><select className="select select-bordered select-sm md:col-span-4" value={target.endpoint} onChange={e => updateTarget(index, 'endpoint', e.target.value)}><option value="">Gemini Pool</option>{endpoints.map(endpoint => <option key={endpoint.name} value={endpoint.name}>{endpoint.name}</option>)}</select><input className="input input-bordered input-sm md:col-span-2" type="number" min="1" placeholder="Weight" value={target.weight} onChange={e => updateTarget(index, 'weight', e.target.value)} /><button type="button" className="btn btn-ghost btn-sm text-error" disabled={form.targets.length === 1} onClick={() => setForm(prev => ({ ...prev, targets: prev.targets.filter((_, i) => i !== index) }))}><Trash2 className="w-4 h-4" /></button></div>)}</div>
        <div className="flex flex-wrap gap-2"><button type="button" className="btn btn-outline btn-sm" onClick={() => setForm(prev => ({ ...prev, targets: [...prev.targets, blankTarget()] }))}><Plus className="w-4 h-4" /> Thêm target</button><button type="submit" className="btn btn-primary btn-sm">{editing ? 'Lưu thay đổi' : 'Tạo combo'}</button><button type="button" className="btn btn-ghost btn-sm" onClick={reset}>Hủy</button></div>
      </form>}
      {!aliases.length ? <div className="card glass-card p-8 text-center text-base-content/60">Chưa có combo nào. Hãy bấm “Tạo combo”.</div> : <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">{aliases.map(alias => <div key={alias.alias_id} className="card glass-card p-4 border border-base-content/10"><div className="flex justify-between gap-2"><div><h2 className="font-bold">{alias.alias_name}</h2><p className="text-xs text-base-content/60">{alias.label || 'Combo Model Mix'} · {alias.strategy || 'waterfall'}</p></div><span className={`badge ${alias.enabled ? 'badge-success' : 'badge-ghost'}`}>{alias.enabled ? 'Bật' : 'Tắt'}</span></div><div className="flex flex-wrap gap-1 my-3">{decodeTargets(alias).map((target, index) => <span className="badge badge-outline font-mono text-xs" key={`${alias.alias_id}-${index}`}>{target.model}{target.weight > 1 ? ` ×${target.weight}` : ''}</span>)}</div><div className="flex gap-2"><button className="btn btn-ghost btn-xs" onClick={() => edit(alias)}><Edit3 className="w-3 h-3" /> Sửa</button><button className="btn btn-ghost btn-xs text-error" onClick={() => remove(alias)}><Trash2 className="w-3 h-3" /> Xóa</button><button className="btn btn-ghost btn-xs" onClick={async () => { try { await api(`/api/me/aliases/${alias.alias_id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !alias.enabled }) }, token); load(); } catch (err) { notify(err.message, 'error'); } }}><Power className="w-3 h-3" /> {alias.enabled ? 'Tắt' : 'Bật'}</button></div></div>)}</div>}
    </div>
  );
}
