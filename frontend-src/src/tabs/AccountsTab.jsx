import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt, fmtD } from '../utils/format';
import { api } from '../utils/api';
import EditAccountModal from '../components/EditAccountModal';
import Loading from '../components/Loading';
import TokenTable, { InvitePanel } from '../components/TokenTable';
import PasswordReveal from '../components/PasswordReveal';
import { Search, Plus, Trash2, ShieldCheck, ShieldAlert, KeyRound, KeySquare, Edit, Copy, RefreshCw } from 'lucide-react';

export default function AccountsTab() {
  const { tabData, token, lang, refreshTab } = useApp();
  const accounts = tabData.ac || [];

  const [search, setSearch] = useState('');
  const [filterTier, setFilterTier] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');

  const [sortBy, setSortBy] = useState('name');
  const [sortOrder, setSortOrder] = useState('asc');

  // Which account's must-change toggle is mid-flight. One name, not a set:
  // two toggles cannot be in flight at once from a single row of buttons, and a
  // boolean keeps the per-row disabled state honest.
  const [mustChangeBusy, setMustChangeBusy] = useState(null);

  // New account form state
  const [newName, setNewName] = useState('');
  const [newTier, setNewTier] = useState('free');
  const [newRpm, setNewRpm] = useState('');
  const [newTpm, setNewTpm] = useState('');
  const [newRpd, setNewRpd] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [createMsg, setCreateMsg] = useState({ text: '', type: '' });

  // Mirrors config.TIER_CAPS. Duplicated rather than fetched so the form can
  // warn before the round trip; the server clamps regardless, so this is only
  // a heads-up, never the enforcement.
  const TIER_CAPS = {
    free: { rpm: 30, tpm: 200000, rpd: 1000 },
    premium: { rpm: 120, tpm: 800000, rpd: 5000 },
    admin: { rpm: 300, tpm: 6000000, rpd: 20000 },
  };

  const tierCapped = useMemo(() => {
    const caps = TIER_CAPS[newTier];
    if (!caps) return false;
    const over = (val, cap) => val !== '' && Number(val) > cap;
    return over(newRpm, caps.rpm) || over(newTpm, caps.tpm) || over(newRpd, caps.rpd);
  }, [newTier, newRpm, newTpm, newRpd]);

  // Edit account modal state
  const [editingAccount, setEditingAccount] = useState(null);
  const [isEditOpen, setIsEditOpen] = useState(false);

  // ── auth tokens per account ──
  // An account no longer has one auth_key: it owns zero or more structured
  // tokens, each with its own quota. Tokens are fetched per account on demand
  // rather than bundled into /dashboard/accounts, which would multiply the
  // payload by the number of accounts.
  const [tokenAccount, setTokenAccount] = useState(null);
  const [tokens, setTokens] = useState([]);
  const [tokensLoading, setTokensLoading] = useState(false);
  const [tokenCounts, setTokenCounts] = useState({});

  const loadTokens = useCallback(async (name) => {
    setTokensLoading(true);
    try {
      const res = await api('/dashboard/admin/accounts/keys', {
        method: 'POST',
        body: JSON.stringify({ name })
      }, token);
      setTokens(res?.keys || []);
      setTokenCounts((c) => ({ ...c, [name]: (res?.keys || []).length }));
    } catch (e) {
      setTokens([]);
      alert('Error load tokens: ' + e.message);
    } finally {
      setTokensLoading(false);
    }
  }, [token]);

  const openTokens = (account) => {
    setTokenAccount(account);
    loadTokens(account.name);
  };

  const closeTokens = () => { setTokenAccount(null); setTokens([]); };

  const issueToken = async (name, payload) => {
    const res = await api('/dashboard/admin/accounts/keys/issue', {
      method: 'POST',
      body: JSON.stringify({ name, ...payload })
    }, token);
    await loadTokens(name);
    refreshTab();
    if (res?.token) {
      window.prompt(
        `Token mới cho ${name} — copy ngay, chỉ hiện một lần:`,
        res.token
      );
    }
  };

  const updateToken = async (name, keyId, payload) => {
    await api('/dashboard/admin/accounts/keys/update', {
      method: 'POST',
      body: JSON.stringify({ key_id: keyId, ...payload })
    }, token);
    await loadTokens(name);
  };

  const revokeToken = async (name, keyId) => {
    await api('/dashboard/admin/accounts/keys/revoke', {
      method: 'POST',
      body: JSON.stringify({ key_id: keyId })
    }, token);
    await loadTokens(name);
    refreshTab();
  };

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
    };
    if (newPassword) body.password = newPassword;
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

      setNewPassword('');
      
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

  // Mark an account as owing a password change, or clear the mark. Writes a
  // flag only — the stored password is untouched, so this is not a reset and the
  // admin does not need to know the current password to press it.
  const handleToggleMustChange = async (account) => {
    const required = !account.must_change_password;
    setMustChangeBusy(account.name);
    try {
      await api('/dashboard/admin/accounts/require-password-change', {
        method: 'POST',
        body: JSON.stringify({ name: account.name, required: required })
      }, token);
      refreshTab();
    } catch (err) {
      alert('Error: ' + err.message);
    } finally {
      setMustChangeBusy(null);
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

  const handleCopyMasterKey = (key) => {
    if (!key) {
      alert('Tài khoản này không có master key.');
      return;
    }
    navigator.clipboard.writeText(key);
    alert('Đã copy master key vào clipboard!');
  };

  const handleRotateKey = async (name) => {
    if (!window.confirm(`Bạn có chắc muốn cấp mới Master Key cho account "${name}"? Master key cũ sẽ bị vô hiệu hóa ngay lập tức.`)) return;
    try {
      const res = await api('/dashboard/admin/accounts/rotate-key', {
        method: 'POST',
        body: JSON.stringify({ name })
      }, token);
      refreshTab();
      if (res.account && res.account.auth_key) {
        alert(`Đã cấp mới Master Key thành công!\n\nKhóa mới: ${res.account.auth_key}\n\n(Hãy copy và lưu lại khóa này)`);
      }
    } catch (e) {
      alert('Rotate key error: ' + e.message);
    }
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
            {t('ac_add_account_title', lang) || 'Tạo tài khoản người dùng mới'}
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
                {TIER_CAPS[newTier] && (
                  <span className="text-[10px] text-base-content/45 mt-1 font-mono">
                    trần: {TIER_CAPS[newTier].rpm} RPM · {fmt(TIER_CAPS[newTier].tpm)} TPM · {fmt(TIER_CAPS[newTier].rpd)} RPD
                  </span>
                )}
              </div>
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Mật khẩu đăng nhập</span></label>
                <input
                  type="text"
                  disabled={isCreating}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="để trống = 1234, user phải đổi khi đăng nhập"
                  className="input input-bordered input-sm text-xs w-full"
                />
              </div>
              <div className="form-control">
                <label className="label py-1"><span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Giới hạn (RPM / TPM / RPD)</span></label>
                <div className="flex gap-2">
                  <input type="number" disabled={isCreating} value={newRpm} onChange={(e) => setNewRpm(e.target.value)} placeholder="RPM" className="input input-bordered input-sm text-xs w-full" />
                  <input type="number" disabled={isCreating} value={newTpm} onChange={(e) => setNewTpm(e.target.value)} placeholder="TPM" className="input input-bordered input-sm text-xs w-full" />
                  <input type="number" disabled={isCreating} value={newRpd} onChange={(e) => setNewRpd(e.target.value)} placeholder="RPD" className="input input-bordered input-sm text-xs w-full" />
                </div>
                {tierCapped && (
                  <span className="text-[10px] text-warning font-bold mt-1">
                    vượt trần {newTier} — sẽ bị chặn về giá trị tối đa của tier
                  </span>
                )}
              </div>
            </div>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 items-end pt-2 border-t border-base-content/5">
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

      {/* Invite codes — enrollment for self-registered users */}
      <div className="animate-fade-in-up cascade-1">
        <InvitePanel token={token} />
      </div>

      {/* Accounts Table — full width */}
      <div className="card glass-card rounded-2xl overflow-hidden text-left border border-base-content/5 animate-fade-in-up cascade-3">
        <div className="p-5 border-b border-base-content/5 flex justify-between items-center bg-base-200/10">
          <h3 className="font-extrabold text-sm">{t('ac_list_title', lang) || 'Danh sách tài khoản người dùng'}</h3>
          <span className="text-xs text-base-content/50 font-bold">{filteredAccounts.length} / {accounts.length} {t('accounts_count', lang) || 'tài khoản'}</span>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table table-zebra table-fixed w-full text-xs">
            <thead>
              <tr className="border-b border-base-content/5 text-base-content/60 bg-base-200/35 select-none">
                <th onClick={() => handleSort('name')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[18%] whitespace-nowrap">{t('th_account_name', lang) || 'Tên tài khoản'}{sortBy === 'name' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th className="font-bold whitespace-nowrap w-[13%]">Auth tokens</th>
                <th onClick={() => handleSort('tier')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[8%] whitespace-nowrap">{t('th_tier', lang)}{sortBy === 'tier' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('status')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[10%] whitespace-nowrap">{t('th_status', lang)}{sortBy === 'status' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('rpm')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[7%] whitespace-nowrap">RPM{sortBy === 'rpm' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('tpm')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[8%] whitespace-nowrap">TPM{sortBy === 'tpm' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('rpd')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[8%] whitespace-nowrap">RPD{sortBy === 'rpd' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th onClick={() => handleSort('created')} className="font-bold cursor-pointer hover:bg-base-200/50 hover:text-base-content transition-all w-[10%] whitespace-nowrap">{t('th_created_at', lang) || 'Ngày tạo'}{sortBy === 'created' ? (sortOrder === 'asc' ? ' ▲' : ' ▼') : ''}</th>
                <th className="font-bold w-[8%] whitespace-nowrap">Mật khẩu</th>
                <th className="font-bold w-[8%] whitespace-nowrap">Hành động</th>
              </tr>
            </thead>
            <tbody>
              {sortedAccounts.length > 0 ? (
                sortedAccounts.map((a, i) => (
                  <tr key={i} className="border-b border-base-content/5 hover:bg-base-200/50">
                    <td>
                      <span className="font-bold text-base-content/90 truncate block" title={a.name}>{a.name}</span>
                      <span className="text-[10px] text-base-content/40 font-mono truncate block" title={a.account_id}>
                        {a.account_id}
                      </span>
                    </td>
                    <td>
                      <button
                        onClick={() => openTokens(a)}
                        className="btn btn-ghost btn-xs gap-1.5 font-bold text-primary hover:bg-primary/15 whitespace-nowrap"
                        title="Quản lý auth tokens"
                      >
                        <KeyRound className="w-3.5 h-3.5" />
                        {tokenCounts[a.name] !== undefined ? tokenCounts[a.name] : '—'}
                      </button>
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
                      <div className="flex flex-col gap-1 items-start">
                        <PasswordReveal token={token} accountName={a.name} />
                        <button
                          onClick={() => handleToggleMustChange(a)}
                          disabled={mustChangeBusy === a.name}
                          className={`btn btn-xs gap-1 font-bold normal-case ${
                            a.must_change_password
                              ? 'btn-warning'
                              : 'btn-ghost text-base-content/50'
                          }`}
                          title={
                            a.must_change_password
                              ? 'Đang yêu cầu đổi MK — bấm để bỏ yêu cầu'
                              : 'Yêu cầu tài khoản này đổi mật khẩu ở lần đăng nhập tới'
                          }
                        >
                          <KeySquare className="w-3 h-3" />
                          {a.must_change_password ? 'Cần đổi MK' : 'Yêu cầu đổi MK'}
                        </button>
                      </div>
                    </td>
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
                        <button onClick={() => openTokens(a)}
                          className="btn btn-ghost btn-xs btn-square text-warning hover:bg-warning/15" title="Quản lý auth tokens">
                          <KeyRound className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleCopyMasterKey(a.auth_key)}
                          className="btn btn-ghost btn-xs btn-square text-accent hover:bg-accent/15" title="Copy Master Key">
                          <Copy className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => handleRotateKey(a.name)}
                          className="btn btn-ghost btn-xs btn-square text-secondary hover:bg-secondary/15" title="Rotate Master Key">
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
                  <td colSpan="10" className="text-center py-8 text-base-content/40 font-medium">
                    {t('no_accounts_found', lang) || 'Không tìm thấy tài khoản người dùng nào'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Token manager for the selected account */}
      {tokenAccount && (
        <div className="card glass-card rounded-2xl border border-primary/25 p-5 animate-fade-in-up">
          <div className="flex items-center justify-between gap-3 mb-4 pb-3 border-b border-base-content/5">
            <div>
              <h3 className="font-extrabold text-sm flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-primary" />
                Auth tokens — {tokenAccount.name}
              </h3>
              <p className="text-[11px] text-base-content/55 mt-0.5">
                Mỗi token có hạn mức riêng. Admin có thể nới trần; user chỉ
                được siết chặt hơn hạn mức tài khoản.
              </p>
            </div>
            <button onClick={closeTokens}
                    className="btn btn-ghost btn-sm font-bold w-20">Đóng</button>
          </div>

          <TokenTable
            tokens={tokens}
            loading={tokensLoading}
            scope="admin"
            accountName={tokenAccount.name}
            onIssue={(payload) => issueToken(tokenAccount.name, payload)}
            onUpdate={(keyId, payload) => updateToken(tokenAccount.name, keyId, payload)}
            onRevoke={(keyId) => revokeToken(tokenAccount.name, keyId)}
          />
        </div>
      )}

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

