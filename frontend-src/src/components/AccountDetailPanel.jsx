import { useState, useEffect, useCallback } from 'react';
import { api } from '../utils/api';
import { fmt, fmtD } from '../utils/format';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import EditAccountModal from './EditAccountModal';
import TokenTable from './TokenTable';
import PasswordReveal from './PasswordReveal';
import Card from './Card';
import Badge from './Badge';
import Button from './Button';
import { copyText } from '../utils/clipboard';
import {
  ShieldCheck, ShieldAlert, KeyRound, KeySquare, Edit, Copy, RefreshCw, Trash2, Gauge, Lock,
} from 'lucide-react';

const TIER_VARIANT = { admin: 'primary', premium: 'info', free: 'ghost' };

/* One titled block. The rule under the title is the only divider in the panel,
   so the four sections read as one column of cards rather than a stack of
   unrelated boxes. */
function Section({ icon: Icon, title, hint, children, tone }) {
  return (
    <Card padding="lg">
      <div className="flex items-start gap-2 mb-4 pb-2.5" style={{ borderBottom: '1px solid var(--c-line)' }}>
        {Icon && <Icon className="w-4 h-4 shrink-0 mt-px" style={{ color: tone || 'var(--c-ink-3)' }} />}
        <div className="min-w-0">
          <h3 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{title}</h3>
          {hint && (
            <p className="text-[0.6875rem] leading-snug mt-0.5" style={{ color: 'var(--c-ink-3)' }}>{hint}</p>
          )}
        </div>
      </div>
      {children}
    </Card>
  );
}

