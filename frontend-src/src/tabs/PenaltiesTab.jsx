import React, { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { relt } from '../utils/format';
import { ShieldAlert, AlertOctagon, HelpCircle, CheckCircle2 } from 'lucide-react';
import Loading from '../components/Loading';

export default function PenaltiesTab() {
  const { tabData, lang } = useApp();
  const penalties = tabData.pe || [];

  // Update cooldown countdown ticker every 1s
  const [timeTicker, setTimeTicker] = useState(Date.now() / 1000);
  useEffect(() => {
    const interval = setInterval(() => {
      setTimeTicker(Date.now() / 1000);
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  if (!tabData.pe) {
    return <Loading message={t('loading', lang)} />;
  }

  return (
    <div className="space-y-6">
      {/* Title */}
      <div className="text-left">
        <h1 className="text-2xl font-black tracking-tight">{t('nav_pe', lang) || 'Khóa bị phạt (Penalties)'}</h1>
        <p className="text-xs text-base-content/60 mt-1">{t('pe_sub', lang) || 'Danh sách các Gemini API Keys đang bị phạt giảm điểm số (Score Reduction) do lỗi kết nối hoặc rate limit'}</p>
      </div>

      {/* Overview Status Card */}
      <div className="flex justify-between items-center bg-base-200/20 px-4 py-3 rounded-xl border border-base-content/5">
        <span className="text-xs font-bold text-base-content/70 flex items-center gap-1.5">
          <ShieldAlert className="w-4 h-4 text-warning" />
          Tổng số hình phạt đang áp dụng
        </span>
        <span className={`badge ${penalties.length > 0 ? 'badge-error animate-pulse' : 'badge-success'} font-bold text-xs`}>
          {penalties.length} {t('penalties_count', lang) || 'hình phạt'}
        </span>
      </div>

      {/* Penalties List */}
      {penalties.length > 0 ? (
        <div className="card glass-card rounded-2xl overflow-hidden text-left border border-base-content/5">
          <div className="p-5 border-b border-base-content/5 bg-base-200/10 flex items-center gap-2">
            <AlertOctagon className="w-4 h-4 text-error" />
            <h3 className="font-extrabold text-sm">Hình phạt chi tiết</h3>
          </div>
          <div className="overflow-x-auto w-full">
            <table className="table table-zebra w-full text-xs">
              <thead>
                <tr className="border-b border-base-content/5 text-base-content/60 bg-base-200/35">
                  <th className="font-bold">{t('th_key_code', lang)}</th>
                  <th className="font-bold">{t('th_model', lang)}</th>
                  <th className="font-bold">{t('lbl_error_reason', lang) || 'Lý do lỗi'}</th>
                  <th className="font-bold">{t('th_score_reduction', lang) || 'Giảm điểm'}</th>
                  <th className="font-bold">{t('lbl_expires_after', lang) || 'Hết hạn sau'}</th>
                </tr>
              </thead>
              <tbody>
                {penalties.map((p, i) => {
                  const isExpired = p.expires <= timeTicker;
                  
                  return (
                    <tr key={i} className="border-b border-base-content/5 hover:bg-base-200/50">
                      <td><code className="font-semibold text-primary">{p.key}</code></td>
                      <td>
                        <code className="bg-base-200/80 px-2 py-0.5 rounded border border-base-content/5 font-mono text-[11px] font-semibold text-base-content/80">
                          {p.model_id || t('lbl_global', lang) || 'Global'}
                        </code>
                      </td>
                      <td className="font-semibold text-amber-500 max-w-xs truncate" title={p.reason || '—'}>
                        {p.reason || '—'}
                      </td>
                      <td>
                        <span className="badge badge-sm badge-error font-extrabold text-xs uppercase px-2 py-0.5">
                          -{p.score_reduction || 0}
                        </span>
                      </td>
                      <td className="font-bold">
                        {isExpired ? (
                          <span className="text-success flex items-center gap-1">
                            <CheckCircle2 className="w-3.5 h-3.5" /> Expired
                          </span>
                        ) : (
                          <span className="text-warning font-mono">
                            {relt(p.expires)}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="card glass-card p-12 text-center text-success rounded-2xl flex flex-col items-center gap-3">
          <CheckCircle2 className="w-12 h-12 text-success opacity-85" />
          <div>
            <h3 className="font-black text-lg">{t('no_penalties', lang) || 'Không có hình phạt nào!'}</h3>
            <p className="text-xs text-base-content/50 mt-1">Hệ thống đang hoạt động trơn tru. Toàn bộ API Keys đều đạt điểm tối ưu.</p>
          </div>
        </div>
      )}
    </div>
  );
}
