import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../utils/api';
import { fmt, fmtD } from '../utils/format';
import EditAccountModal from './EditAccountModal';
import TokenTable from './TokenTable';
import PasswordReveal from './PasswordReveal';
import { TIER_BADGE } from '../utils/tier';
import {
  ShieldCheck, ShieldAlert, KeyRound, KeySquare, Edit, Copy, RefreshCw, Trash2, Gauge, Lock,
} from 'lucide-react';


function Section({ icon: Icon, title, hint, children, tone = '' }) {
  return (
    <section className={`card glass-card rounded-2xl p-5 text-left border ${tone || 'border-base-content/5'}`}>
      <div className="mb-3">
        <h3 className="font-extrabold text-sm flex items-center gap-2">
          {Icon && <Icon className="w-4 h-4 text-primary" />} {title}
        </h3>
        {hint && <p className="text-[11px] text-base-content/55 mt-0.5">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

function LimitTile({ label, value }) {
  return (
    <div className="rounded-xl bg-base-200/40 border border-base-content/5 px-4 py-3">
      <div className="text-[10px] font-bold uppercase tracking-wider text-base-content/50">{label}</div>
      <div className="text-xl font-black text-base-content/90 mt-1 font-mono">{value}</div>
    </div>
  );
}

/**
 * Everything an admin can do to ONE account, grouped by intent instead of
 * crammed into a row of unlabeled icon buttons. Tokens are fetched here, on
 * selection, rather than bundled into /dashboard/accounts (which would multiply
 * that payload by the number of accounts).
 */
export default function AccountDetailPanel({ account, token, refreshTab, notify, onSecret, onDeleted }) {
  const [tokens, setTokens] = useState([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [busy, setBusy] = useState(null);

  const name = account.name;

  const loadTokens = useCallback(async () => {
    setTokensLoading(true);
    try {
      const res = await api('/dashboard/admin/accounts/keys', {
        method: 'POST',
        body: JSON.stringify({ name }),
      }, token);
      setTokens(res?.keys || []);
    } catch (e) {
      setTokens([]);
      notify('Không tải được token: ' + e.message, 'error');
    } finally {
      setTokensLoading(false);
    }
  }, [name, token]);

  useEffect(() => { loadTokens(); }, [loadTokens]);

  const run = async (key, fn) => {
    setBusy(key);
    try { await fn(); } catch (e) { notify('Lỗi: ' + e.message, 'error'); } finally { setBusy(null); }
  };

  const issueToken = async (payload) => {
    const res = await api('/dashboard/admin/accounts/keys/issue', {
      method: 'POST', body: JSON.stringify({ name, ...payload }),
    }, token);
    await loadTokens();
    refreshTab();
    if (res?.token) onSecret({ title: `Token mới cho ${name}`, value: res.token });
  };

  const updateToken = async (keyId, payload) => {
    await api('/dashboard/admin/accounts/keys/update', {
      method: 'POST', body: JSON.stringify({ key_id: keyId, ...payload }),
    }, token);
    await loadTokens();
  };

  const revokeToken = async (keyId) => {
    await api('/dashboard/admin/accounts/keys/revoke', {
      method: 'POST', body: JSON.stringify({ key_id: keyId }),
    }, token);
    await loadTokens();
    refreshTab();
  };

  const toggleStatus = () => run('status', async () => {
    await api('/dashboard/admin/accounts/toggle', {
      method: 'POST', body: JSON.stringify({ name, enabled: !account.enabled }),
    }, token);
    refreshTab();
    notify(account.enabled ? `Đã khoá ${name}` : `Đã mở khoá ${name}`, 'success');
  });

  // A flag only — the stored password is untouched, so the admin needs no
  // current password to press it.
  const toggleMustChange = () => run('mustchange', async () => {
    await api('/dashboard/admin/accounts/require-password-change', {
      method: 'POST', body: JSON.stringify({ name, required: !account.must_change_password }),
    }, token);
    refreshTab();
  });

  const copyMasterKey = async () => {
    // The list endpoint ships a mask, not the key: it is polled every few
    // seconds and a screenshot of this tab would otherwise be a list of
    // quota-exempt credentials. The real value is fetched by name, on demand.
    try {
      const res = await api(`/dashboard/admin/accounts/master-key?name=${encodeURIComponent(name)}`, {}, token);
      if (!res?.available || !res?.auth_key) {
        notify(res?.reason || 'Tài khoản này không có master key.', 'error');
        return;
      }
      onSecret({ title: `Master Key của ${name}`, value: res.auth_key });
      await navigator.clipboard.writeText(res.auth_key);
      notify('Đã copy master key vào clipboard.', 'success');
    } catch {
      notify('Trình duyệt chặn clipboard.', 'error');
    }
  };

  const rotateKey = () => {
    if (!window.confirm(`Cấp mới Master Key cho "${name}"? Key cũ bị vô hiệu hoá ngay lập tức.`)) return;
    run('rotate', async () => {
      const res = await api('/dashboard/admin/accounts/rotate-key', {
        method: 'POST', body: JSON.stringify({ name }),
      }, token);
      refreshTab();
      if (res?.account?.auth_key) onSecret({ title: `Master Key mới của ${name}`, value: res.account.auth_key });
    });
  };

  const deleteAccount = () => {
    if (!window.confirm(`Xoá vĩnh viễn "${name}"? Toàn bộ thống kê và token của tài khoản sẽ mất.`)) return;
    run('delete', async () => {
      await api('/dashboard/admin/accounts/delete', {
        method: 'POST', body: JSON.stringify({ name }),
      }, token);
      refreshTab();
      onDeleted();
      notify(`Đã xoá ${name}`, 'success');
    });
  };

  return (
    <div className="space-y-4 animate-fade-in-up">
      {/* Identity + primary actions */}
      <section className="card glass-card rounded-2xl p-5 text-left border border-base-content/5">
        <div className="flex flex-col sm:flex-row sm:items-center gap-4">
          <div className="w-14 h-14 shrink-0 bg-primary/10 text-primary text-xl font-black rounded-full flex items-center justify-center border border-primary/20 uppercase">
            {name.substring(0, 2)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-lg font-black truncate">{name}</h2>
              <span className={`badge badge-sm text-[10px] font-extrabold uppercase ${TIER_BADGE[account.tier] || TIER_BADGE.free}`}>
                {account.tier}
              </span>
              <span className={`badge badge-sm text-[10px] font-bold ${account.enabled ? 'badge-success badge-outline' : 'badge-ghost text-base-content/50'}`}>
                {account.enabled ? 'Hoạt động' : 'Đã khoá'}
              </span>
            </div>
            <p className="text-[11px] text-base-content/45 font-mono mt-1 truncate">
              {account.account_id} · tạo {fmtD(account.created_at)}
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            <button onClick={() => setIsEditOpen(true)} className="btn btn-sm btn-outline gap-1.5 font-bold">
              <Edit className="w-3.5 h-3.5" /> Sửa hạn mức
            </button>
            <button
              onClick={toggleStatus}
              disabled={busy === 'status'}
              className={`btn btn-sm gap-1.5 font-bold ${account.enabled ? 'btn-ghost text-error hover:bg-error/15' : 'btn-success'}`}
            >
              {account.enabled ? <ShieldAlert className="w-3.5 h-3.5" /> : <ShieldCheck className="w-3.5 h-3.5" />}
              {account.enabled ? 'Khoá' : 'Mở khoá'}
            </button>
          </div>
        </div>
      </section>

      <Section icon={Gauge} title="Hạn mức tài khoản" hint="Trần áp cho toàn bộ token của tài khoản này.">
        <div className="grid grid-cols-3 gap-3">
          <LimitTile label="RPM" value={(account.rpm || 0).toLocaleString()} />
          <LimitTile label="TPM" value={fmt(account.tpm || 0)} />
          <LimitTile label="RPD" value={(account.rpd || 0).toLocaleString()} />
        </div>
      </Section>

      <Section
        icon={KeyRound}
        title={`Auth tokens (${tokensLoading ? '…' : tokens.length})`}
        hint="Mỗi token có hạn mức riêng. Admin được nới trần; user chỉ siết chặt hơn hạn mức tài khoản."
        tone="border-primary/25"
      >
        <TokenTable
          tokens={tokens}
          loading={tokensLoading}
          scope="admin"
          accountTier={account.tier}
          accountName={name}
          onIssue={issueToken}
          onUpdate={updateToken}
          onRevoke={revokeToken}
        />
      </Section>

      <Section icon={Lock} title="Bảo mật & đăng nhập" hint="Mật khẩu web và master key (đường vào không bị rate limit).">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="rounded-xl bg-base-200/30 border border-base-content/5 p-4 space-y-3">
            <div className="text-[11px] font-bold uppercase tracking-wider text-base-content/50">Mật khẩu web</div>
            <PasswordReveal token={token} accountName={name} />
            <button
              onClick={toggleMustChange}
              disabled={busy === 'mustchange'}
              className={`btn btn-xs gap-1 font-bold normal-case ${account.must_change_password ? 'btn-warning' : 'btn-ghost border border-base-content/15'}`}
              title="Chỉ ghi cờ, không đụng mật khẩu hiện tại"
            >
              <KeySquare className="w-3 h-3" />
              {account.must_change_password ? 'Đang yêu cầu đổi MK — bấm để bỏ' : 'Yêu cầu đổi MK ở lần đăng nhập tới'}
            </button>
          </div>

          <div className="rounded-xl bg-base-200/30 border border-base-content/5 p-4 space-y-3">
            <div className="text-[11px] font-bold uppercase tracking-wider text-base-content/50">Master key</div>
            <code className="block text-xs font-semibold select-all break-all text-base-content/70">
              {account.auth_key_masked || '—'}
            </code>
            <div className="flex flex-wrap gap-2">
              <button onClick={copyMasterKey} className="btn btn-xs btn-ghost border border-base-content/15 gap-1 font-bold normal-case">
                <Copy className="w-3 h-3" /> Copy
              </button>
              <button
                onClick={rotateKey}
                disabled={busy === 'rotate'}
                className="btn btn-xs btn-ghost border border-base-content/15 gap-1 font-bold normal-case"
              >
                <RefreshCw className="w-3 h-3" /> Cấp mới
              </button>
            </div>
            <p className="text-[10px] text-base-content/45">Cấp mới sẽ vô hiệu hoá key cũ ngay lập tức.</p>
          </div>
        </div>
      </Section>

      <Section icon={Trash2} title="Vùng nguy hiểm" tone="border-error/25">
        <div className="flex items-center justify-between gap-4">
          <p className="text-[11px] text-base-content/55">Xoá tài khoản kéo theo toàn bộ thống kê sử dụng và token của nó.</p>
          <button onClick={deleteAccount} disabled={busy === 'delete'} className="btn btn-sm btn-error btn-outline gap-1.5 font-bold shrink-0">
            <Trash2 className="w-3.5 h-3.5" /> Xoá tài khoản
          </button>
        </div>
      </Section>

      <EditAccountModal
        account={account}
        isOpen={isEditOpen}
        onClose={() => setIsEditOpen(false)}
        onSaveSuccess={refreshTab}
      />
    </div>
  );
}
