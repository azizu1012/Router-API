import { useState, useEffect, useMemo, useRef } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { api } from '../utils/api';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import Badge from '../components/Badge';
import Button from '../components/Button';
import Alert from '../components/Alert';
import { InvitePanel } from '../components/TokenTable';
import AccountDetailPanel from '../components/AccountDetailPanel';
import { Search, Plus, Ticket, Copy, ArrowDownAZ, ArrowUpAZ, KeySquare, X } from 'lucide-react';
import { copyText } from '../utils/clipboard';

const TIER_RANK = { admin: 0, premium: 1, free: 2 };
const TIER_VARIANT = { admin: 'primary', premium: 'info', free: 'ghost' };

const SORTS = [
  { id: 'name', key: 'ac_sort_name' },
  { id: 'tier', key: 'ac_sort_tier' },
  { id: 'rpm', key: 'ac_sort_rpm' },
  { id: 'tpm', key: 'ac_sort_tpm' },
  { id: 'rpd', key: 'ac_sort_rpd' },
  { id: 'created', key: 'ac_sort_created' },
];

// Mirrors config.TIER_CAPS. Duplicated rather than fetched so the form can warn
// before the round trip; the server clamps regardless, so this is a heads-up,
// never the enforcement.
const TIER_CAPS = {
  free: { rpm: 30, tpm: 200000, rpd: 1000 },
  premium: { rpm: 120, tpm: 800000, rpd: 5000 },
  admin: { rpm: 300, tpm: 6000000, rpd: 20000 },
};

/* Master–detail. The list is the spine of this screen: it never scrolls away
   on desktop (sticky), and it keeps its own sort/filter state so a poll every
   1.5 s does not reset what the admin was looking at. The detail panel is
   keyed by account name so switching rows remounts it with clean state. */
