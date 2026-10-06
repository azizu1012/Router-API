import React, { useState, useEffect, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { api } from '../utils/api';
import { Wifi, Zap, Activity, HelpCircle, KeyRound } from 'lucide-react';
import Card from '../components/Card';
import Badge from '../components/Badge';
import Loading from '../components/Loading';
import TokenTable from '../components/TokenTable';

export default function MyAccountTab() {
  const { tabData, token, lang, refreshTab, wsHook, user, logout } = useApp();
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
      setPwdMsg({ text: '⚠️ Vui lòng nhập đủ thông tin', type: 'error' });
      return;
    }
    try {
      await api('/dashboard/password', {
        method: 'POST',
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
      }, token);
      setPwdMsg({ text: '✅ Đổi mật khẩu thành công. Vui lòng đăng nhập lại.', type: 'success' });
      setCurrentPassword('');
      setNewPassword('');
      setIsChangingPassword(false);
      setTimeout(() => logout(), 2000);
    } catch (err) {
      setPwdMsg({ text: '❌ ' + err.message, type: 'error' });
    }
  };

  // ── auth tokens ──
  const [tokens, setTokens] = useState([]);
  const [tokensLoading, setTokensLoading] = useState(true);
  const [tokenMsg, setTokenMsg] = useState(null);

  const loadTokens = useCallback(async () => {
    setTokensLoading(true);
    try {
      const res = await api('/dashboard/my/keys', {}, token);
      setTokens(res?.keys || []);
    } catch (e) {
      setTokens([]);
      setTokenMsg({ text: '❌ ' + e.message, type: 'error' });
    } finally {
      setTokensLoading(false);
    }
  }, [token]);

  useEffect(() => { loadTokens(); }, [loadTokens]);

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
        window.prompt('Token mới — copy ngay, chỉ hiện một lần:', res.token);
      }
      setTokenMsg({ text: '✅ Đã tạo token mới.', type: 'success' });
    } catch (e) {
      setTokenMsg({ text: '❌ ' + e.message, type: 'error' });
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
      setTokenMsg({ text: '✅ Đã cập nhật giới hạn token.', type: 'success' });
    } catch (e) {
      setTokenMsg({ text: '❌ ' + e.message, type: 'error' });
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
      setTokenMsg({ text: '🗑 Đã thu hồi token.', type: 'success' });
    } catch (e) {
      setTokenMsg({ text: '❌ ' + e.message, type: 'error' });
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
  const barCol = p => p < 50 ? 'bg-success' : p < 80 ? 'bg-warning' : 'bg-error';
  const textCol = p => p < 50 ? 'text-success' : p < 80 ? 'text-warning' : 'text-error';

  const fRpm = pct(flash.rpm_used, flash.rpm);
  const fTpm = pct(flash.tpm_used, flash.tpm);
  const fRpd = pct(flash.rpd_used, flash.rpd);

  const lRpm = pct(lite.rpm_used, lite.rpm);
  const lTpm = pct(lite.tpm_used, lite.tpm);
  const lRpd = pct(lite.rpd_used, lite.rpd);

  const limitCard = (title, val, used, percent, leftText, showReset, icon) => {
    const colorClass = barCol(percent);
    const textColorClass = textCol(percent);

    return (
      <Card variant="glass" padding="md" className="flex flex-col space-y-2">
        <Card.Row label={title} value={val} color={textColorClass} />
        <div className="w-full h-1.5 bg-base-content/20 rounded-full overflow-hidden">
          <div className={`h-full ${colorClass} rounded-full`} style={{ width: `${percent}%` }} />
        </div>
        <div className="flex justify-between items-center text-[10px] text-base-content/50 font-bold pt-1">
          <span>{t('lbl_left', lang)}: <span className={textColorClass}>{leftText}</span></span>
          <span>{t('lbl_using', lang)}: <span>{percent}%</span></span>
        </div>
        {showReset && (
          <div className="text-[10px] text-base-content/40 font-bold border-t border-base-content/5 pt-1.5 mt-1 flex justify-between">
            <span>{t('lbl_reset', lang)}</span>
            <span className="text-primary font-mono">{resetCountdown}</span>
          </div>
        )}
      </Card>
    );
  };

  const poolSection = (label, colorClass, icon, poolData) => {
    if (!poolData) return null;
    return (
      <Card variant="glass" padding="md">
        <Card.Header
          title={label}
          action={<Badge variant="success" size="xs">{t('opt_active', lang)}</Badge>}
        />
        <div className="space-y-2 text-xs">
          <Card.Row label={t('lbl_pool_rpd', lang)} value={fmt(poolData.rpd_left)} color={colorClass} />
          <Card.Row label={t('lbl_pool_1h', lang)} value={fmt(poolData.tokens_1h_left)} color="text-success" />
          <Card.Row label={t('lbl_pool_12h', lang)} value={fmt(poolData.tokens_12h_left)} color="text-warning" />
          <Card.Row label={t('lbl_pool_24h', lang)} value={fmt(poolData.tokens_24h_left)} color="text-primary" />
        </div>
      </Card>
    );
  };

  const tierBadge = {
    admin: <Badge variant="primary" size="xs">{tier}</Badge>,
    premium: <Badge variant="success" size="xs">{tier}</Badge>,
  }[tier] || <Badge variant="ghost" size="xs">{tier}</Badge>;

  const poolGauge = (label, remain, limit, unit) => {
    const pct = limit > 0 ? Math.round((1 - remain / limit) * 100) : 0;
    const col = pct > 80 ? 'bg-error' : pct > 50 ? 'bg-warning' : 'bg-success';
    const txtCol = pct > 80 ? 'text-error' : pct > 50 ? 'text-warning' : 'text-success';
    return (
      <div className="text-[10px]">
        <div className="flex justify-between mb-1">
          <span className="font-semibold text-base-content/50">{label}</span>
          <span className={`font-mono font-bold ${txtCol}`}>{remain.toLocaleString()} / {limit.toLocaleString()} {unit}</span>
        </div>
        <div className="w-full h-1.5 bg-base-content/20 rounded-full overflow-hidden">
          <div className={`h-full rounded-full transition-all duration-500 ${col}`} style={{width: `${pct}%`}}></div>
        </div>
      </div>
    );
  };

  const livePools = (() => {
    if (!liveActivity || !liveActivity.models) return null;
    const models = liveActivity.models;
    const flash = { rpm_rem: 0, rpm_lim: 0, tpm_rem: 0, tpm_lim: 0, cnt: 0 };
    const lite = { rpm_rem: 0, rpm_lim: 0, tpm_rem: 0, tpm_lim: 0, cnt: 0 };
    for (const [alias, ms] of Object.entries(models)) {
      const p = alias.includes('lite') ? lite : flash;
      p.rpm_rem += ms.rpm_remaining;
      p.rpm_lim += ms.rpm_limit;
      p.tpm_rem += ms.tpm_remaining;
      p.tpm_lim += ms.tpm_limit;
      p.cnt++;
    }
    const cards = [];
    if (flash.cnt > 0) cards.push({ label: 'Flash Pool', color: 'text-cyan-400', ...flash });
    if (lite.cnt > 0) cards.push({ label: 'Lite Pool', color: 'text-emerald-400', ...lite });
    if (cards.length === 0) return null;
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {cards.map(({ label, color, rpm_rem, rpm_lim, tpm_rem, tpm_lim, cnt }) => (
          <Card key={label} variant="glass" padding="md">
            <Card.Header title={<span className={color}>{label}</span>} action={<span className="text-[9px] text-base-content/40">{cnt} models</span>} />
            <div className="space-y-2 pt-1">
              {poolGauge('RPM Live', rpm_rem, rpm_lim, '')}
              {poolGauge('TPM Live', tpm_rem, tpm_lim, '')}
            </div>
          </Card>
        ))}
      </div>
    );
  })();

  return (
    <div className="space-y-6">
      <div className="text-left">
        <h1 className="text-2xl font-black tracking-tight">{t('nav_myacc', lang)}</h1>
        <p className="text-xs text-base-content/60 mt-1">{t('lbl_key_pools_sub', lang)}</p>
      </div>

      <Card variant="glass" padding="lg">
        <div className="flex items-center gap-4">
          <div className="w-14 h-14 bg-primary/10 text-primary text-xl font-black rounded-full flex items-center justify-center border border-primary/20 uppercase shadow-inner">
            {data.name.substring(0, 2).toUpperCase()}
          </div>
          <div className="flex-1 space-y-1">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-black text-base-content">{data.name}</h2>
              {tierBadge}
            </div>
            <p className="text-xs text-base-content/50 font-mono">
              {t('lbl_account_id', lang)}: <span className="text-primary font-bold">{data.account_id || '—'}</span>
            </p>
          </div>
          <button 
            onClick={() => setIsChangingPassword(!isChangingPassword)}
            className="btn btn-sm btn-outline text-xs whitespace-nowrap ml-auto"
          >
            Đổi mật khẩu
          </button>
        </div>

        {isChangingPassword && (
          <form onSubmit={handleChangePassword} className="mt-6 pt-4 border-t border-base-content/10 max-w-sm animate-fade-in-up">
            <h3 className="text-sm font-bold mb-3">Đổi Mật Khẩu Web</h3>
            <div className="space-y-3">
              <input 
                type="password" 
                placeholder="Mật khẩu hiện tại" 
                value={currentPassword}
                onChange={e => setCurrentPassword(e.target.value)}
                className="input input-sm input-bordered w-full" 
              />
              <input 
                type="password" 
                placeholder="Mật khẩu mới (ít nhất 4 ký tự)" 
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                className="input input-sm input-bordered w-full" 
              />
              <div className="flex items-center gap-2">
                <button type="submit" className="btn btn-sm btn-primary">Xác nhận</button>
                <button type="button" onClick={() => setIsChangingPassword(false)} className="btn btn-sm btn-ghost">Hủy</button>
              </div>
            </div>
          </form>
        )}

        {pwdMsg && (
          <div className={`mt-4 text-xs font-semibold p-2.5 rounded-lg border ${
            pwdMsg.type === 'success' ? 'bg-success/10 text-success border-success/20' : 'bg-error/10 text-error border-error/20'
          }`}>
            {pwdMsg.text}
          </div>
        )}
      </Card>

      {/* Auth tokens — the user-facing half of the token refactor */}
      <Card variant="glass" padding="lg">
        <div className="mb-3">
          <h3 className="font-extrabold text-sm flex items-center gap-2">
            <KeyRound className="w-4 h-4 text-primary" /> Auth tokens của tôi
          </h3>
          <p className="text-[11px] text-base-content/55 mt-0.5">
            Mỗi token có hạn mức riêng (concurrency, RPM, TPM, RPD). Bạn được
            tạo token và siết chặt hạn mức; không thể nới rộng hơn hạn mức
            tài khoản.
          </p>
        </div>

        {tokenMsg && (
          <div className={`text-xs font-semibold p-2.5 rounded-lg border mb-3 ${
            tokenMsg.type === 'success'
              ? 'bg-success/10 text-success border-success/20'
              : 'bg-error/10 text-error border-error/20'
          }`}>
            {tokenMsg.text}
          </div>
        )}

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
        <Card variant="glass" padding="sm">
          <div className="flex items-center gap-4 text-xs font-bold text-base-content/50">
            <div className="flex items-center gap-2">
              <Wifi className="w-3 h-3 text-success" />
              <span>Live</span>
            </div>
            <div className="flex items-center gap-4 text-[10px] font-mono text-base-content/50 flex-wrap">
              <span>Active Keys: <span className="text-base-content font-bold">{liveActivity.active_keys}</span></span>
              <span className="hidden sm:inline">|</span>
              <span className="hidden sm:inline">Connections: <span className="text-base-content font-bold">{liveActivity.connections}</span></span>
              <span>|</span>
              <span>429: <span className="text-warning font-bold">{liveActivity.rate_limits_429 || 0}</span></span>
              <span>|</span>
              <span>503: <span className="text-warning font-bold">{liveActivity.unavailable_503 || 0}</span></span>
              <span>|</span>
              <span>Penalties: <span className="text-error font-bold">{liveActivity.penalties}</span></span>
            </div>
          </div>
        </Card>
      )}

      {livePools}

      <div className="text-left space-y-1">
        <h3 className="font-extrabold text-sm text-base-content/85">Gemini Flash Pool — {t('lbl_usage_limits', lang)}</h3>
        <p className="text-[10px] text-base-content/50">{t('lbl_usage_limits_sub', lang)}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {limitCard(t('lbl_rpm_card', lang), `${flash.rpm_used} / ${(flash.rpm || 0).toLocaleString()}`, flash.rpm_used, fRpm, flash.rpm_left.toLocaleString(), false)}
        {limitCard(t('lbl_tpm_card', lang), `${fmt(flash.tpm_used)} / ${fmt(flash.tpm || 0)}`, flash.tpm_used, fTpm, fmt(flash.tpm_left), false)}
        {limitCard(t('lbl_rpd_card', lang), `${flash.rpd_used} / ${fmt(flash.rpd || 0)}`, flash.rpd_used, fRpd, flash.rpd_left.toLocaleString(), true)}
      </div>

      <div className="text-left space-y-1 pt-2">
        <h3 className="font-extrabold text-sm text-base-content/85">Gemini Flash Lite Pool — {t('lbl_usage_limits', lang)}</h3>
        <p className="text-[10px] text-base-content/50">{t('lbl_usage_limits_sub', lang)}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {limitCard(t('lbl_rpm_card', lang), `${lite.rpm_used} / ${(lite.rpm || 0).toLocaleString()}`, lite.rpm_used, lRpm, lite.rpm_left.toLocaleString(), false)}
        {limitCard(t('lbl_tpm_card', lang), `${fmt(lite.tpm_used)} / ${fmt(lite.tpm || 0)}`, lite.tpm_used, lTpm, fmt(lite.tpm_left), false)}
        {limitCard(t('lbl_rpd_card', lang), `${lite.rpd_used} / ${fmt(lite.rpd || 0)}`, lite.rpd_used, lRpd, lite.rpd_left.toLocaleString(), true)}
      </div>

      <div className="text-left space-y-1 pt-4">
        <h3 className="font-extrabold text-sm text-base-content/85">{t('lbl_key_pools', lang)}</h3>
        <p className="text-[10px] text-base-content/50">{t('lbl_key_pools_sub', lang)}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {poolSection('Gemini Flash Pool', 'text-cyan-400', <Zap className="w-4 h-4" />, data.flash_pool)}
        {poolSection('Gemini Flash Lite Pool', 'text-emerald-400', <Activity className="w-4 h-4" />, data.lite_pool)}
      </div>
    </div>
  );
}
