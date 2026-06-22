import React, { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../utils/api';
import { useApp } from '../context/AppContext';

export default function EditAccountModal({ account, isOpen, onClose, onSaveSuccess }) {
  const { token, lang } = useApp();
  const [tier, setTier] = useState('free');
  const [rpm, setRpm] = useState('');
  const [tpm, setTpm] = useState('');
  const [rpd, setRpd] = useState('');
  const [webSearchEnabled, setWebSearchEnabled] = useState(true);
  const [searchEngine, setSearchEngine] = useState('auto');
  
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState({ text: '', type: '' });

  useEffect(() => {
    if (account) {
      setTier(account.tier || 'free');
      setRpm(account.rpm !== undefined && account.rpm !== null ? account.rpm.toString() : '');
      setTpm(account.tpm !== undefined && account.tpm !== null ? account.tpm.toString() : '');
      setRpd(account.rpd !== undefined && account.rpd !== null ? account.rpd.toString() : '');
      setWebSearchEnabled(account.web_search_enabled !== undefined ? !!account.web_search_enabled : true);
      setSearchEngine(account.search_engine || 'auto');
      setMsg({ text: '', type: '' });
    }
  }, [account, isOpen]);

  if (!isOpen || !account) return null;

  const handleSave = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMsg({ text: '⏳ Đang lưu...', type: 'info' });

    const body = { 
      name: account.name, 
      tier,
      web_search_enabled: webSearchEnabled,
      search_engine: searchEngine
    };
    if (rpm.trim() !== '') body.rpm = parseInt(rpm, 10);
    if (tpm.trim() !== '') body.tpm = parseInt(tpm, 10);
    if (rpd.trim() !== '') body.rpd = parseInt(rpd, 10);

    try {
      await api('/dashboard/admin/accounts/update', {
        method: 'POST',
        body: JSON.stringify(body)
      }, token);

      setMsg({ text: '✅ Đã cập nhật thành công!', type: 'success' });
      setTimeout(() => {
        onSaveSuccess();
        onClose();
      }, 800);
    } catch (err) {
      setMsg({ text: '❌ Lỗi: ' + err.message, type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return createPortal(
    <div className="modal modal-open z-[9999] fixed inset-0 flex items-center justify-center">
      <div className="modal-overlay fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose}></div>
      <div className="modal-box max-w-md bg-base-100 border border-base-content/10 relative z-10 p-6 rounded-2xl shadow-2xl">
        <h3 className="font-extrabold text-lg mb-4 text-left">Chỉnh sửa tài khoản con</h3>
        
        <form onSubmit={handleSave} className="space-y-4 text-left">
          <div className="form-control w-full">
            <label className="label"><span className="label-text font-bold text-xs uppercase text-base-content/70">Tên tài khoản</span></label>
            <input type="text" value={account.name} disabled className="input input-bordered w-full bg-base-200/50 cursor-not-allowed text-sm font-semibold" />
          </div>

          <div className="form-control w-full">
            <label className="label"><span className="label-text font-bold text-xs uppercase text-base-content/70">Tier</span></label>
            <select value={tier} onChange={(e) => setTier(e.target.value)} className="select select-bordered w-full text-sm">
              <option value="free">Free Tier</option>
              <option value="premium">Premium Tier</option>
              <option value="admin">Admin Tier</option>
            </select>
          </div>

          <div className="form-control w-full">
            <label className="label"><span className="label-text font-bold text-xs uppercase text-base-content/70">RPM (Requests / Phút)</span></label>
            <input 
              type="number" 
              value={rpm} 
              onChange={(e) => setRpm(e.target.value)} 
              placeholder="Hạn mức RPM (để trống để giữ nguyên)" 
              className="input input-bordered w-full text-sm focus:border-primary"
            />
          </div>

          <div className="form-control w-full">
            <label className="label"><span className="label-text font-bold text-xs uppercase text-base-content/70">TPM (Tokens / Phút)</span></label>
            <input 
              type="number" 
              value={tpm} 
              onChange={(e) => setTpm(e.target.value)} 
              placeholder="Hạn mức TPM (để trống để giữ nguyên)" 
              className="input input-bordered w-full text-sm focus:border-primary"
            />
          </div>

          <div className="form-control w-full">
            <label className="label"><span className="label-text font-bold text-xs uppercase text-base-content/70">RPD (Requests / Ngày)</span></label>
            <input 
              type="number" 
              value={rpd} 
              onChange={(e) => setRpd(e.target.value)} 
              placeholder="Hạn mức RPD (để trống để giữ nguyên)" 
              className="input input-bordered w-full text-sm focus:border-primary"
            />
          </div>

          <div className="form-control w-full flex flex-row items-center gap-2 py-2">
            <input 
              type="checkbox" 
              checked={webSearchEnabled} 
              onChange={(e) => setWebSearchEnabled(e.target.checked)} 
              className="checkbox checkbox-primary checkbox-xs"
              id="web-search-enabled-checkbox"
            />
            <label htmlFor="web-search-enabled-checkbox" className="label cursor-pointer py-0">
              <span className="label-text font-bold text-xs uppercase text-base-content/70 select-none">Bật tìm kiếm Web</span>
            </label>
          </div>

          <div className="form-control w-full">
            <label className="label py-1"><span className="label-text font-bold text-xs uppercase text-base-content/70">Công cụ tìm kiếm mặc định</span></label>
            <select value={searchEngine} onChange={(e) => setSearchEngine(e.target.value)} className="select select-bordered w-full text-xs select-sm">
              <option value="auto">Tự động (Auto)</option>
              <option value="google_grounding">Google Grounding</option>
              <option value="duckduckgo">DuckDuckGo</option>
              <option value="disabled">Tắt tìm kiếm (Disabled)</option>
            </select>
          </div>

          {msg.text && (
            <div className={`text-xs font-semibold p-3 rounded-lg border ${
              msg.type === 'success' ? 'bg-success/10 text-success border-success/20' : 
              msg.type === 'error' ? 'bg-error/10 text-error border-error/20' : 'bg-info/10 text-info border-info/20'
            }`}>
              {msg.text}
            </div>
          )}

          <div className="modal-action gap-2 justify-end pt-2 border-t border-base-content/5 mt-6">
            <button type="button" onClick={onClose} disabled={loading} className="btn btn-sm btn-ghost normal-case font-bold">Hủy</button>
            <button type="submit" disabled={loading} className="btn btn-sm btn-primary normal-case font-bold px-4">Lưu thay đổi</button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
