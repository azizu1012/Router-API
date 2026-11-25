import { useState, useEffect } from 'react';
import {
  KeyRound, Plus, Copy, Check, Trash2, Pencil, ShieldCheck, ShieldX,
  Users, AlertTriangle, Clock,
} from 'lucide-react';
import { fmt, fmtD } from '../utils/format';
import { api } from '../utils/api';
import { copyText } from '../utils/clipboard';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import Button from './Button';
import Badge from './Badge';
import Alert from './Alert';
import Modal from './Modal';

const ALL_TIERS = ['free', 'premium', 'admin'];
const TIER_RANKS = { free: 0, premium: 1, admin: 2 };

const LIMITS = {
  user: {
    max_concurrency: [1, 6],
    rpm: [1, 300],
    tpm: [1000, 6_000_000],
    rpd: [1, 20_000],
  },
  admin: {
    max_concurrency: [1, 64],
    rpm: [1, 100_000],
    tpm: [1_000, 100_000_000],
    rpd: [1, 100_000],
  },
};

const DEFAULTS = {
  user: { max_concurrency: 6, rpm: 30, tpm: 200_000, rpd: 1_000 },
  admin: { max_concurrency: 6, rpm: 30, tpm: 200_000, rpd: 1_000 },
};

const TIER_VARIANT = { admin: 'primary', premium: 'info', free: 'ghost' };

/** One labelled field. The hint carries the range or a one-line explanation —
 *  never both, so a field never reads twice. */
function Field({ label, value, min, max, onChange, disabled, hint, type = 'number' }) {
  return (
    <label className="block w-full">
      <span className="label-micro block mb-1.5">{label}</span>
      <input
        type={type}
        className="input input-sm w-full text-xs"
        value={value}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        autoComplete="off"
      />
      <span className="block text-[0.6875rem] mt-1" style={{ color: 'var(--c-ink-3)' }}>
        {hint || (min != null && max != null ? `${min} – ${max}` : '')}
      </span>
    </label>
  );
}

/**
 * Issue / edit / revoke form for one auth token.
 *
 * The four limits are independent and each one blocks on its own, so the form
 * says so rather than presenting them as a single budget: a token at its RPM
 * ceiling still has concurrency and TPM headroom. min_interval_seconds only
 * applies when max_concurrency is 1 — with more slots the gap between starts
 * is already smaller than the interval — so it is disabled and annotated
 * rather than silently ignored.
 */
