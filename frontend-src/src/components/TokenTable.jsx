import React, { useState, useEffect } from 'react';
import {
  KeyRound, Plus, Copy, Check, Trash2, Pencil, ShieldCheck, ShieldX,
  Users, AlertTriangle, Clock, Info,
} from 'lucide-react';
import { fmt } from '../utils/format';
import { api } from '../utils/api';

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

/** Render a labelled numeric field with its allowed range spelled out. */
function Field({ label, value, min, max, onChange, disabled, hint }) {
  return (
    <label className="form-control w-full">
      <span className="label-text font-bold text-[11px] uppercase text-base-content/60 mb-1">
        {label}
      </span>
      <input
        type="number"
        className="input input-bordered input-sm w-full text-xs"
        value={value}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
      <span className="text-[10px] text-base-content/40 mt-0.5">
        {hint || `${min} – ${max}`}
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
function TokenForm({ initial, isAdmin, accountTier = 'free', submitLabel, onSubmit, onCancel, busy }) {
  const caps = isAdmin ? LIMITS.admin : LIMITS.user;
  const d = isAdmin ? DEFAULTS.admin : DEFAULTS.user;

  const maxRank = isAdmin ? 2 : (TIER_RANKS[accountTier] ?? 0);
  const availableTiers = ALL_TIERS.filter((t) => TIER_RANKS[t] <= maxRank);

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
        setErr(`${k} phải nằm trong ${lo} – ${hi}`);
        return;
      }
    }
    const interval = Number(form.min_interval_seconds);
    if (!Number.isFinite(interval) || interval < 0) {
      setErr('min_interval_seconds không được âm');
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
        <Field label="Nhãn" value={form.label} onChange={set('label')}
               disabled={busy} hint="Tùy chọn, giúp nhận biết token" />
        <label className="form-control w-full">
          <span className="label-text font-bold text-[11px] uppercase text-base-content/60 mb-1">Tier</span>
          <select className="select select-bordered select-sm text-xs"
                  value={form.tier} disabled={busy || availableTiers.length <= 1}
                  onChange={(e) => set('tier')(e.target.value)}>
            {availableTiers.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <span className="text-[10px] text-base-content/40 mt-0.5">
            {availableTiers.length <= 1
              ? `Cố định theo gói ${accountTier} của tài khoản`
              : 'Quyết định pool key nào token được dùng'}
          </span>
        </label>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Field label="Concurrency" value={form.max_concurrency}
               onChange={setNum('max_concurrency')}
               min={caps.max_concurrency[0]} max={caps.max_concurrency[1]}
               disabled={busy} hint="Request song song tối đa" />
        <Field label="RPM" value={form.rpm} onChange={setNum('rpm')}
               min={caps.rpm[0]} max={caps.rpm[1]} disabled={busy}
               hint="Request / 60 giây" />
        <Field label="TPM" value={form.tpm} onChange={setNum('tpm')}
               min={caps.tpm[0]} max={caps.tpm[1]} disabled={busy}
               hint="Token / 60 giây" />
        <Field label="RPD" value={form.rpd} onChange={setNum('rpd')}
               min={caps.rpd[0]} max={caps.rpd[1]} disabled={busy}
               hint="Request / ngày" />
      </div>

      <Field
        label="Min interval (giây)"
        value={form.min_interval_seconds}
        onChange={setNum('min_interval_seconds')}
        min={0}
        disabled={busy || !concurrencyOne}
        hint={concurrencyOne
          ? 'Giãn cách giữa 2 lần bắt đầu'
          : 'Chỉ có tác dụng khi Concurrency = 1'}
      />

      {!concurrencyOne && (
        <div className="flex items-start gap-2 p-2.5 rounded-lg bg-info/10 border border-info/20">
          <Info className="w-3.5 h-3.5 text-info shrink-0 mt-0.5" />
          <span className="text-[11px] text-base-content/70 leading-relaxed">
            Với {form.max_concurrency || '?'} slot chạy song song, khoảng cách
            giữa các lần bắt đầu luôn nhỏ hơn interval — dùng <b>RPM</b> để
            giãn tải thay thế.
          </span>
        </div>
      )}

      {err && (
        <div className="text-xs font-semibold p-2.5 rounded-lg bg-error/10 text-error border border-error/20">
          {err}
        </div>
      )}

      <div className="flex gap-2 justify-end pt-1">
        {onCancel && (
          <button type="button" disabled={busy} onClick={onCancel}
                  className="btn btn-ghost btn-sm font-bold w-24">
            Hủy
          </button>
        )}
        <button type="submit" disabled={busy}
                className="btn btn-primary btn-sm font-bold w-32">
          {busy ? <span className="loading loading-spinner loading-xs" /> : submitLabel}
        </button>
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
      setErr('❌ ' + e.message);
      setInvite(null);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!invite) return;
    try {
      await navigator.clipboard.writeText(invite.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked; the code is on screen anyway */ }
  };

  const remaining = invite ? Math.max(0, Math.round(invite.expires_at - now)) : 0;
  const expired = invite && remaining <= 0;

  return (
    <div className="card glass-card rounded-2xl border border-base-content/5 p-5 text-left">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <h3 className="font-extrabold text-sm flex items-center gap-2">
            <Users className="w-4 h-4 text-primary" /> Mã đăng ký
          </h3>
          <p className="text-[11px] text-base-content/55 mt-0.5">
            User dùng mã này để tự tạo tài khoản. Hạn 3 phút, dùng 1 lần.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="select select-bordered select-xs text-[11px]"
            value={ttl}
            disabled={busy}
            onChange={(e) => setTtl(e.target.value)}
          >
            <option value={180}>3 phút</option>
            <option value={300}>5 phút</option>
            <option value={600}>10 phút</option>
            <option value={900}>15 phút</option>
          </select>
          <button onClick={issue} disabled={busy}
                  className="btn btn-primary btn-sm font-bold">
            {busy ? <span className="loading loading-spinner loading-xs" /> : 'Cấp mã'}
          </button>
        </div>
      </div>

      {err && (
        <div className="text-xs font-semibold p-2.5 rounded-lg bg-error/10 text-error border border-error/20 mb-3">
          {err}
        </div>
      )}

      {invite ? (
        <div className={`flex items-center justify-between gap-3 p-4 rounded-xl border ${
          expired
            ? 'bg-error/5 border-error/20 opacity-60'
            : 'bg-success/5 border-success/20'
        }`}>
          <div className="flex items-center gap-4">
            <code className="font-mono text-3xl font-black tracking-[0.3em] text-primary">
              {invite.code}
            </code>
            <span className={`flex items-center gap-1.5 text-xs font-bold ${
              expired ? 'text-error' : 'text-success'
            }`}>
              {expired ? <ShieldX className="w-3.5 h-3.5" /> : <Clock className="w-3.5 h-3.5" />}
              {expired
                ? 'Đã hết hạn'
                : `còn ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`}
            </span>
          </div>
          <button onClick={copy} disabled={expired}
                  className="btn btn-ghost btn-sm gap-1.5 font-bold">
            {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
            {copied ? 'Đã copy' : 'Copy'}
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-base-200/30 text-base-content/45 text-xs">
          <AlertTriangle className="w-3.5 h-3.5" />
          Chưa có mã nào. Bấm "Cấp mã" — mỗi lần cấp mã cũ sẽ hết hiệu lực.
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
      setMsg({ text: '❌ ' + e.message, type: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const handleIssue = (payload) => run(
    () => onIssue(payload),
    '✅ Đã cấu hình token. Copy giá trị bên dưới — chỉ hiện một lần.'
  );

  const handleUpdate = (payload) => run(
    () => onUpdate(mode.token.key_id, payload),
    '✅ Đã cập nhật giới hạn token.'
  );

  const handleToggle = (tk) => run(
    () => onUpdate(tk.key_id, { enabled: !tk.enabled }),
    tk.enabled ? '⏸ Đã tạm khoá token.' : '▶ Đã kích hoạt token.'
  );

  const handleRevoke = () => {
    const tk = confirm;
    setConfirm(null);
    run(() => onRevoke(tk.key_id), '🗑 Đã thu hồi token.');
  };

  const copy = async (tk) => {
    try {
      await navigator.clipboard.writeText(tk.token);
      setCopiedId(tk.key_id);
      setTimeout(() => setCopiedId(null), 2000);
    } catch { /* clipboard blocked */ }
  };

  const rows = tokens || [];

  return (
    <div className="space-y-3">
      {onIssue && mode?.type !== 'issue' && (
        <div className="flex justify-end">
          <button
            onClick={() => { setMsg(null); setMode({ type: 'issue' }); }}
            className="btn btn-primary btn-sm gap-1.5 font-bold"
          >
            <Plus className="w-3.5 h-3.5" /> Tạo token mới
          </button>
        </div>
      )}

      {mode?.type === 'issue' && (
        <div className="card glass-card rounded-2xl border border-primary/25 p-5">
          <h4 className="font-extrabold text-xs mb-3 uppercase text-base-content/70">
            Cấu hình token mới
          </h4>
          <TokenForm
            isAdmin={isAdmin}
            accountTier={accountTier}
            busy={busy}
            submitLabel="Tạo token"
            onSubmit={handleIssue}
            onCancel={close}
          />
        </div>
      )}

      {msg && (
        <div className={`text-xs font-semibold p-2.5 rounded-lg border ${
          msg.type === 'success'
            ? 'bg-success/10 text-success border-success/20'
            : 'bg-error/10 text-error border-error/20'
        }`}>
          {msg.text}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-8 text-base-content/45 text-xs">
          <span className="loading loading-spinner loading-xs" /> Đang tải token...
        </div>
      ) : rows.length === 0 && mode?.type !== 'issue' ? (
        <div className="flex flex-col items-center gap-3 py-8 text-base-content/45">
          <KeyRound className="w-7 h-7 opacity-40" />
          <span className="text-xs font-medium">
            {isAdmin
              ? `Tài khoản ${accountName} chưa có token nào`
              : 'Bạn chưa có auth token nào'}
          </span>
        </div>
      ) : (
        <div className="overflow-x-auto w-full">
          <table className="table table-zebra w-full text-xs">
            <thead>
              <tr className="border-b border-base-content/5 text-base-content/60 bg-base-200/35 select-none">
                <th className="font-bold whitespace-nowrap">Token</th>
                <th className="font-bold whitespace-nowrap">Tier</th>
                <th className="font-bold whitespace-nowrap">Conc</th>
                <th className="font-bold whitespace-nowrap">RPM</th>
                <th className="font-bold whitespace-nowrap">TPM</th>
                <th className="font-bold whitespace-nowrap">RPD</th>
                <th className="font-bold whitespace-nowrap">Trạng thái</th>
                <th className="font-bold whitespace-nowrap">Ngày tạo</th>
                <th className="font-bold whitespace-nowrap">Hành động</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((tk) => (
                <tr key={tk.key_id} className="border-b border-base-content/5 hover:bg-base-200/50">
                  <td className="max-w-0">
                    <div className="flex items-center gap-1.5">
                      <code
                        className={`font-mono text-[10px] block overflow-hidden text-ellipsis whitespace-nowrap ${
                          tk.enabled ? 'text-primary' : 'text-base-content/40 line-through'
                        }`}
                        title={tk.token}
                      >
                        {tk.token}
                      </code>
                      <button
                        onClick={() => copy(tk)}
                        className="btn btn-ghost btn-xs btn-square text-base-content/60 hover:text-primary shrink-0"
                        title="Copy token"
                      >
                        {copiedId === tk.key_id
                          ? <Check className="w-3 h-3 text-success" />
                          : <Copy className="w-3 h-3" />}
                      </button>
                    </div>
                    {tk.label && (
                      <span className="text-[10px] text-base-content/45 italic">{tk.label}</span>
                    )}
                  </td>
                  <td>
                    <span className={`badge badge-xs text-[9px] font-extrabold uppercase ${
                      tk.tier === 'admin' ? 'badge-primary'
                        : tk.tier === 'premium' ? 'badge-accent'
                        : 'badge-ghost border-base-content/15'
                    }`}>{tk.tier}</span>
                  </td>
                  <td>{tk.max_concurrency}</td>
                  <td>{(tk.rpm || 0).toLocaleString()}</td>
                  <td>{fmt(tk.tpm || 0)}</td>
                  <td>{(tk.rpd || 0).toLocaleString()}</td>
                  <td>
                    <span className={`badge badge-xs font-bold uppercase ${
                      tk.enabled
                        ? 'badge-success/15 text-success border border-success/30'
                        : 'badge-ghost border-base-content/15 text-base-content/50'
                    }`}>{tk.enabled ? 'Active' : 'Locked'}</span>
                  </td>
                  <td className="text-base-content/50 font-medium whitespace-nowrap">
                    {tk.created_at ? fmt(tk.created_at) : '—'}
                  </td>
                  <td>
                    <div className="flex items-center gap-0.5">
                      <button
                        onClick={() => handleToggle(tk)}
                        disabled={busy}
                        className={`btn btn-ghost btn-xs btn-square ${
                          tk.enabled
                            ? 'text-error hover:bg-error/15'
                            : 'text-success hover:bg-success/15'
                        }`}
                        title={tk.enabled ? 'Khoá token' : 'Kích hoạt token'}
                      >
                        {tk.enabled
                          ? <ShieldX className="w-3.5 h-3.5" />
                          : <ShieldCheck className="w-3.5 h-3.5" />}
                      </button>
                      <button
                        onClick={() => {
                          setMsg(null);
                          setMode({ type: 'edit', token: tk });
                        }}
                        disabled={busy}
                        className="btn btn-ghost btn-xs btn-square text-primary hover:bg-primary/15"
                        title="Sửa giới hạn"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => setConfirm(tk)}
                        disabled={busy}
                        className="btn btn-ghost btn-xs btn-square text-error hover:bg-error/15"
                        title="Thu hồi token"
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
      )}

      {mode?.type === 'edit' && (
        <div className="card glass-card rounded-2xl border border-primary/25 p-5">
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-extrabold text-xs uppercase text-base-content/70">
              Sửa giới hạn token
            </h4>
            <code className="font-mono text-[10px] text-base-content/50">
              {mode.token.token}
            </code>
          </div>
          <TokenForm
            initial={mode.token}
            isAdmin={isAdmin}
            accountTier={accountTier}
            busy={busy}
            submitLabel="Lưu"
            onSubmit={handleUpdate}
            onCancel={close}
          />
        </div>
      )}

      {confirm && (
        <div className="modal modal-open z-[9999] fixed inset-0 flex items-center justify-center">
          <div className="modal-overlay fixed inset-0 bg-black/60 backdrop-blur-sm"
               onClick={() => setConfirm(null)}></div>
          <div className="modal-box max-w-sm bg-base-100 border border-base-content/10 relative z-10 p-6 rounded-2xl shadow-2xl">
            <h3 className="font-extrabold text-base mb-2 text-left flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-error" /> Thu hồi token?
            </h3>
            <p className="text-xs text-base-content/65 leading-relaxed mb-4 text-left">
              <code className="font-mono text-primary">{confirm.token}</code> sẽ
              ngừng hoạt động ngay. Mọi client đang dùng nó sẽ nhận 401 và phải
              lấy token mới. Không thể hoàn tác.
            </p>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setConfirm(null)}
                      className="btn btn-ghost btn-sm font-bold w-24">Hủy</button>
              <button onClick={handleRevoke}
                      className="btn btn-error btn-sm font-bold w-28">Thu hồi</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}