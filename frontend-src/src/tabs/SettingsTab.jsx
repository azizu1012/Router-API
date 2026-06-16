import React, { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import Loading from '../components/Loading';
import { Save, Settings, ShieldAlert, Cpu, RefreshCw, KeyRound, Sliders, HardDrive, Info } from 'lucide-react';

export default function SettingsTab() {
  const { token, lang } = useApp();
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState({ text: '', type: '' });

  const fetchSettings = async () => {
    setLoading(true);
    try {
      const data = await api('/dashboard/admin/settings', {}, token);
      setSettings(data);
    } catch (err) {
      setMsg({ text: 'Không thể tải cấu hình: ' + err.message, type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSettings();
  }, [token]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMsg({ text: 'Đang lưu cấu hình...', type: 'info' });
    try {
      await api('/dashboard/admin/settings', {
        method: 'POST',
        body: JSON.stringify(settings)
      }, token);
      setMsg({ text: 'Đã lưu cấu hình và nạp lại hệ thống thành công!', type: 'success' });
      setTimeout(() => setMsg({ text: '', type: '' }), 4000);
    } catch (err) {
      setMsg({ text: 'Lưu thất bại: ' + err.message, type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (key, value) => {
    setSettings(prev => ({
      ...prev,
      [key]: value === '' ? '' : Number(value)
    }));
  };

  if (loading) {
    return <Loading message={t('loading', lang) || 'Đang tải...'} />;
  }

  if (!settings) {
    return (
      <div className="card glass-card p-12 text-center text-error font-medium rounded-2xl border border-error/15">
        Không tìm thấy hoặc không có quyền truy cập cấu hình hệ thống.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Title */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 animate-tab-in">
        <div className="text-left">
          <h1 className="text-2xl font-black tracking-tight flex items-center gap-2">
            <Settings className="w-6 h-6 text-primary" />
            Cấu hình Hệ thống (System Settings)
          </h1>
          <p className="text-xs text-base-content/60 mt-1">
            Điều chỉnh trực quan các tham số cấu hình chạy nền của Router API v2. Thay đổi sẽ cập nhật trực tiếp vào file .env và tự động nạp lại (hot-reload).
          </p>
        </div>
        <button
          onClick={fetchSettings}
          className="btn btn-outline btn-sm gap-2 font-bold hover:scale-[1.03] active:scale-[0.97] transition-all rounded-xl"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Tải lại (Reload)
        </button>
      </div>

      {msg.text && (
        <div className={`text-xs font-semibold p-4 rounded-xl border animate-fade-in-up ${
          msg.type === 'success' ? 'bg-success/10 text-success border-success/20' :
          msg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-info/10 text-info border-info/20'
        }`}>
          {msg.text}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6 animate-fade-in-up cascade-1">
        {/* Settings Groups Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          
          {/* Card 1: General & Timeout */}
          <div className="card glass-card p-6 rounded-3xl text-left border border-base-content/5 space-y-4">
            <h3 className="font-extrabold text-sm text-primary flex items-center gap-2 border-b border-base-content/5 pb-2">
              <Sliders className="w-4 h-4" />
              Cấu hình Chung & Timeout
            </h3>
            
            <div className="form-control w-full space-y-1">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-base-content/75 uppercase">Số lần Thử lại Tối đa (MAX_RETRIES)</span>
                <span className="badge badge-sm badge-primary font-mono">{settings.ROUTER_API_MAX_RETRIES}</span>
              </div>
              <input 
                type="range" 
                min="1" 
                max="20" 
                value={settings.ROUTER_API_MAX_RETRIES} 
                onChange={(e) => handleChange('ROUTER_API_MAX_RETRIES', e.target.value)}
                className="range range-xs range-primary" 
              />
              <p className="text-[10px] text-base-content/50">Số lần tối đa thử gửi lại request qua các key dự phòng khác khi gặp lỗi từ Gemini.</p>
            </div>

            <div className="form-control w-full space-y-1">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-base-content/75 uppercase">Thời gian Timeout Request (Giây)</span>
                <span className="badge badge-sm badge-primary font-mono">{settings.ROUTER_API_REQUEST_TIMEOUT_SEC}s</span>
              </div>
              <input 
                type="range" 
                min="30" 
                max="600" 
                step="30"
                value={settings.ROUTER_API_REQUEST_TIMEOUT_SEC} 
                onChange={(e) => handleChange('ROUTER_API_REQUEST_TIMEOUT_SEC', e.target.value)}
                className="range range-xs range-primary" 
              />
              <p className="text-[10px] text-base-content/50">Thời gian timeout tối đa cho các request API dài hoặc stream nặng trước khi ngắt kết nối.</p>
            </div>
          </div>

          {/* Card 2: Pool & Failures */}
          <div className="card glass-card p-6 rounded-3xl text-left border border-base-content/5 space-y-4">
            <h3 className="font-extrabold text-sm text-cyan-400 flex items-center gap-2 border-b border-base-content/5 pb-2">
              <Cpu className="w-4 h-4" />
              Khả năng Dung lỗi & Hoán đổi Pool
            </h3>

            <div className="form-control w-full space-y-1">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-base-content/75 uppercase">Số lỗi Hoán đổi Pool (POOL_SWAP_FAILURES)</span>
                <span className="badge badge-sm badge-secondary font-mono">{settings.POOL_SWAP_FAILURES} lần</span>
              </div>
              <input 
                type="range" 
                min="1" 
                max="10" 
                value={settings.POOL_SWAP_FAILURES} 
                onChange={(e) => handleChange('POOL_SWAP_FAILURES', e.target.value)}
                className="range range-xs range-secondary" 
              />
              <p className="text-[10px] text-base-content/50">Số lần gặp lỗi liên tiếp trên một pool trước khi tự động hoán chuyển sang pool dự phòng khác.</p>
            </div>

            <div className="form-control w-full space-y-1">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold text-base-content/75 uppercase">Số lần Thử lại tối đa mỗi Pool (POOL_MAX_ATTEMPTS)</span>
                <span className="badge badge-sm badge-secondary font-mono">{settings.POOL_MAX_ATTEMPTS} lần</span>
              </div>
              <input 
                type="range" 
                min="5" 
                max="30" 
                value={settings.POOL_MAX_ATTEMPTS} 
                onChange={(e) => handleChange('POOL_MAX_ATTEMPTS', e.target.value)}
                className="range range-xs range-secondary" 
              />
              <p className="text-[10px] text-base-content/50">Tổng số lần cố gắng thử các key trong cùng một pool trước khi báo lỗi toàn bộ hệ thống.</p>
            </div>
          </div>

          {/* Card 3: Context Compaction */}
          <div className="card glass-card p-6 rounded-3xl text-left border border-base-content/5 space-y-4 lg:col-span-2">
            <h3 className="font-extrabold text-sm text-amber-400 flex items-center gap-2 border-b border-base-content/5 pb-2">
              <HardDrive className="w-4 h-4" />
              Ngưỡng thu nén Context (Smart Token Compaction)
            </h3>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="form-control w-full space-y-1">
                <label className="label py-0">
                  <span className="label-text text-[11px] font-bold text-base-content/75 uppercase">Ngưỡng nén Token (Threshold)</span>
                </label>
                <div className="relative">
                  <input 
                    type="number" 
                    value={settings.COMPACTION_TOKEN_THRESHOLD} 
                    onChange={(e) => handleChange('COMPACTION_TOKEN_THRESHOLD', e.target.value)}
                    className="input input-bordered input-sm w-full font-mono text-xs pr-12" 
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[9px] font-bold text-base-content/40">Tokens</span>
                </div>
                <p className="text-[9px] text-base-content/50 leading-relaxed">Router bắt đầu thu nén khi context của client vượt quá mức này.</p>
              </div>

              <div className="form-control w-full space-y-1">
                <label className="label py-0">
                  <span className="label-text text-[11px] font-bold text-base-content/75 uppercase">Mức Token Đích (Target Limit)</span>
                </label>
                <div className="relative">
                  <input 
                    type="number" 
                    value={settings.COMPACTION_TARGET_LIMIT} 
                    onChange={(e) => handleChange('COMPACTION_TARGET_LIMIT', e.target.value)}
                    className="input input-bordered input-sm w-full font-mono text-xs pr-12" 
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[9px] font-bold text-base-content/40">Tokens</span>
                </div>
                <p className="text-[9px] text-base-content/50 leading-relaxed">Số lượng token đích tối đa sau khi thực hiện thuật toán thu nén context.</p>
              </div>

              <div className="form-control w-full space-y-1">
                <label className="label py-0">
                  <span className="label-text text-[11px] font-bold text-base-content/75 uppercase">Ngưỡng Khẩn cấp (Emergency Max)</span>
                </label>
                <div className="relative">
                  <input 
                    type="number" 
                    value={settings.EMERGENCY_MAX_INPUT_TOKENS} 
                    onChange={(e) => handleChange('EMERGENCY_MAX_INPUT_TOKENS', e.target.value)}
                    className="input input-bordered input-sm w-full font-mono text-xs pr-12" 
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[9px] font-bold text-base-content/40">Tokens</span>
                </div>
                <p className="text-[9px] text-base-content/50 leading-relaxed">Giới hạn khẩn cấp tối đa chấp nhận trước khi cắt bỏ mạnh để chống quá tải (429).</p>
              </div>
            </div>

            {/* Sub-section: Claude Code specific overrides */}
            <div className="bg-base-200/20 p-4 rounded-2xl border border-base-content/5 space-y-3 mt-2">
              <div className="flex items-center gap-1.5 text-amber-500/80 font-bold text-xs">
                <Info className="w-3.5 h-3.5" />
                <span>Cấu hình ghi đè riêng cho Claude Code (RTK Compaction Override)</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="form-control w-full space-y-1">
                  <label className="label py-0"><span className="label-text text-[10px] font-bold text-base-content/60 uppercase">Claude Threshold</span></label>
                  <input type="number" value={settings.CLAUDE_CODE_COMPACTION_THRESHOLD} onChange={(e) => handleChange('CLAUDE_CODE_COMPACTION_THRESHOLD', e.target.value)} className="input input-bordered input-sm w-full font-mono text-xs" />
                </div>
                <div className="form-control w-full space-y-1">
                  <label className="label py-0"><span className="label-text text-[10px] font-bold text-base-content/60 uppercase">Claude Target Limit</span></label>
                  <input type="number" value={settings.CLAUDE_CODE_COMPACTION_TARGET_LIMIT} onChange={(e) => handleChange('CLAUDE_CODE_COMPACTION_TARGET_LIMIT', e.target.value)} className="input input-bordered input-sm w-full font-mono text-xs" />
                </div>
                <div className="form-control w-full space-y-1">
                  <label className="label py-0"><span className="label-text text-[10px] font-bold text-base-content/60 uppercase">Claude Emergency Max</span></label>
                  <input type="number" value={settings.CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS} onChange={(e) => handleChange('CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS', e.target.value)} className="input input-bordered input-sm w-full font-mono text-xs" />
                </div>
              </div>
            </div>
          </div>

          {/* Card 4: Client Rate Limits */}
          <div className="card glass-card p-6 rounded-3xl text-left border border-base-content/5 space-y-4 lg:col-span-2">
            <h3 className="font-extrabold text-sm text-emerald-400 flex items-center gap-2 border-b border-base-content/5 pb-2">
              <KeyRound className="w-4 h-4" />
              Giới hạn Tải mặc định của Client
            </h3>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="form-control w-full space-y-1">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-bold text-base-content/75 uppercase">Hạn mức RPM Mặc định (CLIENT_DEFAULT_RPM)</span>
                  <span className="badge badge-sm badge-accent font-mono">{settings.CLIENT_DEFAULT_RPM} RPM</span>
                </div>
                <input 
                  type="range" 
                  min="1" 
                  max="60" 
                  value={settings.CLIENT_DEFAULT_RPM} 
                  onChange={(e) => handleChange('CLIENT_DEFAULT_RPM', e.target.value)}
                  className="range range-xs range-accent" 
                />
                <p className="text-[10px] text-base-content/50">Số request tối đa mỗi phút (RPM) cấp mặc định cho tài khoản client mới tạo.</p>
              </div>

              <div className="form-control w-full space-y-1">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-bold text-base-content/75 uppercase">Hạn mức RPM Burst (CLIENT_BURST_RPM)</span>
                  <span className="badge badge-sm badge-accent font-mono">{settings.CLIENT_BURST_RPM} RPM</span>
                </div>
                <input 
                  type="range" 
                  min="2" 
                  max="120" 
                  value={settings.CLIENT_BURST_RPM} 
                  onChange={(e) => handleChange('CLIENT_BURST_RPM', e.target.value)}
                  className="range range-xs range-accent" 
                />
                <p className="text-[10px] text-base-content/50">Hạn mức RPM tăng cường đột biến cho phép của client để tránh nghẽn khi client gửi dồn dập.</p>
              </div>
            </div>
          </div>

        </div>

        {/* Submit Bar */}
        <div className="flex items-center justify-end gap-3 pt-4 border-t border-base-content/5">
          <button 
            type="button" 
            onClick={fetchSettings} 
            disabled={saving}
            className="btn btn-ghost font-bold rounded-xl"
          >
            Hủy bỏ (Discard)
          </button>
          <button 
            type="submit" 
            disabled={saving}
            className="btn btn-primary px-8 gap-2 font-bold shadow-lg shadow-primary/25 hover:scale-[1.03] active:scale-[0.97] transition-all rounded-xl"
          >
            {saving ? <span className="loading loading-spinner loading-xs"></span> : <Save className="w-4 h-4" />}
            Lưu thay đổi (Save Settings)
          </button>
        </div>

      </form>
    </div>
  );
}