function TokenForm({ initial, isAdmin, accountTier = 'free', submitLabel, onSubmit, onCancel, busy, lang }) {
  const caps = isAdmin ? LIMITS.admin : LIMITS.user;
  const d = isAdmin ? DEFAULTS.admin : DEFAULTS.user;

  const maxRank = isAdmin ? 2 : (TIER_RANKS[accountTier] ?? 0);
  const availableTiers = ALL_TIERS.filter((tier) => TIER_RANKS[tier] <= maxRank);

  const initialTier = initial?.tier;
  const defaultTier = initialTier
    ? (TIER_RANKS[initialTier] <= maxRank ? initialTier : (availableTiers[availableTiers.length - 1] || 'free'))
    : (availableTiers[availableTiers.length - 1] || 'free');

  const [form, setForm] = useState(() => ({
    label: initial?.label || '',
    tier: defaultTier,
    max_concurrency: initial?.max_concurrency ?? d.max_concurrency,
    rpm: initial?.rpm ?? d.rpm,
    tpm: initial?.tpm ?? d.tpm,
    rpd: initial?.rpd ?? d.rpd,
    min_interval_seconds: initial?.min_interval_seconds ?? 3,
  }));
  const [err, setErr] = useState('');

  const set = (k) => (v) => {
    setErr('');
    setForm((f) => ({ ...f, [k]: v }));
  };

  const setNum = (k) => (raw) => set(k)(raw === '' ? '' : Number(raw));

  const concurrencyOne = Number(form.max_concurrency) === 1;

  const handleSubmit = async (e) => {
    e.preventDefault();
    const nums = ['max_concurrency', 'rpm', 'tpm', 'rpd'];
    for (const k of nums) {
      const v = Number(form[k]);
      const [lo, hi] = caps[k];
      if (!Number.isFinite(v) || v < lo || v > hi) {
        setErr(`${k} ${t('tt_bounds', lang)} ${lo} – ${hi}`);
        return;
      }
    }
    const interval = Number(form.min_interval_seconds);
    if (!Number.isFinite(interval) || interval < 0) {
      setErr(t('tt_interval_neg', lang));
      return;
    }
    await onSubmit({
      label: form.label.slice(0, 64),
      tier: form.tier,
      max_concurrency: Number(form.max_concurrency),
      rpm: Number(form.rpm),
      tpm: Number(form.tpm),
      rpd: Number(form.rpd),
      min_interval_seconds: concurrencyOne ? interval : 0,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3 text-left">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label={t('tt_label', lang)} type="text" value={form.label}
               onChange={set('label')} disabled={busy}
               hint={t('tt_label_hint', lang)} />
        <label className="block w-full">
          <span className="label-micro block mb-1.5">Tier</span>
          <select className="select select-sm text-xs w-full"
                  value={form.tier} disabled={busy || availableTiers.length <= 1}
                  onChange={(e) => set('tier')(e.target.value)}>
            {availableTiers.map((tier) => <option key={tier} value={tier}>{tier}</option>)}
          </select>
          <span className="block text-[0.6875rem] mt-1" style={{ color: 'var(--c-ink-3)' }}>
            {availableTiers.length <= 1
              ? `${t('tt_tier_fixed', lang)} ${accountTier}`
              : t('tt_tier_hint', lang)}
          </span>
        </label>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Field label="Concurrency" value={form.max_concurrency}
               onChange={setNum('max_concurrency')}
               min={caps.max_concurrency[0]} max={caps.max_concurrency[1]}
               disabled={busy} hint={t('tt_concurrency_hint', lang)} />
        <Field label="RPM" value={form.rpm} onChange={setNum('rpm')}
               min={caps.rpm[0]} max={caps.rpm[1]} disabled={busy}
               hint={t('tt_rpm_hint', lang)} />
        <Field label="TPM" value={form.tpm} onChange={setNum('tpm')}
               min={caps.tpm[0]} max={caps.tpm[1]} disabled={busy}
               hint={t('tt_tpm_hint', lang)} />
        <Field label="RPD" value={form.rpd} onChange={setNum('rpd')}
               min={caps.rpd[0]} max={caps.rpd[1]} disabled={busy}
               hint={t('tt_rpd_hint', lang)} />
      </div>

      <Field
        label={t('tt_interval', lang)}
        value={form.min_interval_seconds}
        onChange={setNum('min_interval_seconds')}
        min={0}
        disabled={busy || !concurrencyOne}
        hint={concurrencyOne
          ? t('tt_interval_hint', lang)
          : t('tt_interval_off_hint', lang)}
      />

      {!concurrencyOne && (
        <Alert type="info"
               message={`${form.max_concurrency || '?'} slots — ${t('tt_interval_note', lang)}`} />
      )}

      {err && <Alert type="error" message={err} />}

      <div className="flex gap-2 justify-end pt-1">
        {onCancel && (
          <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>
            {t('btn_cancel', lang)}
          </Button>
        )}
        <Button type="submit" loading={busy} className="min-w-[8rem]">
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}

/**
 * A one-time enrollment code, shown big because it is read aloud or typed by
 * hand. Re-issuing voids the previous code, so the UI says which one is live
 * instead of implying both work.
 */
export function InvitePanel({ token, onIssued }) {
  const { lang } = useApp();
  const [invite, setInvite] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [ttl, setTtl] = useState(180);
  const [copied, setCopied] = useState(false);

  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

  const issue = async () => {
    setBusy(true);
    setErr('');
    setCopied(false);
    try {
      const data = await api('/dashboard/admin/invites/issue', {
        method: 'POST',
        body: JSON.stringify({ ttl_seconds: Number(ttl) })
      }, token);
      setInvite(data);
      setNow(Date.now() / 1000);
      if (onIssued) onIssued(data);
    } catch (e) {
      setErr(e.message);
      setInvite(null);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!invite) return;
    // The code is on screen either way, so a blocked clipboard needs no alarm.
    if (await copyText(invite.code)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const remaining = invite ? Math.max(0, Math.round(invite.expires_at - now)) : 0;
  const expired = invite && remaining <= 0;

  const ttls = [
    { v: 180, label: t('tt_ttl_3', lang) },
    { v: 300, label: t('tt_ttl_5', lang) },
    { v: 600, label: t('tt_ttl_10', lang) },
    { v: 900, label: t('tt_ttl_15', lang) },
  ];

  return (
    <div className="glass-card rounded-xl p-4 text-left">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold flex items-center gap-2" style={{ color: 'var(--c-ink)' }}>
            <Users className="w-4 h-4" style={{ color: 'var(--c-accent)' }} />
            {t('tt_invite_title', lang)}
          </h3>
          <p className="text-[0.6875rem] mt-1 leading-relaxed" style={{ color: 'var(--c-ink-3)' }}>
            {t('tt_invite_sub', lang)}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <select
            className="select select-xs text-[0.6875rem]"
            value={ttl}
            disabled={busy}
            onChange={(e) => setTtl(e.target.value)}
            aria-label={t('tt_invite_title', lang)}
          >
            {ttls.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
          <Button onClick={issue} loading={busy}>{t('tt_invite_issue', lang)}</Button>
        </div>
      </div>

      {err && <div className="mb-3"><Alert type="error" message={err} /></div>}

      {invite ? (
        <div className="flex flex-wrap items-center justify-between gap-3 p-4 rounded-lg"
             style={{
               background: expired ? 'var(--c-surface-2)' : 'color-mix(in srgb, var(--c-good) 8%, transparent)',
               border: `1px solid ${expired ? 'var(--c-line)' : 'color-mix(in srgb, var(--c-good) 26%, transparent)'}`,
               opacity: expired ? 0.7 : 1,
             }}>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <code className="font-mono text-[1.75rem] leading-none font-semibold tracking-[0.3em] tabular-nums"
                  style={{ color: expired ? 'var(--c-ink-3)' : 'var(--c-accent)' }}>
              {invite.code}
            </code>
            <span className="flex items-center gap-1.5 text-xs font-medium"
                  style={{ color: expired ? 'var(--c-ink-3)' : 'var(--c-good)' }}>
              {expired ? <ShieldX className="w-3.5 h-3.5" /> : <Clock className="w-3.5 h-3.5" />}
              {expired
                ? t('tt_invite_expired', lang)
                : `${t('tt_invite_left', lang)} ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`}
            </span>
          </div>
          <Button variant="ghost" onClick={copy} disabled={expired}
                  icon={copied ? Check : Copy}>
            {copied ? t('tt_copied', lang) : t('tt_copy', lang)}
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2 p-3 rounded-lg text-[0.6875rem]"
             style={{ background: 'var(--c-surface-2)', color: 'var(--c-ink-3)' }}>
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          {t('tt_invite_empty', lang)}
        </div>
      )}
    </div>
  );
}

/**
 * The auth tokens of one account.
 *
 * `scope="admin"` lets the ceilings be raised; `scope="user"` clamps every field
 * to the account's own budget so a token can only be tightened, never widened.
 */
export default function TokenTable({
  tokens, loading, scope = 'user', accountTier = 'free', accountName,
  onIssue, onUpdate, onRevoke,
}) {
  const { lang } = useApp();
  const [mode, setMode] = useState(null);       // {type:'issue'} | {type:'edit', token}
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [copiedId, setCopiedId] = useState(null);
  const isAdmin = scope === 'admin';

  const close = () => { setMode(null); setMsg(null); };

  const run = async (fn, okText) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ text: okText, type: 'success' });
      close();
    } catch (e) {
      setMsg({ text: e.message, type: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const handleIssue = (payload) => run(
    () => onIssue(payload),
    t('msg_token_config', lang)
  );

  const handleUpdate = (payload) => run(
    () => onUpdate(mode.token.key_id, payload),
    t('msg_token_updated', lang)
  );

  const handleToggle = (tk) => run(
    () => onUpdate(tk.key_id, { enabled: !tk.enabled }),
    tk.enabled ? t('msg_token_locked', lang) : t('msg_token_unlocked', lang)
  );

  const handleRevoke = () => {
    const tk = confirm;
    setConfirm(null);
    run(() => onRevoke(tk.key_id), t('msg_token_revoked', lang));
  };

  const copy = async (tk) => {
    if (await copyText(tk.token)) {
      setCopiedId(tk.key_id);
      setTimeout(() => setCopiedId(null), 2000);
    }
  };

  const rows = tokens || [];

  return (
    <div className="space-y-3">
      {onIssue && mode?.type !== 'issue' && (
        <div className="flex justify-end">
          <Button icon={Plus} onClick={() => { setMsg(null); setMode({ type: 'issue' }); }}>
            {t('tt_new_token', lang)}
          </Button>
        </div>
      )}

      {mode?.type === 'issue' && (
        <div className="rounded-lg p-4 animate-fade-in-up"
             style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
          <h4 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink)' }}>
            {t('tt_issue_title', lang)}
          </h4>
          <TokenForm
            isAdmin={isAdmin}
            accountTier={accountTier}
            busy={busy}
            lang={lang}
            submitLabel={t('tt_submit_create', lang)}
            onSubmit={handleIssue}
            onCancel={close}
          />
        </div>
      )}

      {msg && <Alert type={msg.type} message={msg.text} />}

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-8 text-xs"
             style={{ color: 'var(--c-ink-3)' }}>
          <span className="loading loading-spinner loading-xs" /> {t('tt_loading', lang)}
        </div>
      ) : rows.length === 0 && mode?.type !== 'issue' ? (
        <div className="flex flex-col items-center gap-3 py-8" style={{ color: 'var(--c-ink-3)' }}>
          <KeyRound className="w-7 h-7 opacity-40" />
          <span className="text-xs font-medium">
            {isAdmin
              ? `${t('tt_empty_admin_pre', lang)} ${accountName} ${t('tt_empty_admin_post', lang)}`
              : t('tt_empty_user', lang)}
          </span>
        </div>
      ) : (
        <div className="glass-card rounded-xl overflow-hidden">
          <div className="overflow-x-auto w-full">
            <table className="table table-pin w-full">
              <thead>
                <tr>
                  <th className="min-w-[14rem]">{t('th_token', lang)}</th>
                  <th className="min-w-[5.5rem]">{t('th_tier', lang)}</th>
                  <th className="min-w-[4rem] text-right">{t('th_concurrency', lang)}</th>
                  <th className="min-w-[4.5rem] text-right">{t('th_rpm', lang)}</th>
                  <th className="min-w-[4.5rem] text-right">{t('th_tpm', lang)}</th>
                  <th className="min-w-[4.5rem] text-right">{t('th_rpd', lang)}</th>
                  <th className="min-w-[6.5rem]">{t('th_status', lang)}</th>
                  <th className="min-w-[6.5rem]">{t('th_created', lang)}</th>
                  <th className="min-w-[7.5rem] text-right">{t('th_actions', lang)}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((tk) => (
                  <tr key={tk.key_id}>
                    <td className="max-w-0">
                      <div className="flex items-center gap-1.5">
                        <code
                          className="font-mono text-[0.6875rem] block overflow-hidden text-ellipsis whitespace-nowrap"
                          style={{
                            color: tk.enabled ? 'var(--c-accent)' : 'var(--c-ink-3)',
                            textDecoration: tk.enabled ? 'none' : 'line-through',
                          }}
                          title={tk.token}
                        >
                          {tk.token}
                        </code>
                        <button
                          onClick={() => copy(tk)}
                          className="btn btn-ghost btn-xs btn-square shrink-0"
                          style={{ color: copiedId === tk.key_id ? 'var(--c-good)' : 'var(--c-ink-3)' }}
                          title={t('tt_copy_token', lang)}
                          aria-label={t('tt_copy_token', lang)}
                        >
                          {copiedId === tk.key_id
                            ? <Check className="w-3 h-3" />
                            : <Copy className="w-3 h-3" />}
                        </button>
                      </div>
                      {tk.label && (
                        <span className="text-[0.6875rem] italic" style={{ color: 'var(--c-ink-3)' }}>
                          {tk.label}
                        </span>
                      )}
                    </td>
                    <td>
                      <Badge variant={TIER_VARIANT[tk.tier] || 'ghost'}>{tk.tier}</Badge>
                    </td>
                    <td className="text-right tabular-nums">{tk.max_concurrency}</td>
                    <td className="text-right tabular-nums">{(tk.rpm || 0).toLocaleString()}</td>
                    <td className="text-right tabular-nums">{fmt(tk.tpm || 0)}</td>
                    <td className="text-right tabular-nums">{(tk.rpd || 0).toLocaleString()}</td>
                    <td>
                      <Badge variant={tk.enabled ? 'success' : 'ghost'} dot={tk.enabled}>
                        {tk.enabled ? t('st_enabled', lang) : t('st_locked', lang)}
                      </Badge>
                    </td>
                    <td className="text-[0.6875rem] whitespace-nowrap tabular-nums"
                        style={{ color: 'var(--c-ink-3)' }}>
                      {fmtD(tk.created_at)}
                    </td>
                    <td>
                      <div className="flex items-center justify-end gap-0.5">
                        <button
                          onClick={() => handleToggle(tk)}
                          disabled={busy}
                          className="btn btn-ghost btn-xs btn-square"
                          style={{ color: tk.enabled ? 'var(--c-critical)' : 'var(--c-good)' }}
                          title={tk.enabled ? t('tt_toggle_lock', lang) : t('tt_toggle_unlock', lang)}
                          aria-label={tk.enabled ? t('tt_toggle_lock', lang) : t('tt_toggle_unlock', lang)}
                        >
                          {tk.enabled
                            ? <ShieldX className="w-3.5 h-3.5" />
                            : <ShieldCheck className="w-3.5 h-3.5" />}
                        </button>
                        <button
                          onClick={() => { setMsg(null); setMode({ type: 'edit', token: tk }); }}
                          disabled={busy}
                          className="btn btn-ghost btn-xs btn-square"
                          style={{ color: 'var(--c-accent)' }}
                          title={t('tt_edit_limits', lang)}
                          aria-label={t('tt_edit_limits', lang)}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => setConfirm(tk)}
                          disabled={busy}
                          className="btn btn-ghost btn-xs btn-square"
                          style={{ color: 'var(--c-critical)' }}
                          title={t('tt_revoke', lang)}
                          aria-label={t('tt_revoke', lang)}
                        >
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
      )}

      {mode?.type === 'edit' && (
        <div className="rounded-lg p-4 animate-fade-in-up"
             style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
          <div className="flex items-center justify-between gap-3 mb-3">
            <h4 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>
              {t('tt_edit_title', lang)}
            </h4>
            <code className="font-mono text-[0.6875rem] truncate" style={{ color: 'var(--c-ink-3)' }}>
              {mode.token.token}
            </code>
          </div>
          <TokenForm
            initial={mode.token}
            isAdmin={isAdmin}
            accountTier={accountTier}
            busy={busy}
            lang={lang}
            submitLabel={t('tt_submit_save', lang)}
            onSubmit={handleUpdate}
            onCancel={close}
          />
        </div>
      )}

      <Modal
        isOpen={Boolean(confirm)}
        onClose={() => setConfirm(null)}
        title={t('tt_confirm_title', lang)}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              {t('btn_cancel', lang)}
            </Button>
            <Button variant="danger" onClick={handleRevoke}>
              {t('tt_revoke', lang)}
            </Button>
          </>
        }
      >
        {confirm && (
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="w-4 h-4 mt-px shrink-0" style={{ color: 'var(--c-critical)' }} />
            <p className="text-xs leading-relaxed" style={{ color: 'var(--c-ink-2)' }}>
              <code className="font-mono" style={{ color: 'var(--c-accent)' }}>{confirm.token}</code>{' '}
              {t('tt_confirm_body', lang)}
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}