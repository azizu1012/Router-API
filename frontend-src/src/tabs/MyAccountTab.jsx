import { Fragment, useState, useEffect, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { api } from '../utils/api';
import { Zap, Activity, KeyRound } from 'lucide-react';
import Card from '../components/Card';
import Badge from '../components/Badge';
import Alert from '../components/Alert';
import Button from '../components/Button';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import TokenTable from '../components/TokenTable';

export default function MyAccountTab() {
  const { tabData, token, lang, refreshTab, wsHook, logout } = useApp();
  const data = tabData.myacc;
  const [liveActivity, setLiveActivity] = useState(null);
  // Declared here (not next to the effect that sets it) so it can never be
  // dropped again by someone deleting an unrelated block nearby.
  const [resetCountdown, setResetCountdown] = useState('');

  // ── Change Password ──
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [pwdMsg, setPwdMsg] = useState(null);

  const handleChangePassword = async (e) => {
    e.preventDefault();
    setPwdMsg(null);
    if (!currentPassword || !newPassword) {
      setPwdMsg({ text: t('err_pw_fields', lang), type: 'error' });
      return;
    }
    try {
      await api('/dashboard/password', {
        method: 'POST',
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
      }, token);
      setPwdMsg({ text: t('msg_pw_changed', lang), type: 'success' });
      setCurrentPassword('');
      setNewPassword('');
      setIsChangingPassword(false);
      setTimeout(() => logout(), 2000);
    } catch (err) {
      setPwdMsg({ text: err.message, type: 'error' });
    }
  };

  // ── auth tokens ──
  const [tokens, setTokens] = useState([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [tokenMsg, setTokenMsg] = useState(null);

  /* `isCurrent` lets the effect drop a response that lands after the token has
     already changed; the issue/revoke handlers pass nothing and get the plain
     behaviour. */
  const loadTokens = useCallback(async (isCurrent = () => true) => {
    setTokensLoading(true);
    try {
      const res = await api('/dashboard/my/keys', {}, token);
      if (!isCurrent()) return;
      setTokens(res?.keys || []);
    } catch (e) {
      if (!isCurrent()) return;
      setTokens([]);
      setTokenMsg({ text: e.message, type: 'error' });
    } finally {
      if (isCurrent()) setTokensLoading(false);
    }
  }, [token]);

  useEffect(() => {
    let alive = true;
    (async () => { await loadTokens(() => alive); })();
    return () => { alive = false; };
  }, [loadTokens]);

  const handleIssueToken = async (payload) => {
    setTokenMsg(null);
    try {
      const res = await api('/dashboard/my/keys/create', {
        method: 'POST',
        body: JSON.stringify(payload)
      }, token);
      await loadTokens();
      refreshTab();
      if (res?.token) {
        window.prompt(t('msg_token_once', lang), res.token);
      }
      setTokenMsg({ text: t('msg_token_created', lang), type: 'success' });
    } catch (e) {
      setTokenMsg({ text: e.message, type: 'error' });
      throw e;
    }
  };

  const handleUpdateToken = async (keyId, payload) => {
    setTokenMsg(null);
    try {
      await api('/dashboard/my/keys/update', {
        method: 'POST',
        body: JSON.stringify({ key_id: keyId, ...payload })
      }, token);
      await loadTokens();
      setTokenMsg({ text: t('msg_token_updated', lang), type: 'success' });
    } catch (e) {
      setTokenMsg({ text: e.message, type: 'error' });
      throw e;
    }
  };

  const handleRevokeToken = async (keyId) => {
    setTokenMsg(null);
    try {
      await api('/dashboard/my/keys/revoke', {
        method: 'POST',
        body: JSON.stringify({ key_id: keyId })
      }, token);
      await loadTokens();
      refreshTab();
      setTokenMsg({ text: t('msg_token_revoked', lang), type: 'success' });
    } catch (e) {
      setTokenMsg({ text: e.message, type: 'error' });
      throw e;
    }
  };

  useEffect(() => {
    if (!wsHook || !wsHook.connected) return;
    const unsub = wsHook.subscribe('stats:overview', (msg) => {
      if (msg.type === 'stats_snapshot') setLiveActivity(msg);
    });
    return unsub;
  }, [wsHook, wsHook?.connected]);


  useEffect(() => {
    const updateCountdown = () => {
      const now = new Date();
      const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
      const diffMs = tomorrow - now;
      const hrs = Math.floor(diffMs / 3600000);
      const mins = Math.floor((diffMs % 3600000) / 60000);
      const secs = Math.floor((diffMs % 60000) / 1000);
      setResetCountdown(`${hrs}h ${mins}m ${secs}s`);
    };
    updateCountdown();
    const interval = setInterval(updateCountdown, 1000);
    return () => clearInterval(interval);
  }, []);

  if (!data) return <Loading message={t('loading', lang)} />;

  const tier = data.tier || 'free';
  const flash = data.flash || { rpm: 0, tpm: 0, rpd: 0, rpm_used: 0, tpm_used: 0, rpd_used: 0, rpm_left: 0, tpm_left: 0, rpd_left: 0 };
  const lite = data.lite || { rpm: 0, tpm: 0, rpd: 0, rpm_used: 0, tpm_used: 0, rpd_used: 0, rpm_left: 0, tpm_left: 0, rpd_left: 0 };

  const pct = (used, lim) => Math.min(100, Math.round(((used || 0) / (lim || 1)) * 100)) || 0;

  const fRpm = pct(flash.rpm_used, flash.rpm);
  const fTpm = pct(flash.tpm_used, flash.tpm);
  const fRpd = pct(flash.rpd_used, flash.rpd);

  const lRpm = pct(lite.rpm_used, lite.rpm);
  const lTpm = pct(lite.tpm_used, lite.tpm);
  const lRpd = pct(lite.rpd_used, lite.rpd);

  const limitCard = (title, val, used, percent, leftText, showReset) => {
    const color = percent < 50 ? 'var(--c-good)' : percent < 80 ? 'var(--c-warn)' : 'var(--c-critical)';

    return (
      <Card padding="md">
        <div className="flex items-baseline justify-between gap-2 mb-1.5">
          <span className="label-micro">{title}</span>
          <span className="text-xs font-medium tabular-nums" style={{ color }}>{val}</span>
        </div>
        <div className="meter" role="meter" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}
             aria-label={`${title} used ${percent}%`}>
          <span style={{ width: `${percent}%`, background: color }} />
        </div>
        <div className="flex justify-between items-center text-[0.6875rem] mt-2" style={{ color: 'var(--c-ink-3)' }}>
          <span>{t('lbl_left', lang)}: <span className="tabular-nums" style={{ color }}>{leftText}</span></span>
          <span>{t('lbl_using', lang)}: <span className="tabular-nums">{percent}%</span></span>
        </div>
        {showReset && (
          <div className="flex justify-between text-[0.6875rem] pt-2 mt-2"
               style={{ borderTop: '1px solid var(--c-line)', color: 'var(--c-ink-3)' }}>
            <span>{t('lbl_reset', lang)}</span>
            <span className="tabular-nums" style={{ color: 'var(--c-ink-2)' }}>{resetCountdown}</span>
          </div>
        )}
      </Card>
    );
  };

  const poolSection = (label, colorClass, icon, poolData) => {
    if (!poolData) return null;
    return (
      <Card padding="md">
        <Card.Header
          title={label}
          action={<Badge variant="success" size="xs">{t('opt_active', lang)}</Badge>}
        />
        <div className="space-y-0.5 text-xs">
          <Card.Row label={t('lbl_pool_rpd', lang)} value={fmt(poolData.rpd_left)} color={colorClass} />
          <Card.Row label={t('lbl_pool_1h', lang)} value={fmt(poolData.tokens_1h_left)} color="var(--c-good)" />
          <Card.Row label={t('lbl_pool_reset_time', lang)} value={resetCountdown} color="var(--c-warn)" />
          <Card.Row label={t('lbl_pool_24h', lang)} value={fmt(poolData.tokens_24h_left)} color="var(--c-accent)" />
        </div>
      </Card>
    );
  };

  const tierBadge = {
    admin: <Badge variant="primary" size="xs">{tier}</Badge>,
    premium: <Badge variant="primary" size="xs">{tier}</Badge>,
  }[tier] || <Badge variant="ghost" size="xs">{tier}</Badge>;

  /* The bar and the number must read the same way. Both now show headroom
     (what is left), and the status colour flags a pool running dry — the old
     version filled by consumption while printing the remaining count, so a
     nearly-empty bar sat beside a nearly-full number. */
  const poolGauge = (label, remain, limit, unit) => {
    const left = limit > 0 ? Math.max(0, Math.min(100, Math.round((remain / limit) * 100))) : 0;
    const color = left < 15 ? 'var(--c-critical)' : left < 40 ? 'var(--c-warn)' : 'var(--c-good)';
    return (
      <div>
        <div className="flex items-baseline justify-between gap-2 mb-1.5">
          <span className="label-micro">{label}</span>
          <span className="text-xs font-medium tabular-nums" style={{ color: 'var(--c-ink-2)' }}>
            {remain.toLocaleString()}
            <span style={{ color: 'var(--c-ink-3)' }}> / {limit.toLocaleString()} {unit} left</span>
          </span>
        </div>
        <div className="meter" role="meter" aria-valuenow={left} aria-valuemin={0} aria-valuemax={100}
             aria-label={`${label} headroom ${left}%`}>
          <span style={{ width: `${left}%`, background: color }} />
        </div>
      </div>
    );
  };

  const livePools = (() => {
    if (!liveActivity || !liveActivity.models) return null;
    const models = liveActivity.models;
    const poolGroups = liveActivity.pools || {
      'gemini-flash': ['gemini-flash-36', 'gemini-flash-30', 'gemini-flash-38'],
      'gemini-flash-lite': ['gemini-flash-35-lite', 'gemini-flash-31-lite', 'gemini-flash-25-lite'],
    };
    const poolMemberSet = new Set(Object.values(poolGroups).flat());

    const sumMembers = (memberList) => {
      const acc = { rpm_rem: 0, rpm_lim: 0, tpm_rem: 0, tpm_lim: 0, cnt: 0 };
      for (const alias of memberList) {
        const ms = models[alias];
        if (!ms) continue;
        acc.rpm_rem += ms.rpm_remaining || 0;
        acc.rpm_lim += ms.rpm_limit || 0;
        acc.tpm_rem += ms.tpm_remaining || 0;
        acc.tpm_lim += ms.tpm_limit || 0;
        acc.cnt++;
      }
      return acc;
    };

    const flash = sumMembers(poolGroups['gemini-flash'] || []);
    const lite = sumMembers(poolGroups['gemini-flash-lite'] || []);
    const cards = [];
    if (flash.cnt > 0) cards.push({ label: 'Flash 3 Pool', color: 'var(--c-accent)', ...flash });
    if (lite.cnt > 0) cards.push({ label: 'Lite Pool', color: 'var(--s-3)', ...lite });

    const standaloneAliases = Array.isArray(liveActivity.standalone)
      ? liveActivity.standalone
      : Object.keys(models).filter(a => !poolGroups[a] && !poolMemberSet.has(a));
    const standaloneCards = standaloneAliases
      .filter(alias => Boolean(models[alias]))
      .map(alias => ({ alias, ...models[alias] }));

    if (cards.length === 0 && standaloneCards.length === 0) return null;
    return (
      <div className="space-y-3">
        {cards.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {cards.map(({ label, color, rpm_rem, rpm_lim, tpm_rem, tpm_lim, cnt }) => (
              <Card key={label} variant="glass" padding="md">
                <Card.Header
                  title={<span style={{ color }}>{label}</span>}
                  action={<span className="text-[0.6875rem] tabular-nums" style={{ color: 'var(--c-ink-3)' }}>{cnt} {t('lbl_models', lang)}</span>}
                />
                <div className="space-y-2 pt-1">
                  {poolGauge('RPM Live', rpm_rem, rpm_lim, '')}
                  {poolGauge('TPM Live', tpm_rem, tpm_lim, '')}
                </div>
              </Card>
            ))}
          </div>
        )}
        {standaloneCards.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {standaloneCards.map(m => (
              <Card key={m.alias} variant="glass" padding="sm">
                <Card.Header
                  title={<span className="font-mono text-xs" style={{ color: 'var(--c-ink)' }}>{m.alias}</span>}
                  action={<span className="text-[0.625rem]" style={{ color: 'var(--c-accent)' }}>{t('ov_standalone_badge', lang)}</span>}
                />
                <div className="space-y-2 pt-1">
                  {poolGauge('RPM Live', m.rpm_remaining || 0, m.rpm_limit || 0, '')}
                  {(m.tpm_limit || 0) > 0 && (m.tpm_limit || 0) < 999999999 && poolGauge('TPM Live', m.tpm_remaining || 0, m.tpm_limit || 0, '')}
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
    );
  })();

  return (
    <div className="space-y-5">
      <PageHeader title={t('nav_myacc', lang)} subtitle={t('lbl_key_pools_sub', lang)} />

      <Card padding="md">
        <div className="flex flex-wrap items-center gap-4">
          <div className="w-11 h-11 rounded-lg flex items-center justify-center text-sm font-semibold uppercase shrink-0"
               style={{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }}>
            {(data.name || '??').substring(0, 2).toUpperCase()}
          </div>
          <div className="flex-1 min-w-0 space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-base font-semibold" style={{ color: 'var(--c-ink)' }}>{data.name}</h2>
              {tierBadge}
            </div>
            <p className="text-xs font-mono" style={{ color: 'var(--c-ink-3)' }}>
              {t('lbl_account_id', lang)}: <span style={{ color: 'var(--c-ink-2)' }}>{data.account_id || '—'}</span>
            </p>
          </div>
          <Button size="sm" variant="outline" className="ml-auto shrink-0"
                  onClick={() => setIsChangingPassword(!isChangingPassword)}>
            {t('lbl_change_pw', lang)}
          </Button>
        </div>

        {isChangingPassword && (
          <form onSubmit={handleChangePassword} className="mt-4 pt-4 max-w-sm animate-fade-in-up"
                style={{ borderTop: '1px solid var(--c-line)' }}>
            <h3 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink)' }}>
              {t('lbl_change_pw_title', lang)}
            </h3>
            <div className="space-y-2.5">
              <input
                type="password"
                placeholder={t('ph_current_pw', lang)}
                value={currentPassword}
                onChange={e => setCurrentPassword(e.target.value)}
                className="input w-full text-sm"
                autoComplete="current-password"
              />
              <input
                type="password"
                placeholder={t('ph_new_pw', lang)}
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                className="input w-full text-sm"
                autoComplete="new-password"
              />
              <div className="flex items-center gap-2">
                <Button type="submit" size="sm">{t('btn_confirm', lang)}</Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setIsChangingPassword(false)}>
                  {t('btn_cancel', lang)}
                </Button>
              </div>
            </div>
          </form>
        )}

        {pwdMsg && <div className="mt-3"><Alert type={pwdMsg.type} message={pwdMsg.text} /></div>}
      </Card>

      {/* Auth tokens — the user-facing half of the token refactor */}
      <Card padding="md">
        <div className="mb-3">
          <h3 className="text-xs font-semibold flex items-center gap-2" style={{ color: 'var(--c-ink)' }}>
            <KeyRound className="w-3.5 h-3.5" style={{ color: 'var(--c-ink-3)' }} />
            {t('lbl_my_tokens', lang)}
          </h3>
          <p className="text-[0.6875rem] mt-1 leading-relaxed" style={{ color: 'var(--c-ink-3)' }}>
            {t('lbl_my_tokens_desc', lang)}
          </p>
        </div>

        {tokenMsg && <div className="mb-3"><Alert type={tokenMsg.type} message={tokenMsg.text} /></div>}

        <TokenTable
          tokens={tokens}
          loading={tokensLoading}
          scope="user"
          accountTier={data?.tier || 'free'}
          accountName={data.name}
          onIssue={handleIssueToken}
          onUpdate={handleUpdateToken}
          onRevoke={handleRevokeToken}
        />
      </Card>

      {wsHook.connected && liveActivity && (
        <Card padding="sm">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
            <span className="flex items-center gap-1.5 label-micro">
              <span className="w-1.5 h-1.5 rounded-full animate-breathing" style={{ background: 'var(--c-good)' }} />
              Live
            </span>
            {[
              ['Active Keys', liveActivity.active_keys, 'var(--c-ink)'],
              ['Connections', liveActivity.connections, 'var(--c-ink)'],
              ['429', liveActivity.rate_limits_429 || 0, 'var(--c-warn)'],
              ['503', liveActivity.unavailable_503 || 0, 'var(--c-warn)'],
              ['Penalties', liveActivity.penalties, 'var(--c-critical)'],
            ].map(([label, value, color]) => (
              <span key={label} className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
                {label}{' '}
                <span className="font-medium tabular-nums" style={{ color }}>{value}</span>
              </span>
            ))}
          </div>
        </Card>
      )}

      {livePools}

      {[
        { label: 'Gemini Flash Pool', pool: flash, p: [fRpm, fTpm, fRpd] },
        { label: 'Gemini Flash Lite Pool', pool: lite, p: [lRpm, lTpm, lRpd] },
      ].map(({ label, pool, p }) => (
        <Fragment key={label}>
          <div className="text-left">
            <h3 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>
              {label} — {t('lbl_usage_limits', lang)}
            </h3>
            <p className="text-[0.6875rem] mt-0.5" style={{ color: 'var(--c-ink-3)' }}>
              {t('lbl_usage_limits_sub', lang)}
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {limitCard(t('lbl_rpm_card', lang), `${pool.rpm_used} / ${(pool.rpm || 0).toLocaleString()}`, pool.rpm_used, p[0], pool.rpm_left.toLocaleString(), false)}
            {limitCard(t('lbl_tpm_card', lang), `${fmt(pool.tpm_used)} / ${fmt(pool.tpm || 0)}`, pool.tpm_used, p[1], fmt(pool.tpm_left), false)}
            {limitCard(t('lbl_rpd_card', lang), `${pool.rpd_used} / ${fmt(pool.rpd || 0)}`, pool.rpd_used, p[2], pool.rpd_left.toLocaleString(), true)}
          </div>
        </Fragment>
      ))}

      <div className="text-left pt-1">
        <h3 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{t('lbl_key_pools', lang)}</h3>
        <p className="text-[0.6875rem] mt-0.5" style={{ color: 'var(--c-ink-3)' }}>{t('lbl_key_pools_sub', lang)}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {poolSection('Gemini Flash Pool', 'var(--c-accent)', <Zap className="w-4 h-4" />, data.flash_pool)}
        {poolSection('Gemini Flash Lite Pool', 'var(--s-3)', <Activity className="w-4 h-4" />, data.lite_pool)}
      </div>
    </div>
  );
}
