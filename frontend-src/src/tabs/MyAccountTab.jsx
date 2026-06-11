import React, { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { api } from '../utils/api';
import { User, Shield, Search, Zap, Calendar, Activity, Info, HelpCircle } from 'lucide-react';

export default function MyAccountTab() {
  const { tabData, token, lang, refreshTab } = useApp();
  const data = tabData.myacc;

  const [wsLoading, setWsLoading] = useState(false);
  const [resetCountdown, setResetCountdown] = useState('');

  // Setup ticking reset countdown until tomorrow midnight
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

  if (!data) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-3">
        <span className="loading loading-spinner loading-md text-primary"></span>
        <span className="text-sm font-semibold opacity-70">{t('loading', lang)}</span>
      </div>
    );
  }

  const tier = data.tier || 'free';
  const webSearchEnabled = data.web_search_enabled === true;
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

  // Handle Web Search toggle
  const handleWebSearchToggle = async (checked) => {
    setWsLoading(true);
    try {
      await api('/dashboard/my/web-search-toggle', {
        method: 'POST',
        body: JSON.stringify({ enabled: checked })
      }, token);
      refreshTab();
    } catch (err) {
      alert('Error toggle web search: ' + err.message);
    } finally {
      setWsLoading(false);
    }
  };

  const limitCard = (title, val, used, percent, leftText, showReset, icon) => {
    const colorClass = barCol(percent);
    const textColorClass = textCol(percent);

    return (
      <div className="card glass-card p-5 rounded-2xl flex flex-col text-left space-y-2 border border-base-content/5 shadow-md">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold text-base-content/60 uppercase">{title}</span>
          <div className={`w-8 h-8 rounded-lg bg-base-content/5 flex items-center justify-center ${textColorClass}`}>
            {icon}
          </div>
        </div>
        <span className="text-xl font-black">{val}</span>
        <div className="w-full h-1.5 bg-base-200/60 rounded-full overflow-hidden">
          <div className={`h-full ${colorClass} rounded-full`} style={{ width: `${percent}%` }}></div>
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
      </div>
    );
  };

  const poolSection = (label, colorClass, icon, poolData) => {
    if (!poolData) return null;
    return (
      <div className="card glass-card p-5 rounded-2xl text-left border border-base-content/5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={colorClass}>{icon}</span>
            <span className="font-extrabold text-sm text-base-content/90">{label}</span>
          </div>
          <span className="badge badge-sm badge-success/15 text-success border border-success/30 font-bold text-[9px] uppercase px-1.5">
            {t('opt_active', lang) || 'Active'}
          </span>
        </div>
        
        <div className="space-y-2 text-xs">
          <div className="flex justify-between py-1 border-b border-base-content/5">
            <span className="text-base-content/50">{t('lbl_pool_rpd', lang) || 'Hạn mức RPD còn lại'}</span>
            <span className={`font-bold ${colorClass}`}>{fmt(poolData.rpd_left)} / {fmt(poolData.rpd_limit)}</span>
          </div>
          <div className="flex justify-between py-1 border-b border-base-content/5">
            <span className="text-base-content/50">{t('lbl_pool_1h', lang) || 'Hạn mức Token 1h'}</span>
            <span className="font-bold text-success">{fmt(poolData.tokens_1h_left)} / {fmt(poolData.tokens_1h_limit)}</span>
          </div>
          <div className="flex justify-between py-1 border-b border-base-content/5">
            <span className="text-base-content/50">{t('lbl_pool_12h', lang) || 'Hạn mức Token 12h'}</span>
            <span className="font-bold text-warning">{fmt(poolData.tokens_12h_left)} / {fmt(poolData.tokens_12h_limit)}</span>
          </div>
          <div className="flex justify-between py-1">
            <span className="text-base-content/50">{t('lbl_pool_24h', lang) || 'Hạn mức Token 24h'}</span>
            <span className="font-bold text-primary">{fmt(poolData.tokens_24h_left)} / {fmt(poolData.tokens_24h_limit)}</span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Title */}
      <div className="text-left">
        <h1 className="text-2xl font-black tracking-tight">{t('nav_myacc', lang)}</h1>
        <p className="text-xs text-base-content/60 mt-1">{t('lbl_key_pools_sub', lang) || 'Thông tin tài khoản và hạn mức sử dụng khóa hiện tại'}</p>
      </div>

      {/* Profile Info Header */}
      <div className="card glass-card p-5 rounded-2xl flex flex-row items-center gap-4 text-left border border-base-content/5 shadow-md">
        <div className="w-14 h-14 bg-primary/10 text-primary text-xl font-black rounded-full flex items-center justify-center border border-primary/20 uppercase shadow-inner">
          {data.name.substring(0, 2).toUpperCase()}
        </div>
        <div className="flex-1 space-y-1">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-black text-base-content">{data.name}</h2>
            <span className={`badge badge-xs text-[9px] font-extrabold uppercase ${
              tier === 'admin' ? 'badge-primary' : tier === 'premium' ? 'badge-accent' : 'badge-ghost border-base-content/15'
            }`}>
              {tier}
            </span>
          </div>
          <p className="text-xs text-base-content/50 font-mono">
            {t('lbl_account_id', lang)}: <span className="text-primary font-bold">{data.account_id || '—'}</span>
          </p>
        </div>
      </div>

      {/* Web Search Toggle wrapper */}
      <div className="card glass-card p-4 rounded-2xl flex flex-row items-center justify-between text-left border border-base-content/5 shadow-sm">
        <div className="flex items-center gap-3">
          <input 
            type="checkbox" 
            id="ws-toggle-input" 
            checked={webSearchEnabled} 
            disabled={wsLoading}
            onChange={(e) => handleWebSearchToggle(e.target.checked)}
            className="toggle toggle-primary toggle-sm" 
          />
          <label htmlFor="ws-toggle-input" className="cursor-pointer space-y-0.5 select-none">
            <span className="text-xs font-bold text-base-content/90 block">{t('ws_toggle_label', lang) || 'Tìm kiếm Web (Google Search)'}</span>
            <span className="text-[10px] text-base-content/50 block">{t('ws_toggle_sub', lang) || 'Tự động tích hợp kết quả tìm kiếm Google vào câu trả lời của model'}</span>
          </label>
        </div>
        <div className="dropdown dropdown-left dropdown-hover">
          <label tabIndex={0} className="btn btn-circle btn-ghost btn-xs text-base-content/50">
            <HelpCircle className="w-4 h-4" />
          </label>
          <div tabIndex={0} className="dropdown-content card card-compact p-3 shadow bg-base-100 border border-base-content/10 text-xs w-64 z-20">
            {t('ws_toggle_tip', lang) || 'Bật tính năng để model tự động gọi công cụ tìm kiếm Google khi cần thông tin cập nhật realtime.'}
          </div>
        </div>
      </div>

      {/* Gemini Flash Pool Limits */}
      <div className="text-left space-y-1">
        <h3 className="font-extrabold text-sm text-base-content/85">Gemini Flash Pool — {t('lbl_usage_limits', lang)}</h3>
        <p className="text-[10px] text-base-content/50">{t('lbl_usage_limits_sub', lang)}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {limitCard(
          t('lbl_rpm_card', lang) || 'RPM (Giới hạn / Phút)', 
          `${flash.rpm_used} / ${(flash.rpm || 0).toLocaleString()}`, 
          flash.rpm_used, 
          fRpm, 
          flash.rpm_left.toLocaleString(), 
          false,
          <Zap className="w-4 h-4" />
        )}
        {limitCard(
          t('lbl_tpm_card', lang) || 'TPM (Token / Phút)', 
          `${fmt(flash.tpm_used)} / ${fmt(flash.tpm || 0)}`, 
          flash.tpm_used, 
          fTpm, 
          fmt(flash.tpm_left), 
          false,
          <Activity className="w-4 h-4" />
        )}
        {limitCard(
          t('lbl_rpd_card', lang) || 'RPD (Yêu cầu / Ngày)', 
          `${flash.rpd_used} / ${fmt(flash.rpd || 0)}`, 
          flash.rpd_used, 
          fRpd, 
          flash.rpd_left.toLocaleString(), 
          true,
          <Calendar className="w-4 h-4" />
        )}
      </div>

      {/* Gemini Flash Lite Pool Limits */}
      <div className="text-left space-y-1 pt-2">
        <h3 className="font-extrabold text-sm text-base-content/85">Gemini Flash Lite Pool — {t('lbl_usage_limits', lang)}</h3>
        <p className="text-[10px] text-base-content/50">{t('lbl_usage_limits_sub', lang)}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {limitCard(
          t('lbl_rpm_card', lang) || 'RPM (Giới hạn / Phút)', 
          `${lite.rpm_used} / ${(lite.rpm || 0).toLocaleString()}`, 
          lite.rpm_used, 
          lRpm, 
          lite.rpm_left.toLocaleString(), 
          false,
          <Zap className="w-4 h-4" />
        )}
        {limitCard(
          t('lbl_tpm_card', lang) || 'TPM (Token / Phút)', 
          `${fmt(lite.tpm_used)} / ${fmt(lite.tpm || 0)}`, 
          lite.tpm_used, 
          lTpm, 
          fmt(lite.tpm_left), 
          false,
          <Activity className="w-4 h-4" />
        )}
        {limitCard(
          t('lbl_rpd_card', lang) || 'RPD (Yêu cầu / Ngày)', 
          `${lite.rpd_used} / ${fmt(lite.rpd || 0)}`, 
          lite.rpd_used, 
          lRpd, 
          lite.rpd_left.toLocaleString(), 
          true,
          <Calendar className="w-4 h-4" />
        )}
      </div>

      {/* Key Pools Section */}
      <div className="text-left space-y-1 pt-4">
        <h3 className="font-extrabold text-sm text-base-content/85">{t('lbl_key_pools', lang) || 'Hệ thống Quỹ Keys (Key Pools)'}</h3>
        <p className="text-[10px] text-base-content/50">{t('lbl_key_pools_sub', lang)}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {poolSection(
          'Gemini Flash Pool', 
          'text-cyan-400', 
          <Zap className="w-4 h-4" />, 
          data.flash_pool
        )}
        {poolSection(
          'Gemini Flash Lite Pool', 
          'text-emerald-400', 
          <Activity className="w-4 h-4" />, 
          data.lite_pool
        )}
      </div>
    </div>
  );
}
