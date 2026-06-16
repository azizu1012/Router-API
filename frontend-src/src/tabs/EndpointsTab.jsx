import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import { Plus, Trash2, Network, RefreshCw, Power, Info, Terminal as TerminalIcon, Key } from 'lucide-react';
import LogHistoryModal from '../components/LogHistoryModal';
import Loading from '../components/Loading';

export default function EndpointsTab() {
  const { tabData, token, lang, refreshTab } = useApp();
  const endpoints = tabData.ep || [];
  const accounts = tabData.ac || [];

  const [newName, setNewName] = useState('');
  const [newUrl, setNewUrl] = useState('');
  const [newKey, setNewKey] = useState('');
  const [showAddForm, setShowAddForm] = useState(false);
  const [addMsg, setAddMsg] = useState({ text: '', type: '' });
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [epLoadingStates, setEpLoadingStates] = useState({});
  const [epMsgStates, setEpMsgStates] = useState({});
  const [selectedEndpoint, setSelectedEndpoint] = useState(null);

  if (!tabData.ep) {
    return <Loading message={t('loading', lang)} />;
  }

  const handleAddEndpoint = async (e) => {
    e.preventDefault();
    if (!newName.trim() || !newUrl.trim()) return;
    setIsSubmitting(true);
    setAddMsg({ text: '⏳ Đang thêm...', type: 'info' });
    try {
      await api('/dashboard/admin/endpoints/add', {
        method: 'POST',
        body: JSON.stringify({ name: newName.trim(), base_url: newUrl.trim(), auth_key: newKey.trim() || undefined })
      }, token);
      setNewName(''); setNewUrl(''); setNewKey('');
      setAddMsg({ text: t('msg_ep_added', lang) || 'Đã thêm Endpoint thành công!', type: 'success' });
      refreshTab();
      setShowAddForm(false);
      setTimeout(() => setAddMsg({ text: '', type: '' }), 4000);
    } catch (err) {
      setAddMsg({ text: (t('msg_ep_error', lang) || 'Lỗi thêm Endpoint: ') + err.message, type: 'error' });
    } finally {
      setIsSubmitting(false);
    }
  };

  const setEpMsg = (epName, text, type) => {
    setEpMsgStates(prev => ({ ...prev, [epName]: { text, type } }));
    if (type === 'success') setTimeout(() => setEpMsgStates(prev => ({ ...prev, [epName]: { text: '', type: '' } })), 3000);
  };

  const handleToggleEp = async (epName, isEnabled) => {
    const action = isEnabled ? 'disable' : 'enable';
    try {
      await api('/dashboard/admin/endpoints/toggle', { method: 'POST', body: JSON.stringify({ name: epName, action }) }, token);
      refreshTab();
    } catch (err) { alert('Error: ' + err.message); }
  };

  const handleRefreshModels = async (epName) => {
    setEpLoadingStates(prev => ({ ...prev, [epName]: true }));
    setEpMsg(epName, '⏳ Fetching models...', 'info');
    try {
      const data = await api('/dashboard/admin/endpoints/refresh', { method: 'POST', body: JSON.stringify({ name: epName }) }, token);
      setEpMsg(epName, `✅ Thành công: Cập nhật được ${data.count} models từ Endpoint`, 'success');
      refreshTab();
    } catch (err) {
      setEpMsg(epName, `❌ Lỗi: ${err.message}`, 'error');
    } finally {
      setEpLoadingStates(prev => ({ ...prev, [epName]: false }));
    }
  };

  const handleDeleteEp = async (epName) => {
    if (!confirm(`Remove endpoint "${epName}"?`)) return;
    try {
      await api('/dashboard/admin/endpoints/delete', { method: 'POST', body: JSON.stringify({ name: epName }) }, token);
      refreshTab();
    } catch (err) { alert('Error: ' + err.message); }
  };

  const handleAccountAssign = async (epName, accountName) => {
    try {
      await api('/dashboard/admin/endpoints/assign', { method: 'POST', body: JSON.stringify({ name: epName, account_id: accountName }) }, token);
      setEpMsg(epName, `✅ Gán thành công cho: ${accountName || 'Không gán'}`, 'success');
      refreshTab();
    } catch (err) { setEpMsg(epName, `❌ Lỗi gán tài khoản: ${err.message}`, 'error'); }
  };

  const handleModelToggle = async (epName, modelId, isChecked) => {
    try {
      await api('/dashboard/admin/endpoints/toggle-model', { method: 'POST', body: JSON.stringify({ name: epName, model_id: modelId, enabled: isChecked }) }, token);
      setEpMsg(epName, `✅ Đã ${isChecked ? 'bật' : 'tắt'} model ${modelId}`, 'success');
      refreshTab();
    } catch (err) { setEpMsg(epName, `❌ Lỗi toggle model: ${err.message}`, 'error'); }
  };

  return (
    <div className="space-y-5">
      {/* Title + Add button */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 animate-tab-in">
        <div className="text-left">
          <h1 className="text-2xl font-black tracking-tight">{t('nav_ep', lang) || 'Endpoint Tùy Chỉnh'}</h1>
          <p className="text-xs text-base-content/60 mt-1">{t('ep_sub', lang) || 'Kết nối các endpoint OpenAI/Gemini bên thứ ba và gán cho các tài khoản con khác nhau'}</p>
        </div>
        <button
          onClick={() => setShowAddForm(v => !v)}
          className="btn btn-primary btn-sm gap-2 font-bold shadow-lg shadow-primary/25 hover:scale-[1.03] active:scale-[0.97] transition-all"
        >
          <Plus className="w-4 h-4" />
          {t('btn_add', lang)}
        </button>
      </div>

      {/* Collapsible Add Endpoint Form */}
      {showAddForm && (
        <div className="card glass-card p-5 rounded-2xl text-left border border-primary/20 animate-fade-in-up">
          <h3 className="font-extrabold text-sm mb-4 flex items-center gap-2">
            <Plus className="w-4 h-4 text-primary" />
            {t('ep_add_title', lang) || 'Thêm Endpoint Mới'}
          </h3>
          <form onSubmit={handleAddEndpoint} className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Tên Endpoint</span></label>
              <input type="text" required value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. OpenRouter_Main" className="input input-bordered input-sm text-xs w-full" />
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">URL Cơ Sở (Base URL)</span></label>
              <input type="url" required value={newUrl} onChange={(e) => setNewUrl(e.target.value)} placeholder="https://api.openrouter.ai/v1" className="input input-bordered input-sm text-xs w-full" />
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Auth Key (Optional)</span></label>
              <input type="password" value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="sk-or-..." className="input input-bordered input-sm text-xs w-full" autoComplete="off" />
            </div>
            <div className="sm:col-span-3 flex gap-3 justify-end">
              <button type="button" onClick={() => setShowAddForm(false)} className="btn btn-ghost btn-sm font-bold">Hủy</button>
              <button type="submit" disabled={isSubmitting} className="btn btn-primary btn-sm font-bold">
                {isSubmitting ? <span className="loading loading-spinner loading-xs"></span> : t('btn_add', lang)}
              </button>
            </div>
          </form>
          {addMsg.text && (
            <div className={`text-xs font-semibold mt-3 p-3 rounded-lg border ${
              addMsg.type === 'success' ? 'bg-success/10 text-success border-success/20' :
              addMsg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-info/10 text-info border-info/20'
            }`}>{addMsg.text}</div>
          )}
        </div>
      )}

      {/* Endpoints Count Header */}
      <div className="flex justify-between items-center bg-base-200/20 px-4 py-3 rounded-xl border border-base-content/5 animate-fade-in-up cascade-1">
        <span className="text-xs font-bold text-base-content/70 flex items-center gap-1.5">
          <Network className="w-4 h-4 text-indigo-400" />
          Danh sách Endpoint kết nối
        </span>
        <span className="badge badge-primary font-bold text-xs">{endpoints.length} {t('endpoints_count', lang)}</span>
      </div>

      {/* Endpoints Card List — grouped by shared auth_key */}
      <div className="space-y-6 animate-fade-in-up cascade-2">
        {endpoints.length > 0 ? (
          (() => {
            const groups = {};
            endpoints.forEach(ep => {
              const key = ep.auth_key ? ep.auth_key.slice(-8) : 'unassigned';
              if (!groups[key]) groups[key] = [];
              groups[key].push(ep);
            });

            return Object.entries(groups).map(([keyHash, group]) => (
              <div key={keyHash} className="space-y-3">
                {/* Group header */}
                {keyHash !== 'unassigned' && (
                  <div className="flex items-center gap-2 px-3 py-2 text-[11px] font-bold text-base-content/40 bg-base-200/20 rounded-xl border border-base-content/5">
                    <Key className="w-3.5 h-3.5" />
                    <span>Shared Key: ...{keyHash}</span>
                    <span className="badge badge-xs badge-ghost font-bold">{group.length}</span>
                  </div>
                )}

                {group.map((ep, idx) => {
                  const isEnabled = ep.enabled !== false;
                  const models = ep.models || [];
                  const enabledModels = ep.enabled_models || [];
                  const accountName = ep.account_name || '';
                  const assignedAccountId = ep.account_id || '';
                  const isLoading = !!epLoadingStates[ep.name];
                  const msg = epMsgStates[ep.name] || { text: '', type: '' };

                  return (
                    <div key={idx} className="card glass-card p-5 rounded-2xl text-left border border-base-content/5 space-y-4 shadow-md transition-all duration-300 hover:border-base-content/10">
                      {/* Endpoint Header */}
                      <div className="flex items-center justify-between flex-wrap gap-3">
                        <div className="flex items-center gap-2.5 min-w-0">
                          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${isEnabled ? 'bg-success' : 'bg-error'}`}></span>
                          <strong className="text-sm text-base-content font-extrabold">{ep.name}</strong>
                          <span className="text-xs font-mono text-base-content/50 truncate max-w-[150px] sm:max-w-xs" title={ep.base_url}>{ep.base_url}</span>
                          {accountName && (
                            <span className="badge badge-sm badge-success font-extrabold text-[10px] uppercase">{accountName}</span>
                          )}
                        </div>

                        <div className="flex items-center gap-1.5">
                          <button onClick={() => setSelectedEndpoint(ep.name)}
                            className="btn btn-ghost btn-xs text-green-400 hover:bg-green-500/15 gap-1 normal-case font-bold" title="View Logs">
                            <TerminalIcon className="w-3.5 h-3.5" />
                            <span>Logs</span>
                          </button>
                          <button onClick={() => handleRefreshModels(ep.name)} disabled={isLoading}
                            className="btn btn-ghost btn-xs text-primary hover:bg-primary/15 gap-1 normal-case font-bold" title="Fetch Models">
                            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
                            <span>Fetch Models</span>
                          </button>
                          <button onClick={() => handleToggleEp(ep.name, isEnabled)}
                            className={`btn btn-ghost btn-xs gap-1 normal-case font-bold ${isEnabled ? 'text-error hover:bg-error/15' : 'text-success hover:bg-success/15'}`}>
                            <Power className="w-3.5 h-3.5" />
                            <span>{isEnabled ? 'Disable' : 'Enable'}</span>
                          </button>
                          <button onClick={() => handleDeleteEp(ep.name)} className="btn btn-ghost btn-xs btn-square text-error hover:bg-error/15">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      {/* Assignment & Models */}
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-3 border-t border-base-content/5">
                        <div className="space-y-2 text-left sm:col-span-1">
                          <label className="text-[10px] font-bold uppercase tracking-wider text-base-content/50 block">
                            {t('lbl_assigned_account', lang) || 'Gán cho tài khoản con'}
                          </label>
                          <select
                            value={accountName || assignedAccountId}
                            onChange={(e) => handleAccountAssign(ep.name, e.target.value)}
                            className="select select-bordered select-sm text-xs w-full bg-base-200/70"
                          >
                            <option value="">— Chưa gán (No assignment) —</option>
                            {accounts.filter(a => a.enabled).map((a, i) => (
                              <option key={i} value={a.name}>{a.name} ({a.tier})</option>
                            ))}
                          </select>
                        </div>

                        <div className="sm:col-span-2 space-y-2">
                          <label className="text-[10px] font-bold uppercase tracking-wider text-base-content/50 block">
                            Các Model được hỗ trợ ({models.length})
                          </label>
                          {models.length > 0 ? (
                            <div className="collapse collapse-arrow bg-base-200/40 border border-base-content/5 rounded-xl transition-all duration-300">
                              <input type="checkbox" className="min-h-0 py-2" defaultChecked />
                              <div className="collapse-title text-[11px] font-bold min-h-0 py-2">Xem chi tiết các Model hỗ trợ</div>
                              <div className="collapse-content px-4 pb-4">
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2 max-h-60 overflow-y-auto pr-1">
                                  {models.map((mid, mIdx) => {
                                    const isModelEnabled = enabledModels.includes(mid);
                                    return (
                                      <div key={mIdx} className="flex items-center justify-between p-2 rounded-lg bg-base-300/30 border border-base-content/5 text-xs transition-all duration-200 hover:scale-[1.015]">
                                        <span className={`font-mono truncate max-w-[130px] font-bold ${isModelEnabled ? 'text-success' : 'text-base-content/40'}`} title={mid}>{mid}</span>
                                        <input type="checkbox" checked={isModelEnabled}
                                          onChange={(e) => handleModelToggle(ep.name, mid, e.target.checked)}
                                          className="toggle toggle-xs toggle-success" />
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            </div>
                          ) : (
                            <div className="flex items-center gap-2 text-xs text-base-content/40 bg-base-200/20 p-3 rounded-xl border border-dashed border-base-content/10">
                              <Info className="w-4 h-4 text-base-content/50" />
                              <span>Chưa fetch danh sách Model — Hãy click 🔄 Fetch Models để lấy danh sách.</span>
                            </div>
                          )}
                        </div>
                      </div>

                      {msg.text && (
                        <div className={`text-xs font-semibold p-2.5 rounded-lg border ${
                          msg.type === 'success' ? 'bg-success/10 text-success border-success/20' :
                          msg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-info/10 text-info border-info/20'
                        }`}>{msg.text}</div>
                      )}
                    </div>
                  );
                })}
              </div>
            ));
          })()
        ) : (
          <div className="card glass-card p-12 text-center text-base-content/50 rounded-2xl">
            <Network className="w-10 h-10 mx-auto opacity-35 mb-2" />
            <p className="font-semibold">{t('no_endpoints', lang) || 'Chưa cấu hình Endpoint nào'}</p>
          </div>
        )}
      </div>

      {/* Log Terminal Modal */}
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
