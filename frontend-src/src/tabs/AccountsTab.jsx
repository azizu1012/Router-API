import React, { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt, fmtD } from '../utils/format';
import { api } from '../utils/api';
import EditAccountModal from '../components/EditAccountModal';
import Loading from '../components/Loading';
import { Search, Plus, Trash2, ShieldCheck, ShieldAlert, Key, Edit, RefreshCw, Eye, Copy } from 'lucide-react';

export default function AccountsTab() {
  const { tabData, token, lang, refreshTab } = useApp();
  const accounts = tabData.ac || [];

  const [search, setSearch] = useState('');
  const [filterTier, setFilterTier] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');

  const [sortBy, setSortBy] = useState('name');
  const [sortOrder, setSortOrder] = useState('asc');

  // New account form state
  const [newName, setNewName] = useState('');
  const [newTier, setNewTier] = useState('free');
  const [newRpm, setNewRpm] = useState('');
  const [newTpm, setNewTpm] = useState('');
  const [newRpd, setNewRpd] = useState('');
  const [newWebSearchEnabled, setNewWebSearchEnabled] = useState(false);
  const [newSearchEngine, setNewSearchEngine] = useState('auto');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [createMsg, setCreateMsg] = useState({ text: '', type: '' });

  // Edit account modal state
  const [editingAccount, setEditingAccount] = useState(null);
  const [isEditOpen, setIsEditOpen] = useState(false);

  const handleSort = (field) => {
    if (sortBy === field) {
      setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortBy(field);
      setSortOrder(field === 'name' || field === 'tier' ? 'asc' : 'desc');
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

  if (!tabData.ac) {
    return <Loading message={t('loading', lang)} />;
  }

  // Calculate Account stats
  const totalAccs = accounts.length;
  const freeAccs = accounts.filter(a => a.tier === 'free').length;
  const premAccs = accounts.filter(a => a.tier === 'premium').length;
  const adminAccs = accounts.filter(a => a.tier === 'admin').length;

  // Filter accounts list
  const filteredAccounts = accounts.filter(a => {
    const matchesSearch = a.name.toLowerCase().includes(search.toLowerCase().trim()) || 
                          (a.account_id && a.account_id.toLowerCase().includes(search.toLowerCase().trim()));
    const matchesTier = filterTier === 'all' || a.tier === filterTier;
    const matchesStatus = filterStatus === 'all' || 
                          (filterStatus === 'active' && a.enabled) || 
                          (filterStatus === 'disabled' && !a.enabled);
    
    return matchesSearch && matchesTier && matchesStatus;
  });

  const sortedAccounts = [...filteredAccounts].sort((a, b) => {
    let valA = a[sortBy];
    let valB = b[sortBy];

    if (sortBy === 'status') {
      valA = a.enabled ? 1 : 0;
      valB = b.enabled ? 1 : 0;
    } else if (sortBy === 'rpm') {
      valA = a.rpm || 0;
      valB = b.rpm || 0;
    } else if (sortBy === 'tpm') {
      valA = a.tpm || 0;
      valB = b.tpm || 0;
    } else if (sortBy === 'rpd') {
      valA = a.rpd || 0;
      valB = b.rpd || 0;
    } else if (sortBy === 'created') {
      valA = a.created_at || 0;
      valB = b.created_at || 0;
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

  // Handle Create Account
  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newName.trim()) return;

    setIsCreating(true);
    setCreateMsg({ text: '⏳ Đang tạo...', type: 'info' });

    const body = { 
      name: newName.trim(), 
      tier: newTier,
      web_search_enabled: newWebSearchEnabled,
      search_engine: newSearchEngine
    };
    if (newRpm.trim() !== '') body.rpm = parseInt(newRpm, 10);
    if (newTpm.trim() !== '') body.tpm = parseInt(newTpm, 10);
    if (newRpd.trim() !== '') body.rpd = parseInt(newRpd, 10);

    try {
      const res = await api('/dashboard/admin/accounts/create', {
        method: 'POST',
        body: JSON.stringify(body)
      }, token);

      setNewName('');
      setNewRpm('');
      setNewTpm('');
      setNewRpd('');
      setNewTier('free');
      setNewWebSearchEnabled(false);
      setNewSearchEngine('auto');
      
      if (res && res.account) {
        alert(`Tạo tài khoản ${res.account.name} thành công!\nKey truy cập: ${res.account.auth_key}\n\n(Hãy copy và lưu lại khóa này!)`);
      }
      
      setCreateMsg({ text: t('msg_saved', lang), type: 'success' });
      setShowCreateForm(false);
      refreshTab();
      setTimeout(() => setCreateMsg({ text: '', type: '' }), 5000);
    } catch (err) {
      setCreateMsg({ text: '❌ Lỗi: ' + err.message, type: 'error' });
    } finally {
      setIsCreating(false);
    }
  };

  // Handle Toggle Account Status
  const handleToggleStatus = async (accountName, currentEnabled) => {
    const newEnabled = !currentEnabled;
    try {
      await api('/dashboard/admin/accounts/toggle', {
        method: 'POST',
        body: JSON.stringify({ name: accountName, enabled: newEnabled })
      }, token);
      refreshTab();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  // Handle Rotate Account Key
  const handleRotateKey = async (accountName) => {
    if (!confirm(`Rotate Auth Key for account "${accountName}"? This will invalidate the old key immediately.`)) return;
    try {
      const res = await api('/dashboard/admin/accounts/rotate-key', {
        method: 'POST',
        body: JSON.stringify({ name: accountName })
      }, token);
      if (res && res.account) {
        alert(`Xoay Auth Key thành công!\nKey mới: ${res.account.auth_key}\n\n(Hãy lưu lại khóa này!)`);
      }
      refreshTab();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  // Handle Delete Account
  const handleDeleteAccount = async (accountName) => {
    if (!confirm(`Delete account "${accountName}" permanently? All usage statistics and key settings for this account will be lost.`)) return;
    try {
      await api('/dashboard/admin/accounts/delete', {
        method: 'POST',
        body: JSON.stringify({ name: accountName })
      }, token);
      refreshTab();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  // Copy key helper
  const handleCopyKey = (authKey) => {
    if (!authKey) return;
    navigator.clipboard.writeText(authKey);
    alert(t('lbl_refreshed', lang) + ' (Copied)');
  };

  return (
    <div className="space-y-5">
      {/* Title + Create button */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 animate-tab-in">
        <div className="text-left">
          <h1 className="text-2xl font-black tracking-tight">{t('ac_title', lang) || 'Quản lý tài khoản'}</h1>
          <p className="text-xs text-base-content/60 mt-1">{t('ac_sub', lang) || 'Xem danh sách, phân quyền và điều chỉnh hạn mức RPM/TPM/RPD'}</p>
        </div>
        <button
          onClick={() => setShowCreateForm(v => !v)}
          className="btn btn-primary btn-sm gap-2 font-bold shadow-lg shadow-primary/25 hover:scale-[1.03] active:scale-[0.97] transition-all"
        >
          <Plus className="w-4 h-4" />
          {t('btn_add', lang)}
        </button>
      </div>

      {/* Collapsible Create Account Form */}
      {showCreateForm && (
        <div className="card glass-card p-5 rounded-2xl text-left border border-primary/20 animate-fade-in-up">
          <h3 className="font-extrabold text-sm mb-4 flex items-center gap-2">
            <Plus className="w-4 h-4 text-primary" />
            {t('ac_add_account_title', lang) || 'Tạo tài khoản con mới'}
          </h3>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">{t('th_account_name', lang) || 'Tên tài khoản'}</span></label>
                <input type="text" required disabled={isCreating} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. user_dev" className="input input-bordered input-sm text-xs w-full" />
              </div>
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">{t('lbl_tier', lang)}</span></label>
                <select disabled={isCreating} value={newTier} onChange={(e) => setNewTier(e.target.value)} className="select select-bordered select-sm text-xs w-full">
                  <option value="free">Free</option>
                  <option value="premium">Premium</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Giới hạn (RPM / TPM / RPD)</span></label>
                <div className="flex gap-2">
                  <input type="number" disabled={isCreating} value={newRpm} onChange={(e) => setNewRpm(e.target.value)} placeholder="RPM" className="input input-bordered input-sm text-xs w-full" />
                  <input type="number" disabled={isCreating} value={newTpm} onChange={(e) => setNewTpm(e.target.value)} placeholder="TPM" className="input input-bordered input-sm text-xs w-full" />
                  <input type="number" disabled={isCreating} value={newRpd} onChange={(e) => setNewRpd(e.target.value)} placeholder="RPD" className="input input-bordered input-sm text-xs w-full" />
                </div>
              </div>
            </div>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 items-end pt-2 border-t border-base-content/5">
              <div className="form-control flex flex-row items-center gap-2 h-9">
                <input 
                  type="checkbox" 
                  checked={newWebSearchEnabled} 
                  onChange={(e) => setNewWebSearchEnabled(e.target.checked)} 
                  disabled={isCreating}
                  className="checkbox checkbox-primary checkbox-xs"
                  id="new-web-search-enabled-checkbox"
                />
                <label htmlFor="new-web-search-enabled-checkbox" className="label cursor-pointer py-0">
                  <span className="label-text font-bold text-xs uppercase text-base-content/70 select-none">Bật tìm kiếm Web</span>
                </label>
              </div>
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Công cụ tìm kiếm mặc định</span></label>
                <select disabled={isCreating} value={newSearchEngine} onChange={(e) => setNewSearchEngine(e.target.value)} className="select select-bordered select-sm text-xs w-full">
                  <option value="auto">Tự động (Auto)</option>
                  <option value="google_grounding">Google Grounding</option>
                  <option value="duckduckgo">DuckDuckGo</option>
                  <option value="disabled">Tắt tìm kiếm (Disabled)</option>
                </select>
              </div>
              <div className="flex gap-2 justify-end">
                <button type="button" disabled={isCreating} onClick={() => setShowCreateForm(false)} className="btn btn-ghost btn-sm font-bold w-24">Hủy</button>
                <button type="submit" disabled={isCreating} className="btn btn-primary btn-sm font-bold w-24">
                  {isCreating ? <span className="loading loading-spinner loading-xs"></span> : t('btn_add', lang)}
                </button>
              </div>
            </div>
          </form>
          {createMsg.text && (
            <div className={`text-xs font-semibold mt-3 p-3 rounded-lg border ${
              createMsg.type === 'success' ? 'bg-success/10 text-success border-success/20' :
              createMsg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-info/10 text-info border-info/20'
            }`}>{createMsg.text}</div>
          )}
        </div>
      )}

      {/* Stats Bar — 4 cards horizontal */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 animate-fade-in-up cascade-1">
        {[
          { label: t('ac_card_total', lang) || 'Tổng tài khoản', value: totalAccs, color: 'bg-primary', badge: 'text-primary', border: 'border-primary/20' },
          { label: t('ac_card_free', lang) || 'Free', value: freeAccs, color: 'bg-success', badge: 'text-success', border: 'border-success/20' },
          { label: t('ac_card_premium', lang) || 'Premium', value: premAccs, color: 'bg-warning', badge: 'text-warning', border: 'border-warning/20' },
          { label: t('ac_card_admin', lang) || 'Admin', value: adminAccs, color: 'bg-error', badge: 'text-error', border: 'border-error/20' },
        ].map(({ label, value, color, badge, border }) => (
          <div key={label} className={`card glass-card p-4 rounded-xl border ${border}`}>
            <div className="flex items-center gap-2 mb-2">
              <span className={`w-2 h-2 rounded-full shrink-0 ${color}`}></span>
              <span className="text-[10px] font-bold text-base-content/55 uppercase tracking-wider leading-none truncate">{label}</span>
            </div>
            <div className={`text-2xl font-black leading-none ${badge}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Filter & Search bar */}
      <div className="flex flex-col sm:flex-row gap-3 items-center justify-between animate-fade-in-up cascade-2">
        <div className="relative flex items-center w-full sm:max-w-xs">
          <Search className="absolute left-3 w-4 h-4 text-base-content/50" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('placeholder_search_accounts', lang) || 'Tìm theo tên hoặc ID tài khoản...'}
            className={`input input-bordered input-sm w-full pl-10 text-xs transition-all duration-300 ${
              isGeminiSearch ? 'bg-gradient-to-r from-amber-500 to-purple-600 text-white font-extrabold shadow-lg shadow-purple-500/25 border-none' :
              isHackerSearch ? 'matrix-text' : ''
            }`}
          />
        </div>
        <div className="flex gap-3 w-full sm:w-auto justify-end">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-bold text-base-content/60">{t('lbl_tier', lang)}</span>
            <select value={filterTier} onChange={(e) => setFilterTier(e.target.value)} className="select select-bordered select-sm text-xs min-w-[100px]">
              <option value="all">{t('opt_all', lang)}</option>
              <option value="admin">{t('opt_admin', lang)}</option>
              <option value="premium">{t('opt_premium', lang)}</option>
              <option value="free">{t('opt_free', lang)}</option>
            </select>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="font-bold text-base-content/60">{t('lbl_status', lang)}</span>
            <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} className="select select-bordered select-sm text-xs min-w-[100px]">
              <option value="all">{t('opt_all', lang)}</option>
              <option value="active">{t('opt_active', lang) || 'Hoạt động'}</option>
              <option value="disabled">{t('opt_disabled', lang)}</option>
            </select>
          </div>
        </div>
      </div>

      {/* Accounts Table — full width */}
      <div className="card glass-card rounded-2xl overflow-hidden text-left border border-base-content/5 animate-fade-in-up cascade-3">
        <div className="p-5 border-b border-base-content/5 flex justify-between items-center bg-base-200/10">
          <h3 className="font-extrabold text-sm">{t('ac_list_title', lang) || 'Danh sách tài khoản con'}</h3>
          <span className="text-xs text-base-content/50 font-bold">{filteredAccounts.length} / {accounts.length} {t('accounts_count', lang) || 'tài khoản'}</span>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table table-zebra table-fixed w-full text-xs">
            <thead>
              <tr className="border-b border-base-content/5 text-base-content/60 bg-base-200/35 select-none">
                <th onClick={() => handleSort('name')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[16%] whitespace-nowrap">{t('th_account_name', lang) || 'Tên tài khoản'}{sortBy === 'name' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('auth_key')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[24%] whitespace-nowrap">{t('th_key_code', lang)}{sortBy === 'auth_key' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('tier')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[9%] whitespace-nowrap">{t('th_tier', lang)}{sortBy === 'tier' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('status')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[10%] whitespace-nowrap">{t('th_status', lang)}{sortBy === 'status' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('rpm')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[7%] whitespace-nowrap">RPM{sortBy === 'rpm' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('tpm')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[8%] whitespace-nowrap">TPM{sortBy === 'tpm' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('rpd')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[8%] whitespace-nowrap">RPD{sortBy === 'rpd' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('created')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[10%] whitespace-nowrap">{t('th_created_at', lang) || 'Ngày tạo'}{sortBy === 'created' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th className="font-bold w-[8%] whitespace-nowrap">Hành động</th>
              </tr>
            </thead>
            <tbody>
              {sortedAccounts.length > 0 ? (
                sortedAccounts.map((a, i) => (
                  <tr key={i} className="border-b border-base-content/5 hover:bg-base-200/50">
                    <td><span className="font-bold text-base-content/90 truncate block" title={a.name}>{a.name}</span></td>
                    <td className="max-w-0">
                      {a.auth_key ? (
                        <div className="flex items-center gap-1.5">
                          <code className="font-mono text-primary text-[10px] block overflow-hidden text-ellipsis whitespace-nowrap" title={a.auth_key}>{a.auth_key}</code>
                          <button onClick={() => handleCopyKey(a.auth_key)} className="btn btn-ghost btn-xs btn-square text-base-content/60 hover:text-primary shrink-0" title="Copy Auth Key">
                            <Copy className="w-3 h-3" />
                          </button>
                        </div>
                      ) : (
                        <span className="text-base-content/40">— (Hidden)</span>
                      )}
                    </td>
                    <td>
                      <span className={`badge badge-xs text-[9px] font-extrabold uppercase ${
                        a.tier === 'admin' ? 'badge-primary' : a.tier === 'premium' ? 'badge-accent' : 'badge-ghost border-base-content/15'
                      }`}>{a.tier}</span>
                    </td>
                    <td>
                      <span className={`badge badge-xs font-bold uppercase ${
                        a.enabled ? 'badge-success/15 text-success border border-success/30' : 'badge-ghost border-base-content/15 text-base-content/50'
                      }`}>{a.enabled ? t('opt_active', lang) || 'Active' : t('opt_disabled', lang) || 'Disabled'}</span>
                    </td>
                    <td>{(a.rpm || 0).toLocaleString()}</td>
                    <td>{fmt(a.tpm || 0)}</td>
                    <td>{(a.rpd || 0).toLocaleString()}</td>
                    <td className="text-base-content/50 font-medium whitespace-nowrap">{fmtD(a.created_at)}</td>
                    <td>
                      <div className="flex items-center gap-0.5">
                        <button onClick={() => handleToggleStatus(a.name, a.enabled)}
                          className={`btn btn-ghost btn-xs btn-square ${a.enabled ? 'text-error hover:bg-error/15' : 'text-success hover:bg-success/15'}`}
                          title={a.enabled ? 'Disable Account' : 'Enable Account'}>
                          {a.enabled ? <ShieldAlert className="w-3.5 h-3.5" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                        </button>
                        <button onClick={() => { setEditingAccount(a); setIsEditOpen(true); }}
                          className="btn btn-ghost btn-xs btn-square text-primary hover:bg-primary/15" title="Edit Limits">
                          <Edit className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleRotateKey(a.name)}
                          className="btn btn-ghost btn-xs btn-square text-warning hover:bg-warning/15" title="Rotate Auth Key">
                          <RefreshCw className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleDeleteAccount(a.name)}
                          className="btn btn-ghost btn-xs btn-square text-error hover:bg-error/15" title="Delete Account">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="9" className="text-center py-8 text-base-content/40 font-medium">
                    {t('no_accounts_found', lang) || 'Không tìm thấy tài khoản con nào'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Edit Account Modal */}
      <EditAccountModal
        account={editingAccount}
        isOpen={isEditOpen}
        onClose={() => { setIsEditOpen(false); setEditingAccount(null); }}
        onSaveSuccess={refreshTab}
      />
    </div>
  );
}

