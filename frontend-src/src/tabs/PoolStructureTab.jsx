import React from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { ShieldCheck, Network, DollarSign, Activity, HelpCircle } from 'lucide-react';

export default function PoolStructureTab() {
  const { tabData, lang } = useApp();
  const muData = tabData.mu;

  if (!muData) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] gap-3">
        <span className="loading loading-spinner loading-md text-primary"></span>
        <span className="text-sm font-semibold opacity-70">{t('loading', lang)}</span>
      </div>
    );
  }

  const savings = muData.savings || {};
  const pools = muData.pools || [];

  return (
    <div className="space-y-6">
      {/* Title */}
      <div className="text-left">
        <h1 className="text-2xl font-black tracking-tight">{t('nav_mu', lang) || 'Cấu trúc tài nguyên & Tiết kiệm'}</h1>
        <p className="text-xs text-base-content/60 mt-1">
          Báo cáo thống kê hiệu quả tiết kiệm chi phí nhờ sử dụng Smart Caching và bảng phân bổ tài nguyên hệ thống (Model Pools)
        </p>
      </div>

      {/* Cost Savings Analytics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Standard Cost */}
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-base-content/60 uppercase">Chi phí gốc (Standard Cost)</span>
            <div className="w-8 h-8 rounded-lg bg-base-content/10 text-base-content flex items-center justify-center">
              <DollarSign className="w-4 h-4" />
            </div>
          </div>
          <span className="text-2xl font-black">${(savings.standard_cost || 0).toFixed(4)}</span>
          <span className="text-xs text-base-content/50 mt-1">Ước tính theo giá Claude</span>
        </div>

        {/* Cached Cost */}
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-base-content/60 uppercase">Chi phí Caching (Cached Cost)</span>
            <div className="w-8 h-8 rounded-lg bg-cyan-500/10 text-cyan-400 flex items-center justify-center">
              <DollarSign className="w-4 h-4" />
            </div>
          </div>
          <span className="text-2xl font-black text-cyan-400">${(savings.cached_cost || 0).toFixed(4)}</span>
          <span className="text-xs text-base-content/50 mt-1">Lượng token đã cache</span>
        </div>

        {/* Gemini actual cost */}
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-base-content/60 uppercase">Chi phí Gemini (Gemini Cost)</span>
            <div className="w-8 h-8 rounded-lg bg-warning/10 text-warning flex items-center justify-center">
              <DollarSign className="w-4 h-4" />
            </div>
          </div>
          <span className="text-2xl font-black text-warning">${(savings.gemini_cost || 0).toFixed(4)}</span>
          <span className="text-xs text-base-content/50 mt-1">Thực chi tại Gemini API</span>
        </div>

        {/* Net Savings */}
        <div className="card glass-card p-5 rounded-2xl flex flex-col text-left border border-success/25 bg-success/5 shadow-lg shadow-success/5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-bold text-success uppercase">Tổng tiết kiệm (Net Savings)</span>
            <div className="w-8 h-8 rounded-lg bg-success/20 text-success flex items-center justify-center">
              <ShieldCheck className="w-4 h-4" />
            </div>
          </div>
          <span className="text-2xl font-black text-success">${(savings.net_savings || 0).toFixed(4)}</span>
          <span className="text-xs text-success/80 mt-1">Đã tối ưu hóa thông minh</span>
        </div>
      </div>

      {/* Model Pools Table Card */}
      <div className="card glass-card rounded-2xl overflow-hidden text-left border border-base-content/5">
        <div className="p-5 border-b border-base-content/5 bg-base-200/10 flex justify-between items-center">
          <h3 className="font-extrabold text-sm flex items-center gap-2">
            <Network className="w-4 h-4 text-primary" />
            Cấu trúc Model Pools
          </h3>
          <span className="badge badge-sm badge-ghost font-bold border-base-content/10">
            {pools.length} resource pools
          </span>
        </div>
        <div className="overflow-x-auto w-full">
          <table className="table table-zebra w-full text-xs">
            <thead>
              <tr className="border-b border-base-content/5 text-base-content/60 bg-base-200/35">
                <th className="font-bold">Pool Name</th>
                <th className="font-bold">Mapped Models</th>
                <th className="font-bold">Pool RPM</th>
                <th className="font-bold">Pool TPM</th>
                <th className="font-bold w-24">Trạng thái</th>
              </tr>
            </thead>
            <tbody>
              {pools.length > 0 ? (
                pools.map((p, i) => {
                  const isPro = p.name.includes('pro');
                  return (
                    <tr key={i} className="border-b border-base-content/5 hover:bg-base-200/50">
                      <td><code className="font-bold text-primary">{p.name}</code></td>
                      <td>
                        <code className="bg-base-300/40 px-2 py-0.5 rounded border border-base-content/5 text-[11px] font-semibold text-base-content/75">
                          {p.models}
                        </code>
                      </td>
                      <td>{(p.rpm || 0).toLocaleString()}</td>
                      <td>{fmt(p.tpm || 0)}</td>
                      <td>
                        <span className={`badge badge-sm font-extrabold text-[10px] uppercase ${
                          isPro ? 'badge-primary' : 'badge-success/15 text-success border border-success/30'
                        }`}>
                          {p.status || 'active'}
                        </span>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan="5" className="text-center py-6 text-base-content/40 font-medium">
                    {t('no_data', lang)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