function LimitTile({ label, value }) {
  return (
    <div className="rounded-lg px-3 py-2.5"
         style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
      <div className="label-micro">{label}</div>
      <div className="text-lg font-semibold tabular-nums mt-1 leading-none" style={{ color: 'var(--c-ink)' }}>
        {value}
      </div>
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
  const { lang } = useApp();
  const [tokens, setTokens] = useState([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [busy, setBusy] = useState(null);

  const name = account?.name || '';

  /* `isCurrent` guards only the effect's own call: switching accounts mid-fetch
     must not paint the previous account's keys. The issue/update/revoke handlers
     call it bare and want the plain behaviour. */
  const loadTokens = useCallback(async (isCurrent = () => true) => {
    setTokensLoading(true);
    try {
      const res = await api('/dashboard/admin/accounts/keys', {
        method: 'POST',
        body: JSON.stringify({ name }),
      }, token);
      if (!isCurrent()) return;
      setTokens(res?.keys || []);
    } catch (e) {
      if (!isCurrent()) return;
      setTokens([]);
      notify(`${t('ac_tokens_load_err', lang)}${e.message}`, 'error');
    } finally {
      if (isCurrent()) setTokensLoading(false);
    }
  }, [name, token, lang, notify]);

  useEffect(() => {
    let alive = true;
    (async () => { await loadTokens(() => alive); })();
    return () => { alive = false; };
  }, [loadTokens]);

  const run = async (key, fn) => {
    setBusy(key);
    try { await fn(); } catch (e) { notify(`${t('ac_err', lang)}${e.message}`, 'error'); } finally { setBusy(null); }
  };

  const issueToken = async (payload) => {
    const res = await api('/dashboard/admin/accounts/keys/issue', {
      method: 'POST', body: JSON.stringify({ name, ...payload }),
    }, token);
    await loadTokens();
    refreshTab();
    if (res?.token) {
      onSecret({ title: t('ac_token_of', lang).replace('{name}', name), value: res.token });
    }
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
    const key = account.enabled ? 'ac_lock_done' : 'ac_unlock_done';
    notify(t(key, lang).replace('{name}', name), 'success');
  });

  // A flag only — the stored password is untouched, so the admin needs no
  // current password to press it.
  const toggleMustChange = () => run('mustchange', async () => {
    await api('/dashboard/admin/accounts/require-password-change', {
      method: 'POST', body: JSON.stringify({ name, required: !account.must_change_password }),
    }, token);
    refreshTab();
    notify(t(account.must_change_password ? 'ac_flag_cleared' : 'ac_flag_required', lang), 'success');
  });

  const copyMasterKey = async () => {
    // The list endpoint ships a mask, not the key: it is polled every few
    // seconds and a screenshot of this tab would otherwise be a list of
    // quota-exempt credentials. The real value is fetched by name, on demand.
    try {
      const res = await api(`/dashboard/admin/accounts/master-key?name=${encodeURIComponent(name)}`, {}, token);
      if (!res?.available || !res?.auth_key) {
        notify(res?.reason || t('ac_no_master_key', lang), 'error');
        return;
      }
      onSecret({ title: t('ac_master_key_of', lang).replace('{name}', name), value: res.auth_key });
      if (await copyText(res.auth_key)) {
        notify(t('ac_master_copied', lang), 'success');
      } else {
        notify(t('ac_copy_blocked', lang), 'error');
      }
    } catch (e) {
      notify(`${t('ac_err', lang)}${e.message}`, 'error');
    }
  };

  const rotateKey = () => {
    if (!window.confirm(t('ac_rotate_confirm', lang).replace('{name}', name))) return;
    run('rotate', async () => {
      const res = await api('/dashboard/admin/accounts/rotate-key', {
        method: 'POST', body: JSON.stringify({ name }),
      }, token);
      refreshTab();
      if (res?.account?.auth_key) {
        onSecret({ title: t('ac_new_master_key_of', lang).replace('{name}', name), value: res.account.auth_key });
      }
    });
  };

  const deleteAccount = () => {
    if (!window.confirm(t('ac_delete_confirm', lang).replace('{name}', name))) return;
    run('delete', async () => {
      await api('/dashboard/admin/accounts/delete', {
        method: 'POST', body: JSON.stringify({ name }),
      }, token);
      refreshTab();
      onDeleted();
      notify(t('ac_deleted', lang).replace('{name}', name), 'success');
    });
  };

  return (
    <div className="space-y-4 animate-fade-in-up">
      {/* Identity + primary actions */}
      <Card padding="lg">
        <div className="flex flex-col sm:flex-row sm:items-center gap-4">
          <div className="w-12 h-12 shrink-0 rounded-xl flex items-center justify-center text-base font-semibold uppercase"
               style={{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }}>
            {(name || '?').substring(0, 2)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-semibold truncate" style={{ color: 'var(--c-ink)' }}>{name}</h2>
              <Badge variant={TIER_VARIANT[account.tier] || 'ghost'} size="sm">{account.tier}</Badge>
              <Badge variant={account.enabled ? 'success' : 'ghost'} size="sm" dot={account.enabled}>
                {account.enabled ? t('ac_status_active', lang) : t('ac_status_disabled', lang)}
              </Badge>
              {account.must_change_password && (
                <Badge variant="warning" size="sm" icon={KeySquare}>{t('ac_req_pw_change', lang)}</Badge>
              )}
            </div>
            <p className="text-[0.6875rem] font-mono truncate mt-1" style={{ color: 'var(--c-ink-3)' }}>
              {account.account_id}
              {account.created_at ? ` · ${t('ac_created_at', lang).replace('{d}', fmtD(account.created_at))}` : ''}
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            <Button variant="outline" icon={Edit} onClick={() => setIsEditOpen(true)}>
              {t('ac_edit_limits', lang)}
            </Button>
            <Button
              variant={account.enabled ? 'ghost' : 'success'}
              icon={account.enabled ? ShieldAlert : ShieldCheck}
              onClick={toggleStatus}
              loading={busy === 'status'}
              style={account.enabled ? { color: 'var(--c-critical)' } : undefined}
            >
              {account.enabled ? t('ac_lock', lang) : t('ac_unlock', lang)}
            </Button>
          </div>
        </div>
      </Card>

      <Section icon={Gauge} title={t('ac_limits_title', lang)} hint={t('ac_limits_hint', lang)}>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <LimitTile label="RPM" value={(account.rpm || 0).toLocaleString()} />
          <LimitTile label="TPM" value={fmt(account.tpm || 0)} />
          <LimitTile label="RPD" value={(account.rpd || 0).toLocaleString()} />
        </div>
      </Section>

      <Section
        icon={KeyRound}
        title={t('ac_tokens_title', lang).replace('{n}', tokensLoading ? '…' : tokens.length)}
        hint={t('ac_tokens_hint', lang)}
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

      <Section icon={Lock} title={t('ac_security_title', lang)} hint={t('ac_security_hint', lang)}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="rounded-lg p-4 space-y-3"
               style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
            <div className="label-micro">{t('ac_web_pw', lang)}</div>
            <PasswordReveal token={token} accountName={name} />
            <Button
              size="xs"
              variant={account.must_change_password ? 'danger' : 'ghost'}
              icon={KeySquare}
              onClick={toggleMustChange}
              loading={busy === 'mustchange'}
              title={t('ac_pw_flag_title', lang)}
              className="max-w-full"
            >
              <span className="truncate">
                {account.must_change_password ? t('ac_pw_flag_on', lang) : t('ac_pw_flag_off', lang)}
              </span>
            </Button>
          </div>

          <div className="rounded-lg p-4 space-y-3"
               style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
            <div className="label-micro">{t('ac_master_key', lang)}</div>
            <code className="block text-[0.6875rem] font-mono select-all break-all"
                  style={{ color: 'var(--c-ink-3)' }}>
              {account.auth_key_masked || '—'}
            </code>
            <div className="flex flex-wrap gap-2">
              <Button size="xs" variant="ghost" icon={Copy} onClick={copyMasterKey}>{t('ac_copy', lang)}</Button>
              <Button size="xs" variant="ghost" icon={RefreshCw} onClick={rotateKey} loading={busy === 'rotate'}>
                {t('ac_rotate', lang)}
              </Button>
            </div>
            <p className="text-[0.6875rem] leading-snug" style={{ color: 'var(--c-ink-3)' }}>
              {t('ac_rotate_hint', lang)}
            </p>
          </div>
        </div>
      </Section>

      <Section icon={Trash2} title={t('ac_danger_title', lang)} tone="var(--c-critical)">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <p className="text-[0.6875rem] leading-relaxed" style={{ color: 'var(--c-ink-2)' }}>
            {t('ac_danger_body', lang)}
          </p>
          <Button variant="danger" size="sm" icon={Trash2} onClick={deleteAccount} loading={busy === 'delete'}
                  className="shrink-0 sm:self-auto self-start">
            {t('ac_delete', lang)}
          </Button>
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