export default function AccountsTab() {
  const { tabData, token, lang, refreshTab } = useApp();
  const accounts = useMemo(() => tabData.ac || [], [tabData.ac]);

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
    { id: 'all', label: t('ac_all', lang), value: accounts.length },
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
        if (res.account.auth_key) {
          setSecret({
            title: t('ac_master_key_of', lang).replace('{name}', res.account.name),
            value: res.account.auth_key,
          });
        }
      }
      notify(t('ac_created', lang));
    } catch (err) {
      notify(err.message, 'error');
    } finally {
      setIsCreating(false);
    }
  };

  const copySecret = async () => {
    if (await copyText(secret.value)) {
      notify(t('ac_copy_done', lang));
    } else {
      notify(t('ac_copy_blocked', lang), 'error');
    }
  };

  const cap = TIER_CAPS[newTier];

  return (
    <div className="space-y-5">
      <PageHeader
        title={t('ac_title', lang)}
        subtitle={t('ac_sub_detail', lang)}
        actions={
          <>
            <Button variant="outline" icon={Ticket}
                    onClick={() => setShowInvite(v => !v)}
                    aria-expanded={showInvite}>
              {t('ac_invite', lang)}
            </Button>
            <Button icon={Plus} onClick={() => setShowCreate(v => !v)} aria-expanded={showCreate}>
              {t('btn_add', lang)}
            </Button>
          </>
        }
      />

      {notice && <Alert type={notice.type} message={notice.text} onClose={() => setNotice(null)} />}

      {/* A minted credential is the one thing on this screen that must not
          disappear on a timer: it is shown until the admin dismisses it. */}
      {secret && (
        <div className="rounded-xl p-4 text-left"
             style={{
               background: 'color-mix(in srgb, var(--c-warn) 8%, var(--c-surface))',
               border: '1px solid color-mix(in srgb, var(--c-warn) 40%, transparent)',
             }}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{secret.title}</h3>
              <p className="text-[0.6875rem] mt-0.5" style={{ color: 'var(--c-ink-3)' }}>
                {t('ac_secret_once', lang)}
              </p>
            </div>
            <button onClick={() => setSecret(null)}
                    className="btn btn-ghost btn-xs btn-square shrink-0"
                    style={{ color: 'var(--c-ink-3)' }}
                    aria-label={t('btn_cancel', lang)}>
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <code className="flex-1 min-w-0 truncate text-xs font-mono px-3 py-2 rounded-lg select-all"
                  style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)', color: 'var(--c-ink)' }}>
              {secret.value}
            </code>
            <Button size="sm" onClick={copySecret} icon={Copy} className="shrink-0">
              {t('ac_copy', lang)}
            </Button>
          </div>
        </div>
      )}

      {showInvite && (
        <div className="animate-fade-in-up"><InvitePanel token={token} /></div>
      )}

      {showCreate && (
        <Card padding="lg" className="animate-fade-in-up">
          <h2 className="text-xs font-semibold mb-4" style={{ color: 'var(--c-ink)' }}>
            {t('ac_add_account_title', lang)}
          </h2>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <label className="block">
                <span className="label-micro block mb-1.5">{t('th_account_name', lang)}</span>
                <input type="text" required disabled={isCreating} value={newName}
                       onChange={e => setNewName(e.target.value)} placeholder="user_dev"
                       className="input input-sm text-xs w-full" autoComplete="off" />
              </label>
              <label className="block">
                <span className="label-micro block mb-1.5">{t('lbl_tier', lang)}</span>
                <select disabled={isCreating} value={newTier} onChange={e => setNewTier(e.target.value)}
                        className="select select-sm text-xs w-full">
                  <option value="free">Free</option>
                  <option value="premium">Premium</option>
                  <option value="admin">Admin</option>
                </select>
                <span className="block text-[0.6875rem] mt-1.5 font-mono tabular-nums"
                      style={{ color: 'var(--c-ink-3)' }}>
                  {t('ac_caps_hint', lang)
                    .replace('{rpm}', cap.rpm)
                    .replace('{tpm}', fmt(cap.tpm))
                    .replace('{rpd}', fmt(cap.rpd))}
                </span>
              </label>
              <label className="block">
                <span className="label-micro block mb-1.5">{t('ac_web_pw', lang)}</span>
                <input type="text" disabled={isCreating} value={newPassword}
                       onChange={e => setNewPassword(e.target.value)}
                       placeholder="••••" className="input input-sm text-xs w-full" autoComplete="new-password" />
                <span className="block text-[0.6875rem] mt-1.5 leading-snug" style={{ color: 'var(--c-ink-3)' }}>
                  {t('ac_pw_hint', lang)}
                </span>
              </label>
              <div>
                <span className="label-micro block mb-1.5">RPM / TPM / RPD</span>
                <div className="flex gap-2">
                  <input type="number" inputMode="numeric" disabled={isCreating} value={newRpm}
                         onChange={e => setNewRpm(e.target.value)} placeholder="RPM"
                         className="input input-sm text-xs w-full font-mono tabular-nums" />
                  <input type="number" inputMode="numeric" disabled={isCreating} value={newTpm}
                         onChange={e => setNewTpm(e.target.value)} placeholder="TPM"
                         className="input input-sm text-xs w-full font-mono tabular-nums" />
                  <input type="number" inputMode="numeric" disabled={isCreating} value={newRpd}
                         onChange={e => setNewRpd(e.target.value)} placeholder="RPD"
                         className="input input-sm text-xs w-full font-mono tabular-nums" />
                </div>
              </div>
            </div>
            {tierCapped && (
              <Alert type="warning" message={t('ac_capped_warn', lang).replace('{tier}', newTier)} />
            )}
            <div className="flex flex-wrap items-center justify-end gap-2 pt-3"
                 style={{ borderTop: '1px solid var(--c-line)' }}>
              <Button type="button" variant="ghost" onClick={() => setShowCreate(false)} disabled={isCreating}>
                {t('btn_cancel', lang)}
              </Button>
              <Button type="submit" loading={isCreating}>{t('btn_add', lang)}</Button>
            </div>
          </form>
        </Card>
      )}

      {/* Master – detail */}
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(300px,360px)_minmax(0,1fr)] gap-4 items-start">
        <aside className="glass-card rounded-xl text-left overflow-hidden lg:sticky lg:top-0 animate-fade-in-up">
          <div className="p-3 space-y-2.5" style={{ borderBottom: '1px solid var(--c-line)' }}>
            <div className="flex flex-wrap gap-1.5">
              {statChips.map(c => {
                const active = filterTier === c.id;
                return (
                  <button
                    key={c.id}
                    onClick={() => setFilterTier(c.id)}
                    aria-pressed={active}
                    className="inline-flex items-center gap-1.5 text-[0.6875rem] font-medium rounded-md px-2 py-1 transition-colors"
                    style={{
                      background: active ? 'var(--c-accent-soft)' : 'transparent',
                      border: `1px solid ${active ? 'color-mix(in srgb, var(--c-accent) 35%, transparent)' : 'var(--c-line)'}`,
                      color: active ? 'var(--c-accent)' : 'var(--c-ink-3)',
                    }}
                  >
                    {c.label}
                    <span className="tabular-nums opacity-70">{c.value}</span>
                  </button>
                );
              })}
            </div>

            <div className="relative flex items-center">
              <Search className="absolute left-2.5 w-3.5 h-3.5 pointer-events-none"
                      style={{ color: 'var(--c-ink-3)' }} />
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder={t('placeholder_search_accounts', lang)}
                className={`input input-sm w-full pl-8 text-xs ${
                  isGeminiSearch ? 'g-gradient' : isHackerSearch ? 'matrix-text' : ''
                }`}
                style={isGeminiSearch ? { background: 'linear-gradient(90deg, #c98500, #9085e9)', color: '#fff', fontWeight: 700 } : undefined}
                aria-label={t('placeholder_search_accounts', lang)}
              />
            </div>

            <div className="flex gap-2">
              <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
                      className="select select-xs text-[0.6875rem] flex-1"
                      aria-label={t('lbl_status', lang)}>
                <option value="all">{t('ac_status_all', lang)}</option>
                <option value="active">{t('ac_status_active', lang)}</option>
                <option value="disabled">{t('ac_status_disabled', lang)}</option>
              </select>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                      className="select select-xs text-[0.6875rem] flex-1"
                      aria-label={t('ac_sort_name', lang)}>
                {SORTS.map(s => (
                  <option key={s.id} value={s.id}>
                    {t('ac_sort_by', lang).replace('{k}', t(s.key, lang))}
                  </option>
                ))}
              </select>
              <button
                onClick={() => setSortOrder(o => (o === 'asc' ? 'desc' : 'asc'))}
                className="btn btn-ghost btn-xs btn-square shrink-0"
                style={{ border: '1px solid var(--c-line)', color: 'var(--c-ink-3)' }}
                title={sortOrder === 'asc' ? t('ac_sort_asc', lang) : t('ac_sort_desc', lang)}
                aria-label={sortOrder === 'asc' ? t('ac_sort_asc', lang) : t('ac_sort_desc', lang)}
              >
                {sortOrder === 'asc' ? <ArrowDownAZ className="w-3.5 h-3.5" /> : <ArrowUpAZ className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>

          <ul className="max-h-[70vh] overflow-y-auto">
            {sortedAccounts.length === 0 && (
              <li className="text-center py-10 px-4 text-xs" style={{ color: 'var(--c-ink-3)' }}>
                {t('no_accounts_found', lang)}
              </li>
            )}
            {sortedAccounts.map(a => {
              const active = selected?.name === a.name;
              return (
                <li key={a.account_id || a.name} style={{ borderTop: '1px solid var(--c-line)' }}>
                  <button
                    onClick={() => pick(a.name)}
                    aria-current={active ? 'true' : undefined}
                    className="w-full text-left px-3 py-2.5 flex items-center gap-3 transition-colors"
                    style={{
                      background: active ? 'var(--c-accent-soft)' : 'transparent',
                      borderLeft: `2px solid ${active ? 'var(--c-accent)' : 'transparent'}`,
                    }}
                  >
                    <div className="relative shrink-0">
                      <div className="w-8 h-8 rounded-full flex items-center justify-center text-[0.6875rem] font-semibold uppercase"
                           style={{
                             background: 'var(--c-surface-3)',
                             color: active ? 'var(--c-accent)' : 'var(--c-ink-3)',
                           }}>
                        {(a.name || '?').substring(0, 2)}
                      </div>
                      <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full"
                            style={{
                              background: a.enabled ? 'var(--c-good)' : 'var(--c-ink-3)',
                              border: '2px solid var(--c-surface)',
                            }}
                            aria-hidden="true" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-medium truncate" style={{ color: 'var(--c-ink)' }}>{a.name}</span>
                        {a.must_change_password && (
                          <KeySquare className="w-3 h-3 shrink-0" style={{ color: 'var(--c-warn)' }}
                                     aria-label={t('ac_req_pw_change', lang)} />
                        )}
                      </div>
                      <div className="text-[0.6875rem] font-mono tabular-nums truncate mt-0.5"
                           style={{ color: 'var(--c-ink-3)' }}>
                        {a.rpm || 0} rpm · {fmt(a.tpm || 0)} tpm · {a.rpd || 0} rpd
                      </div>
                    </div>
                    <Badge variant={TIER_VARIANT[a.tier] || 'ghost'} size="sm">
                      {a.tier}
                    </Badge>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="px-3 py-2 text-[0.6875rem] tabular-nums"
               style={{ borderTop: '1px solid var(--c-line)', color: 'var(--c-ink-3)' }}>
            {sortedAccounts.length} / {accounts.length} {t('accounts_count', lang)}
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
            <Card padding="xl" className="text-center">
              <p className="text-xs" style={{ color: 'var(--c-ink-3)' }}>{t('ac_none', lang)}</p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}