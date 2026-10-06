import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { api } from '../utils/api';
import Loading from '../components/Loading';
import { InvitePanel } from '../components/TokenTable';
import AccountDetailPanel from '../components/AccountDetailPanel';
import { TIER_BADGE } from '../utils/tier';
import { Search, Plus, Ticket, Copy, X, ArrowDownAZ, ArrowUpAZ, KeySquare, Users } from 'lucide-react';

const TIER_RANK = { admin: 0, premium: 1, free: 2 };

const SORTS = [
  { id: 'name', label: 'Tên' },
  { id: 'tier', label: 'Tier' },
  { id: 'rpm', label: 'RPM' },
  { id: 'tpm', label: 'TPM' },
  { id: 'rpd', label: 'RPD' },
  { id: 'created', label: 'Ngày tạo' },
];

// Mirrors config.TIER_CAPS. Duplicated rather than fetched so the form can warn
// before the round trip; the server clamps regardless, so this is a heads-up,
// never the enforcement.
const TIER_CAPS = {
  free: { rpm: 30, tpm: 200000, rpd: 1000 },
  premium: { rpm: 120, tpm: 800000, rpd: 5000 },
  admin: { rpm: 300, tpm: 6000000, rpd: 20000 },
};

export default function AccountsTab() {
  const { tabData, token, lang, refreshTab } = useApp();
  const accounts = tabData.ac || [];

  const [search, setSearch] = useState('');
  const [filterTier, setFilterTier] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');
  const [sortBy, setSortBy] = useState('name');
  const [sortOrder, setSortOrder] = useState('asc');
  const [selectedName, setSelectedName] = useState(null);

  const [showCreate, setShowCreate] = useState(false);
  const [showInvite, setShowInvite] = useState(false);

  // One banner for outcomes, one for secrets that must not vanish on a timer.
  const [notice, setNotice] = useState(null);
  const [secret, setSecret] = useState(null);
  const noticeTimer = useRef(null);
  const detailRef = useRef(null);

  const notify = (text, type = 'success') => {
    clearTimeout(noticeTimer.current);
    setNotice({ text, type });
    noticeTimer.current = setTimeout(() => setNotice(null), 5000);
  };
  useEffect(() => () => clearTimeout(noticeTimer.current), []);

  // Create form
  const [newName, setNewName] = useState('');
  const [newTier, setNewTier] = useState('free');
  const [newRpm, setNewRpm] = useState('');
  const [newTpm, setNewTpm] = useState('');
  const [newRpd, setNewRpd] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  const tierCapped = useMemo(() => {
    const caps = TIER_CAPS[newTier];
    if (!caps) return false;
    const over = (val, cap) => val !== '' && Number(val) > cap;
    return over(newRpm, caps.rpm) || over(newTpm, caps.tpm) || over(newRpd, caps.rpd);
  }, [newTier, newRpm, newTpm, newRpd]);

  // Easter eggs kept from the old tab.
  const needle = search.toLowerCase().trim();
  const isGeminiSearch = needle === 'gemini';
  const isHackerSearch = ['admin', 'root', 'hacker'].includes(needle);
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

  const sortedAccounts = useMemo(() => {
    const filtered = accounts.filter(a => {
      const matchesSearch = !needle
        || a.name.toLowerCase().includes(needle)
        || (a.account_id && a.account_id.toLowerCase().includes(needle));
      const matchesTier = filterTier === 'all' || a.tier === filterTier;
      const matchesStatus = filterStatus === 'all'
        || (filterStatus === 'active' && a.enabled)
        || (filterStatus === 'disabled' && !a.enabled);
      return matchesSearch && matchesTier && matchesStatus;
    });

    const value = (a) => {
      if (sortBy === 'tier') return TIER_RANK[a.tier] ?? 9;
      if (sortBy === 'created') return a.created_at || 0;
      if (sortBy === 'name') return a.name.toLowerCase();
      return a[sortBy] || 0;
    };
    const dir = sortOrder === 'asc' ? 1 : -1;
    return filtered.sort((a, b) => {
      const va = value(a); const vb = value(b);
      return (typeof va === 'string' ? va.localeCompare(vb) : va - vb) * dir;
    });
  }, [accounts, needle, filterTier, filterStatus, sortBy, sortOrder]);

  // Selection survives refresh polls; falls back to the first visible row when
  // the selected account is deleted or filtered out.
  const selected = accounts.find(a => a.name === selectedName)
    && sortedAccounts.find(a => a.name === selectedName)
    || sortedAccounts[0]
    || null;

  if (!tabData.ac) {
    return <Loading message={t('loading', lang)} />;
  }

  const count = (tier) => accounts.filter(a => a.tier === tier).length;
  const statChips = [
    { id: 'all', label: 'Tất cả', value: accounts.length },
    { id: 'admin', label: 'Admin', value: count('admin') },
    { id: 'premium', label: 'Premium', value: count('premium') },
    { id: 'free', label: 'Free', value: count('free') },
  ];

  const pick = (name) => {
    setSelectedName(name);
    // Stacked layout on small screens: bring the detail into view.
    if (window.matchMedia('(max-width: 1023px)').matches) {
      setTimeout(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
    }
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setIsCreating(true);
    const body = { name: newName.trim(), tier: newTier };
    if (newPassword) body.password = newPassword;
    if (newRpm.trim() !== '') body.rpm = parseInt(newRpm, 10);
    if (newTpm.trim() !== '') body.tpm = parseInt(newTpm, 10);
    if (newRpd.trim() !== '') body.rpd = parseInt(newRpd, 10);
    try {
      const res = await api('/dashboard/admin/accounts/create', { method: 'POST', body: JSON.stringify(body) }, token);
      setNewName(''); setNewRpm(''); setNewTpm(''); setNewRpd(''); setNewTier('free'); setNewPassword('');
      setShowCreate(false);
      refreshTab();
      if (res?.account) {
        setSelectedName(res.account.name);
        if (res.account.auth_key) setSecret({ title: `Master key của ${res.account.name}`, value: res.account.auth_key });
      }
      notify(t('msg_saved', lang) || 'Đã tạo tài khoản');
    } catch (err) {
      notify('Lỗi: ' + err.message, 'error');
    } finally {
      setIsCreating(false);
    }
  };

  const copySecret = async () => {
    try { await navigator.clipboard.writeText(secret.value); notify('Đã copy.'); } catch { notify('Trình duyệt chặn clipboard.', 'error'); }
  };

  const labelCls = 'label-text text-[11px] font-bold text-base-content/60 uppercase';

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="text-left">
          <h1 className="text-2xl font-black tracking-tight flex items-center gap-2">
            <Users className="w-6 h-6 text-primary" />
            {t('ac_title', lang) || 'Quản lý tài khoản'}
          </h1>
          <p className="text-xs text-base-content/60 mt-1">
            Chọn một tài khoản bên trái để xem và chỉnh hạn mức, token, mật khẩu.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowInvite(v => !v)}
            className={`btn btn-sm gap-2 font-bold ${showInvite ? 'btn-secondary' : 'btn-outline'}`}
          >
            <Ticket className="w-4 h-4" /> Mã mời
          </button>
          <button
            onClick={() => setShowCreate(v => !v)}
            className="btn btn-primary btn-sm gap-2 font-bold shadow-lg shadow-primary/25"
          >
            <Plus className="w-4 h-4" /> {t('btn_add', lang)}
          </button>
        </div>
      </div>

      {notice && (
        <div className={`text-xs font-semibold p-3 rounded-lg border flex items-center justify-between gap-3 ${
          notice.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-success/10 text-success border-success/20'
        }`}>
          <span>{notice.text}</span>
          <button onClick={() => setNotice(null)} className="opacity-60 hover:opacity-100"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {secret && (
        <div className="rounded-xl border border-warning/40 bg-warning/10 p-4 text-left animate-fade-in-up">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-xs font-extrabold text-warning">{secret.title}</div>
              <div className="text-[11px] text-base-content/60 mt-0.5">Chỉ hiện một lần — copy và cất đi trước khi đóng.</div>
            </div>
            <button onClick={() => setSecret(null)} className="btn btn-ghost btn-xs btn-square"><X className="w-4 h-4" /></button>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <code className="flex-1 min-w-0 truncate text-xs font-mono bg-base-300/60 border border-base-content/10 rounded-lg px-3 py-2">
              {secret.value}
            </code>
            <button onClick={copySecret} className="btn btn-sm btn-warning gap-1.5 font-bold shrink-0">
              <Copy className="w-3.5 h-3.5" /> Copy
            </button>
          </div>
        </div>
      )}

      {showInvite && (
        <div className="animate-fade-in-up"><InvitePanel token={token} /></div>
      )}

      {showCreate && (
        <form onSubmit={handleCreate} className="card glass-card p-5 rounded-2xl text-left border border-primary/20 animate-fade-in-up space-y-4">
          <h3 className="font-extrabold text-sm flex items-center gap-2">
            <Plus className="w-4 h-4 text-primary" /> {t('ac_add_account_title', lang) || 'Tạo tài khoản người dùng mới'}
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <label className="form-control">
              <span className={labelCls}>{t('th_account_name', lang) || 'Tên tài khoản'}</span>
              <input type="text" required disabled={isCreating} value={newName} onChange={e => setNewName(e.target.value)} placeholder="vd: user_dev" className="input input-bordered input-sm text-xs w-full mt-1" />
            </label>
            <label className="form-control">
              <span className={labelCls}>{t('lbl_tier', lang)}</span>
              <select disabled={isCreating} value={newTier} onChange={e => setNewTier(e.target.value)} className="select select-bordered select-sm text-xs w-full mt-1">
                <option value="free">Free</option>
                <option value="premium">Premium</option>
                <option value="admin">Admin</option>
              </select>
              <span className="text-[10px] text-base-content/45 mt-1 font-mono">
                trần: {TIER_CAPS[newTier].rpm} RPM · {fmt(TIER_CAPS[newTier].tpm)} TPM · {fmt(TIER_CAPS[newTier].rpd)} RPD
              </span>
            </label>
            <label className="form-control">
              <span className={labelCls}>Mật khẩu đăng nhập</span>
              <input type="text" disabled={isCreating} value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="trống = 1234, bắt đổi khi đăng nhập" className="input input-bordered input-sm text-xs w-full mt-1" />
            </label>
            <div className="form-control">
              <span className={labelCls}>Giới hạn RPM / TPM / RPD</span>
              <div className="flex gap-2 mt-1">
                <input type="number" disabled={isCreating} value={newRpm} onChange={e => setNewRpm(e.target.value)} placeholder="RPM" className="input input-bordered input-sm text-xs w-full" />
                <input type="number" disabled={isCreating} value={newTpm} onChange={e => setNewTpm(e.target.value)} placeholder="TPM" className="input input-bordered input-sm text-xs w-full" />
                <input type="number" disabled={isCreating} value={newRpd} onChange={e => setNewRpd(e.target.value)} placeholder="RPD" className="input input-bordered input-sm text-xs w-full" />
              </div>
              {tierCapped && (
                <span className="text-[10px] text-warning font-bold mt-1">vượt trần {newTier} — sẽ bị chặn về tối đa của tier</span>
              )}
            </div>
          </div>
          <div className="flex gap-2 justify-end pt-3 border-t border-base-content/5">
            <button type="button" disabled={isCreating} onClick={() => setShowCreate(false)} className="btn btn-ghost btn-sm font-bold">Hủy</button>
            <button type="submit" disabled={isCreating} className="btn btn-primary btn-sm font-bold min-w-24">
              {isCreating ? <span className="loading loading-spinner loading-xs" /> : t('btn_add', lang)}
            </button>
          </div>
        </form>
      )}

      {/* Master – detail */}
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(300px,360px)_minmax(0,1fr)] gap-4 items-start">
        {/* List */}
        <aside className="card glass-card rounded-2xl border border-base-content/5 text-left overflow-hidden lg:sticky lg:top-0">
          <div className="p-3 space-y-3 border-b border-base-content/5 bg-base-200/10">
            <div className="flex flex-wrap gap-1.5">
              {statChips.map(c => (
                <button
                  key={c.id}
                  onClick={() => setFilterTier(c.id)}
                  className={`btn btn-xs rounded-full gap-1.5 font-bold normal-case ${
                    filterTier === c.id ? 'btn-primary' : 'btn-ghost border border-base-content/15 text-base-content/70'
                  }`}
                >
                  {c.label}
                  <span className="opacity-70">{c.value}</span>
                </button>
              ))}
            </div>

            <div className="relative flex items-center">
              <Search className="absolute left-3 w-4 h-4 text-base-content/50" />
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder={t('placeholder_search_accounts', lang) || 'Tìm theo tên hoặc ID...'}
                className={`input input-bordered input-sm w-full pl-9 text-xs transition-all duration-300 ${
                  isGeminiSearch ? 'bg-gradient-to-r from-amber-500 to-purple-600 text-white font-extrabold border-none' :
                  isHackerSearch ? 'matrix-text' : ''
                }`}
              />
            </div>

            <div className="flex gap-2">
              <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className="select select-bordered select-xs text-[11px] flex-1">
                <option value="all">Mọi trạng thái</option>
                <option value="active">Hoạt động</option>
                <option value="disabled">Đã khoá</option>
              </select>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)} className="select select-bordered select-xs text-[11px] flex-1">
                {SORTS.map(s => <option key={s.id} value={s.id}>Xếp theo {s.label}</option>)}
              </select>
              <button
                onClick={() => setSortOrder(o => (o === 'asc' ? 'desc' : 'asc'))}
                className="btn btn-xs btn-ghost border border-base-content/15 btn-square"
                title={sortOrder === 'asc' ? 'Tăng dần' : 'Giảm dần'}
              >
                {sortOrder === 'asc' ? <ArrowDownAZ className="w-3.5 h-3.5" /> : <ArrowUpAZ className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>

          <ul className="max-h-[70vh] overflow-y-auto divide-y divide-base-content/5">
            {sortedAccounts.length === 0 && (
              <li className="text-center py-10 text-xs text-base-content/40 font-medium">
                {t('no_accounts_found', lang) || 'Không tìm thấy tài khoản nào'}
              </li>
            )}
            {sortedAccounts.map(a => {
              const active = selected?.name === a.name;
              return (
                <li key={a.account_id || a.name}>
                  <button
                    onClick={() => pick(a.name)}
                    className={`w-full text-left px-3 py-2.5 flex items-center gap-3 transition-colors border-l-2 ${
                      active ? 'bg-primary/10 border-primary' : 'border-transparent hover:bg-base-200/40'
                    }`}
                  >
                    <div className="relative shrink-0">
                      <div className="w-9 h-9 rounded-full bg-primary/10 text-primary text-xs font-black flex items-center justify-center border border-primary/15 uppercase">
                        {a.name.substring(0, 2)}
                      </div>
                      <span className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-base-100 ${a.enabled ? 'bg-success' : 'bg-base-content/30'}`} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-xs truncate">{a.name}</span>
                        {a.must_change_password && <KeySquare className="w-3 h-3 text-warning shrink-0" title="Đang yêu cầu đổi mật khẩu" />}
                      </div>
                      <div className="text-[10px] text-base-content/45 font-mono truncate">
                        {a.rpm || 0} rpm · {fmt(a.tpm || 0)} tpm · {a.rpd || 0} rpd
                      </div>
                    </div>
                    <span className={`badge badge-xs text-[9px] font-extrabold uppercase shrink-0 ${TIER_BADGE[a.tier] || TIER_BADGE.free}`}>
                      {a.tier}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="px-3 py-2 border-t border-base-content/5 text-[10px] font-bold text-base-content/45">
            {sortedAccounts.length} / {accounts.length} {t('accounts_count', lang) || 'tài khoản'}
          </div>
        </aside>

        {/* Detail */}
        <div ref={detailRef} className="min-w-0 scroll-mt-4">
          {selected ? (
            <AccountDetailPanel
              key={selected.name}
              account={selected}
              token={token}
              refreshTab={refreshTab}
              notify={notify}
              onSecret={setSecret}
              onDeleted={() => setSelectedName(null)}
            />
          ) : (
            <div className="card glass-card rounded-2xl p-10 text-center text-xs text-base-content/45 border border-base-content/5">
              Không có tài khoản nào khớp bộ lọc.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
