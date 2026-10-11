import React, { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import {
  Plus, Trash2, Edit3, Sparkles, Copy, Check, Info, AlertCircle,
  Network, RefreshCw, Power, Server, ArrowRight, Tag
} from 'lucide-react';
import Loading from '../components/Loading';

// Template suggestions for quick setup
const ALIAS_TEMPLATES = [
  // --- CLAUDE (Anthropic) ---
  {
    id: 'claude-3-7-sonnet',
    category: 'Claude',
    name: 'Claude 3.7 Sonnet (Hybrid Reasoning)',
    alias_name: 'claude-3-7-sonnet-20250219',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label: 'Flash = Sonnet 3.7',
    description: 'Model mới nhất của Anthropic (2025), hỗ trợ hybrid reasoning'
  },
  {
    id: 'claude-3-5-sonnet',
    category: 'Claude',
    name: 'Claude 3.5 Sonnet v2',
    alias_name: 'claude-3-5-sonnet-20241022',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label: 'Flash = Sonnet 3.5',
    description: 'Model coding chuẩn phổ biến nhất của Claude, chạy Gemini Flash'
  },
  {
    id: 'claude-sonnet-5-5',
    category: 'Claude',
    name: 'Claude Sonnet 5.5 (Next-Gen Relay)',
    alias_name: 'claude-sonnet-5-5',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label: 'Sonnet 5.5 đời cao → Gemini Flash',
    description: 'Tên model thế hệ mới dùng trên các relay/proxy, chạy Gemini Flash'
  },
  {
    id: 'claude-sonnet-5',
    category: 'Claude',
    name: 'Claude Sonnet 5 (Next-Gen)',
    alias_name: 'claude-sonnet-5',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label: 'Sonnet 5 → Gemini Flash',
    description: 'Chạy Gemini Flash cho client yêu cầu sonnet-5'
  },
  {
    id: 'claude-3-5-haiku',
    category: 'Claude',
    name: 'Claude 3.5 Haiku (Fast)',
    alias_name: 'claude-3-5-haiku-20241022',
    target_model: 'gemini-flash-lite',
    target_endpoint: null,
    label: 'Lite = Haiku 3.5',
    description: 'Tốc độ cao, sub-agent rẻ, chạy Gemini Flash Lite'
  },
  {
    id: 'claude-haiku-5-5',
    category: 'Claude',
    name: 'Claude Haiku 5.5 (Next-Gen Fast)',
    alias_name: 'claude-haiku-5-5',
    target_model: 'gemini-flash-lite',
    target_endpoint: null,
    label: 'Haiku 5.5 → Gemini Flash Lite',
    description: 'Haiku 5.5 tốc độ cao, chạy Gemini Flash Lite'
  },
  {
    id: 'claude-opus-5-5',
    category: 'Claude',
    name: 'Claude Opus 5.5 (Deep Reasoning)',
    alias_name: 'claude-opus-5-5',
    target_model: 'gemini-pro',
    target_endpoint: null,
    label: 'Opus 5.5 → Gemini Pro',
    description: 'Dành cho client đòi model Opus đời cao, chạy Gemini Pro'
  },
  {
    id: 'claude-3-opus',
    category: 'Claude',
    name: 'Claude 3 Opus (Classic Flagship)',
    alias_name: 'claude-3-opus-20240229',
    target_model: 'gemini-pro',
    target_endpoint: null,
    label: 'Opus 3 → Gemini Pro',
    description: 'Claude 3 Opus chính thức, chạy Gemini Pro'
  },

  // --- OPENAI (GPT & Reasoning) ---
  {
    id: 'gpt-4-5-preview',
    category: 'OpenAI',
    name: 'GPT-4.5 Preview (Orion Flagship)',
    alias_name: 'gpt-4.5-preview',
    target_model: 'gemini-pro',
    target_endpoint: null,
    label: 'GPT-4.5 Orion → Gemini Pro',
    description: 'Model frontier lớn nhất mới nhất của OpenAI (Feb 2025), chạy Gemini Pro'
  },
  {
    id: 'o3-mini',
    category: 'OpenAI',
    name: 'OpenAI o3-mini (Reasoning Coder)',
    alias_name: 'o3-mini',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label: 'o3-mini reasoning → Gemini Flash',
    description: 'Model suy luận lập trình chuyên sâu của OpenAI, chạy Gemini Flash'
  },
  {
    id: 'o1',
    category: 'OpenAI',
    name: 'OpenAI o1 (Full Reasoning)',
    alias_name: 'o1',
    target_model: 'gemini-pro',
    target_endpoint: null,
    label: 'o1 reasoning → Gemini Pro',
    description: 'Full reasoning model của OpenAI, chạy Gemini Pro'
  },
  {
    id: 'gpt-4o',
    category: 'OpenAI',
    name: 'GPT-4o (Omni Flagship)',
    alias_name: 'gpt-4o',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label: 'gpt-4o đa năng → Gemini Flash',
    description: 'Model tiêu chuẩn của OpenAI SDK, chạy Gemini Flash'
  },
  {
    id: 'gpt-4o-mini',
    category: 'OpenAI',
    name: 'GPT-4o Mini (Siêu nhanh)',
    alias_name: 'gpt-4o-mini',
    target_model: 'gemini-flash-lite',
    target_endpoint: null,
    label: 'gpt-4o-mini → Gemini Flash Lite',
    description: 'Tác vụ phụ, sub-agents với chi phí tối thiểu'
  },
  {
    id: 'gpt-5',
    category: 'OpenAI',
    name: 'GPT-5 (Speculative Next-Gen)',
    alias_name: 'gpt-5',
    target_model: 'gemini-pro',
    target_endpoint: null,
    label: 'GPT-5 tương lai → Gemini Pro',
    description: 'Dành cho các tool/extension thử nghiệm yêu cầu tên gpt-5'
  },

  // --- CUSTOM ENDPOINTS ---
  {
    id: 'custom-claude-to-gpt',
    category: 'Custom Endpoint',
    name: 'Custom Endpoint: GPT-4o → Claude Sonnet',
    alias_name: 'gpt-4o',
    target_model: 'claude-sonnet-5-5',
    target_endpoint: '',
    label: 'OpenAI client gọi GPT-4o, chạy Claude trên Custom Endpoint',
    description: 'Cần chọn Custom Endpoint của bạn'
  },
  {
    id: 'custom-gpt-to-claude',
    category: 'Custom Endpoint',
    name: 'Custom Endpoint: Claude 3.7 → Custom DeepSeek',
    alias_name: 'claude-3-7-sonnet-20250219',
    target_model: 'deepseek-v4.1-flash',
    target_endpoint: '',
    label: 'Claude Code gọi Sonnet 3.7, chạy DeepSeek trên Custom Endpoint',
    description: 'Bypass regex của Claude Code nhưng chạy endpoint riêng'
  }
];

export default function ModelAliasesTab() {
  const { token, lang, user } = useApp();
  const isAdmin = user?.tier === 'admin';

  const [activeSubTab, setActiveSubTab] = useState('aliases'); // 'aliases' | 'endpoints'
  const [aliases, setAliases] = useState([]);
  const [endpoints, setEndpoints] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [editingAlias, setEditingAlias] = useState(null);
  const [templateCategory, setTemplateCategory] = useState('All');
  const [msgState, setMsgState] = useState({ text: '', type: '' });
  const [copiedId, setCopiedId] = useState(null);

  // New endpoint form state
  const [showAddEpForm, setShowAddEpForm] = useState(false);
  const [epFormData, setEpFormData] = useState({
    name: '',
    base_url: '',
    auth_key: '',
    api_format: 'openai'
  });
  const [epLoadingStates, setEpLoadingStates] = useState({});
  const [newModelInput, setNewModelInput] = useState({});

  // Alias Form state
  const [formData, setFormData] = useState({
    alias_name: '',
    target_model: '',
    target_endpoint: null,
    label: '',
    key_id: null,
    enabled: true
  });

  useEffect(() => {
    loadAliases();
    loadEndpoints();
  }, [token, isAdmin]);

  const setMsg = (text, type = 'info') => {
    setMsgState({ text, type });
    if (type === 'success') {
      setTimeout(() => setMsgState({ text: '', type: '' }), 4000);
    }
  };

  const loadAliases = async () => {
    try {
      setLoading(true);
      const url = isAdmin ? '/dashboard/admin/aliases' : '/api/me/aliases';
      const data = await api(url, { method: 'GET' }, token);
      const list = Array.isArray(data) ? data : (data.aliases || []);
      setAliases(list);
    } catch (err) {
      setMsg('❌ Lỗi tải aliases: ' + err.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  const loadEndpoints = async () => {
    try {
      const url = isAdmin ? '/dashboard/admin/endpoints' : '/api/me/endpoints';
      const data = await api(url, { method: 'GET' }, token);
      const list = Array.isArray(data) ? data : (data.endpoints || data.ep || []);
      setEndpoints(list);
    } catch (err) {
      console.error('Failed to load endpoints:', err);
    }
  };

  const handleAddAlias = async (e) => {
    e.preventDefault();
    if (!formData.alias_name.trim() || !formData.target_model.trim()) return;
    try {
      const url = isAdmin ? '/dashboard/admin/aliases' : '/api/me/aliases';
      await api(url, {
        method: 'POST',
        body: JSON.stringify({
          alias_name: formData.alias_name.trim(),
          target_model: formData.target_model.trim(),
          target_endpoint: formData.target_endpoint || null,
          label: formData.label.trim(),
          account_key_id: formData.key_id || null,
          enabled: true
        })
      }, token);
      setMsg('✅ Đã tạo alias thành công!', 'success');
      resetForm();
      loadAliases();
      setShowAddForm(false);
    } catch (err) {
      setMsg('❌ Lỗi tạo alias: ' + err.message, 'error');
    }
  };

  const handleUpdateAlias = async (e) => {
    e.preventDefault();
    try {
      const url = isAdmin
        ? `/dashboard/admin/aliases/${editingAlias.alias_id}`
        : `/api/me/aliases/${editingAlias.alias_id}`;
      await api(url, {
        method: 'PATCH',
        body: JSON.stringify({
          target_model: formData.target_model.trim(),
          target_endpoint: formData.target_endpoint || null,
          label: formData.label.trim(),
          enabled: formData.enabled
        })
      }, token);
      setMsg('✅ Đã cập nhật alias!', 'success');
      resetForm();
      loadAliases();
      setEditingAlias(null);
      setShowAddForm(false);
    } catch (err) {
      setMsg('❌ Lỗi cập nhật: ' + err.message, 'error');
    }
  };

  const handleDeleteAlias = async (aliasId) => {
    if (!confirm('Xóa alias này? Thao tác không thể hoàn tác.')) return;
    try {
      const url = isAdmin
        ? `/dashboard/admin/aliases/${aliasId}`
        : `/api/me/aliases/${aliasId}`;
      await api(url, { method: 'DELETE' }, token);
      setMsg('✅ Đã xóa alias', 'success');
      loadAliases();
    } catch (err) {
      setMsg('❌ Lỗi xóa: ' + err.message, 'error');
    }
  };

  // Endpoint Management Handlers
  const handleAddEndpoint = async (e) => {
    e.preventDefault();
    if (!epFormData.name.trim() || !epFormData.base_url.trim()) return;
    try {
      const url = isAdmin ? '/dashboard/admin/endpoints/add' : '/api/me/endpoints';
      await api(url, {
        method: 'POST',
        body: JSON.stringify({
          name: epFormData.name.trim(),
          base_url: epFormData.base_url.trim(),
          auth_key: epFormData.auth_key.trim(),
          api_format: epFormData.api_format
        })
      }, token);
      setMsg('✅ Đã thêm Endpoint thành công!', 'success');
      setEpFormData({ name: '', base_url: '', auth_key: '', api_format: 'openai' });
      setShowAddEpForm(false);
      loadEndpoints();
    } catch (err) {
      setMsg('❌ Lỗi thêm endpoint: ' + err.message, 'error');
    }
  };

  const handleDeleteEndpoint = async (name) => {
    if (!confirm(`Xóa endpoint '${name}'?`)) return;
    try {
      const url = isAdmin ? '/dashboard/admin/endpoints/delete' : `/api/me/endpoints/${name}`;
      const options = isAdmin
        ? { method: 'POST', body: JSON.stringify({ name }) }
        : { method: 'DELETE' };
      await api(url, options, token);
      setMsg(`✅ Đã xóa endpoint '${name}'`, 'success');
      loadEndpoints();
    } catch (err) {
      setMsg('❌ Lỗi xóa endpoint: ' + err.message, 'error');
    }
  };

  const handleRefreshEpModels = async (name) => {
    setEpLoadingStates(prev => ({ ...prev, [name]: true }));
    try {
      const url = isAdmin ? '/dashboard/admin/endpoints/refresh' : `/api/me/endpoints/${name}/refresh`;
      const options = isAdmin
        ? { method: 'POST', body: JSON.stringify({ name }) }
        : { method: 'POST' };
      const data = await api(url, options, token);
      const count = data.count || (data.models ? data.models.length : 0);
      setMsg(`✅ Đã cập nhật ${count} models từ endpoint '${name}'!`, 'success');
      loadEndpoints();
    } catch (err) {
      setMsg('❌ Lỗi tải models: ' + err.message, 'error');
    } finally {
      setEpLoadingStates(prev => ({ ...prev, [name]: false }));
    }
  };

  const handleAddCustomModelToEp = async (name) => {
    const modelId = (newModelInput[name] || '').trim();
    if (!modelId) return;
    try {
      const url = isAdmin ? '/dashboard/admin/endpoints/toggle-model' : `/api/me/endpoints/${name}/toggle-model`;
      await api(url, {
        method: 'POST',
        body: JSON.stringify({ name, model_id: modelId, enabled: true })
      }, token);
      setMsg(`✅ Đã thêm model '${modelId}' vào endpoint '${name}'`, 'success');
      setNewModelInput(prev => ({ ...prev, [name]: '' }));
      loadEndpoints();
    } catch (err) {
      setMsg('❌ Lỗi thêm model: ' + err.message, 'error');
    }
  };

  const handleCreateAliasFromEpModel = (endpointName, modelId) => {
    setFormData({
      alias_name: '',
      target_model: modelId,
      target_endpoint: endpointName,
      label: `Spoof for ${endpointName} / ${modelId}`,
      key_id: null,
      enabled: true
    });
    setEditingAlias(null);
    setActiveSubTab('aliases');
    setShowAddForm(true);
    setShowTemplates(false);
  };

  const applyTemplate = (template) => {
    setFormData({
      alias_name: template.alias_name,
      target_model: template.target_model,
      target_endpoint: template.target_endpoint,
      label: template.label,
      key_id: null,
      enabled: true
    });
    setShowTemplates(false);
    setShowAddForm(true);
  };

  const resetForm = () => {
    setFormData({
      alias_name: '',
      target_model: '',
      target_endpoint: null,
      label: '',
      key_id: null,
      enabled: true
    });
    setEditingAlias(null);
  };

  const startEdit = (alias) => {
    setEditingAlias(alias);
    setFormData({
      alias_name: alias.alias_name,
      target_model: alias.target_model,
      target_endpoint: alias.target_endpoint,
      label: alias.label || '',
      key_id: alias.account_key_id,
      enabled: alias.enabled
    });
    setShowAddForm(true);
    setShowTemplates(false);
  };

  const copyToClipboard = (text, id) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Get available models for selected endpoint in form
  const selectedEndpointObj = endpoints.find(e => e.name === formData.target_endpoint);
  const availableTargetModels = selectedEndpointObj
    ? (selectedEndpointObj.enabled_models && selectedEndpointObj.enabled_models.length > 0
        ? selectedEndpointObj.enabled_models
        : (selectedEndpointObj.models || []))
    : ['gemini-flash', 'gemini-flash-lite', 'gemini-pro'];

  if (loading && aliases.length === 0) {
    return <Loading message="Đang tải dữ liệu..." />;
  }

  return (
    <div className="space-y-5 animate-tab-in">
      {/* Header & Sub-Tab Switcher */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div className="text-left">
          <h1 className="text-2xl font-black tracking-tight flex items-center gap-2">
            <Sparkles className="w-6 h-6 text-primary" />
            Model Spoofing & Custom Endpoints
          </h1>
          <p className="text-xs text-base-content/60 mt-1">
            Bypass client regex validation (Cursor AI, Claude Code, OpenAI SDK). Ví dụ: gửi <code className="px-1 py-0.5 bg-base-300 rounded font-mono">claude-3-5-sonnet</code> nhưng chạy <code className="px-1 py-0.5 bg-base-300 rounded font-mono">gemini-flash</code> hoặc Custom Endpoint của bạn.
          </p>
        </div>

        {/* Tab Switcher */}
        <div className="join bg-base-200/60 p-1 rounded-2xl border border-base-300/60 self-start sm:self-auto">
          <button
            onClick={() => setActiveSubTab('aliases')}
            className={`join-item btn btn-xs sm:btn-sm gap-2 font-bold ${activeSubTab === 'aliases' ? 'btn-primary shadow-sm' : 'btn-ghost'}`}
          >
            <Tag className="w-3.5 h-3.5" />
            Model Aliases ({aliases.length})
          </button>
          <button
            onClick={() => setActiveSubTab('endpoints')}
            className={`join-item btn btn-xs sm:btn-sm gap-2 font-bold ${activeSubTab === 'endpoints' ? 'btn-primary shadow-sm' : 'btn-ghost'}`}
          >
            <Server className="w-3.5 h-3.5" />
            My Endpoints ({endpoints.length})
          </button>
        </div>
      </div>

      {/* Message banner */}
      {msgState.text && (
        <div className={`alert ${msgState.type === 'error' ? 'alert-error' : 'alert-success'} text-xs font-semibold shadow-lg animate-fade-in-up py-2.5`}>
          <span>{msgState.text}</span>
        </div>
      )}

      {/* ==================== SUB-TAB 1: MODEL ALIASES ==================== */}
      {activeSubTab === 'aliases' && (
        <div className="space-y-4">
          {/* Controls Bar */}
          <div className="flex justify-between items-center bg-base-200/40 p-3 rounded-2xl border border-base-300/40">
            <div className="text-xs text-base-content/70">
              Có <strong>{aliases.length}</strong> alias đã cấu hình
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => { setShowTemplates(v => !v); setShowAddForm(false); }}
                className={`btn btn-xs sm:btn-sm gap-1.5 font-bold shadow-sm ${showTemplates ? 'btn-primary' : 'btn-outline'}`}
              >
                <Sparkles className="w-3.5 h-3.5" />
                Templates (Gợi ý)
              </button>
              <button
                onClick={() => { setShowAddForm(v => !v); setShowTemplates(false); resetForm(); }}
                className="btn btn-primary btn-xs sm:btn-sm gap-1.5 font-bold shadow-md shadow-primary/20"
              >
                <Plus className="w-3.5 h-3.5" />
                Tạo Alias Mới
              </button>
            </div>
          </div>

          {/* Quick Setup Template Picker */}
          {showTemplates && (
            <div className="card glass-card p-5 rounded-2xl animate-fade-in-up border border-primary/20 bg-base-100/80 shadow-xl">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-extrabold text-sm flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-primary" />
                  Mẫu Cấu Hình Sẵn (Nhấn vào để áp dụng)
                </h3>
                <span className="text-[11px] text-base-content/50">Mới nhất: Claude 3.7, Sonnet 5.5, GPT-4.5, o3-mini</span>
              </div>
              <div className="flex gap-1.5 flex-wrap mb-3.5">
                {['All', 'Claude', 'OpenAI', 'Custom Endpoint'].map(cat => (
                  <button
                    key={cat}
                    onClick={() => setTemplateCategory(cat)}
                    className={`btn btn-xs rounded-xl font-bold transition-all ${templateCategory === cat ? 'btn-primary shadow-sm' : 'btn-ghost bg-base-200/60'}`}
                  >
                    {cat === 'All' ? 'Tất cả' : cat}
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {(templateCategory === 'All' ? ALIAS_TEMPLATES : ALIAS_TEMPLATES.filter(t => t.category === templateCategory)).map(template => (
                  <button
                    key={template.id}
                    onClick={() => applyTemplate(template)}
                    className="card bg-base-200/60 hover:bg-base-200 p-3.5 text-left transition-all hover:scale-[1.02] active:scale-[0.98] border border-base-300/50 hover:border-primary/40 shadow-sm"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-bold text-xs text-primary">{template.name}</span>
                      <span className="badge badge-ghost badge-xs text-[9px] font-medium">{template.category}</span>
                    </div>
                    <div className="text-[11px] text-base-content/70 mb-2 leading-relaxed">{template.description}</div>
                    <div className="flex flex-col gap-1 text-[10px] font-mono bg-base-300/40 p-2 rounded-lg">
                      <div className="truncate"><span className="text-base-content/50">Client gửi:</span> <span className="text-primary font-bold">{template.alias_name}</span></div>
                      <div className="truncate"><span className="text-base-content/50">Router chạy:</span> <span className="text-secondary font-bold">{template.target_model}</span></div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Add / Edit Alias Form */}
          {showAddForm && (
            <div className="card glass-card p-5 rounded-2xl animate-fade-in-up border border-primary/20 bg-base-100/90 shadow-xl">
              <h3 className="font-extrabold text-sm mb-4 flex items-center gap-2">
                {editingAlias ? <Edit3 className="w-4 h-4 text-warning" /> : <Plus className="w-4 h-4 text-primary" />}
                {editingAlias ? 'Chỉnh Sửa Model Alias' : 'Tạo Model Alias Mới'}
              </h3>
              <form onSubmit={editingAlias ? handleUpdateAlias : handleAddAlias} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="form-control">
                  <label className="label py-1">
                    <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Alias Name (Tên Client Gửi)</span>
                  </label>
                  <input
                    type="text"
                    required
                    disabled={!!editingAlias}
                    value={formData.alias_name}
                    onChange={(e) => setFormData({...formData, alias_name: e.target.value})}
                    placeholder="claude-3-5-sonnet-20241022 hoặc gpt-4"
                    className="input input-bordered input-sm text-xs font-mono"
                  />
                  <span className="text-[10px] text-base-content/50 mt-1">Client SDK / IDE (Cursor, Claude Code) sẽ gửi tên này</span>
                </div>

                <div className="form-control">
                  <label className="label py-1">
                    <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Target Endpoint (Tùy chọn)</span>
                  </label>
                  <select
                    value={formData.target_endpoint || ''}
                    onChange={(e) => setFormData({...formData, target_endpoint: e.target.value || null})}
                    className="select select-bordered select-sm text-xs"
                  >
                    <option value="">-- Mặc định: Dùng Pool nội bộ (Gemini) --</option>
                    {endpoints.map(ep => (
                      <option key={ep.name} value={ep.name}>{ep.name} ({ep.api_format || 'openai'})</option>
                    ))}
                  </select>
                  <span className="text-[10px] text-base-content/50 mt-1">Chọn custom endpoint nếu muốn gọi bên ngoài</span>
                </div>

                <div className="form-control">
                  <label className="label py-1">
                    <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Target Model (Model Chạy Thực Tế)</span>
                  </label>
                  <input
                    type="text"
                    required
                    list="target-models-list"
                    value={formData.target_model}
                    onChange={(e) => setFormData({...formData, target_model: e.target.value})}
                    placeholder="gemini-flash hoặc claude-3-5-sonnet"
                    className="input input-bordered input-sm text-xs font-mono"
                  />
                  <datalist id="target-models-list">
                    {availableTargetModels.map(m => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                  <span className="text-[10px] text-base-content/50 mt-1">Gợi ý: gemini-flash, gemini-flash-lite, gemini-pro</span>
                </div>

                <div className="form-control">
                  <label className="label py-1">
                    <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Ghi Chú (Label)</span>
                  </label>
                  <input
                    type="text"
                    value={formData.label}
                    onChange={(e) => setFormData({...formData, label: e.target.value})}
                    placeholder="VD: Dành cho Cursor AI / Claude Code"
                    className="input input-bordered input-sm text-xs"
                  />
                </div>

                {editingAlias && (
                  <div className="form-control">
                    <label className="label py-1">
                      <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Trạng Thái</span>
                    </label>
                    <label className="label cursor-pointer justify-start gap-3">
                      <input
                        type="checkbox"
                        className="toggle toggle-primary toggle-sm"
                        checked={formData.enabled}
                        onChange={(e) => setFormData({...formData, enabled: e.target.checked})}
                      />
                      <span className="label-text text-xs">{formData.enabled ? 'Đang hoạt động' : 'Đã tắt'}</span>
                    </label>
                  </div>
                )}

                <div className="sm:col-span-2 flex gap-2 justify-end pt-2">
                  <button type="button" onClick={() => { resetForm(); setShowAddForm(false); }} className="btn btn-xs sm:btn-sm btn-ghost">
                    Hủy
                  </button>
                  <button type="submit" className="btn btn-xs sm:btn-sm btn-primary">
                    {editingAlias ? 'Lưu Thay Đổi' : 'Tạo Alias'}
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* How it works Banner */}
          <div className="alert bg-base-200/50 border border-base-300/50 p-3 rounded-2xl flex items-start gap-3 text-xs">
            <Info className="w-4 h-4 text-primary shrink-0 mt-0.5" />
            <div className="leading-relaxed">
              <strong>Cơ chế Spoofing:</strong> Khi client gửi <code className="px-1 bg-base-300 rounded font-mono font-bold text-primary">model: "claude-3-5-sonnet-20241022"</code>, Router điều hướng sang <code className="px-1 bg-base-300 rounded font-mono font-bold text-secondary">gemini-flash</code> hoặc Custom Endpoint. Response trả về được spoof giữ nguyên tên gốc <code className="px-1 bg-base-300 rounded font-mono font-bold text-primary">claude-3-5-sonnet-20241022</code>. Client regex không phát hiện được sự thay đổi.
            </div>
          </div>

          {/* Aliases Table */}
          <div className="card glass-card rounded-2xl overflow-hidden border border-base-300/40">
            <div className="overflow-x-auto">
              <table className="table table-zebra table-sm">
                <thead className="bg-base-300/50 text-[11px] font-bold uppercase text-base-content/70">
                  <tr>
                    <th>Alias Name (Client gửi)</th>
                    <th>Target Model (Thực tế)</th>
                    <th>Endpoint</th>
                    <th>Ghi Chú</th>
                    <th>Phạm Vi</th>
                    <th>Trạng Thái</th>
                    <th className="text-right">Thao Tác</th>
                  </tr>
                </thead>
                <tbody className="text-xs">
                  {aliases.length === 0 ? (
                    <tr>
                      <td colSpan="7" className="text-center py-10 text-base-content/50">
                        <AlertCircle className="w-8 h-8 mx-auto mb-2 opacity-40 text-primary" />
                        Chưa có alias nào. Nhấn <strong>"Templates (Gợi ý)"</strong> hoặc <strong>"Tạo Alias Mới"</strong> ở trên để bắt đầu.
                      </td>
                    </tr>
                  ) : (
                    aliases.map(alias => (
                      <tr key={alias.alias_id} className="hover">
                        <td>
                          <div className="flex items-center gap-1.5">
                            <code className="px-2 py-0.5 bg-primary/10 text-primary rounded font-mono text-[11px] font-bold">
                              {alias.alias_name}
                            </code>
                            <button
                              onClick={() => copyToClipboard(alias.alias_name, alias.alias_id)}
                              className="btn btn-ghost btn-xs p-1"
                              title="Copy"
                            >
                              {copiedId === alias.alias_id ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3 text-base-content/50" />}
                            </button>
                          </div>
                        </td>
                        <td>
                          <code className="px-2 py-0.5 bg-secondary/10 text-secondary rounded font-mono text-[11px]">
                            {alias.target_model}
                          </code>
                        </td>
                        <td>
                          {alias.target_endpoint ? (
                            <span className="badge badge-sm badge-outline font-mono text-[10px]">{alias.target_endpoint}</span>
                          ) : (
                            <span className="badge badge-sm badge-ghost text-[10px]">Gemini Pool</span>
                          )}
                        </td>
                        <td className="text-base-content/70 max-w-xs truncate">{alias.label || '—'}</td>
                        <td>
                          {alias.account_key_id ? (
                            <span className="badge badge-xs badge-warning">Key riêng</span>
                          ) : (
                            <span className="badge badge-xs badge-ghost">Toàn tài khoản</span>
                          )}
                        </td>
                        <td>
                          {alias.enabled ? (
                            <span className="badge badge-xs badge-success gap-1">Bật</span>
                          ) : (
                            <span className="badge badge-xs badge-ghost gap-1">Tắt</span>
                          )}
                        </td>
                        <td className="text-right">
                          <div className="flex justify-end gap-1">
                            <button
                              onClick={() => startEdit(alias)}
                              className="btn btn-ghost btn-xs"
                              title="Sửa"
                            >
                              <Edit3 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={() => handleDeleteAlias(alias.alias_id)}
                              className="btn btn-ghost btn-xs text-error hover:bg-error/10"
                              title="Xóa"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ==================== SUB-TAB 2: CUSTOM ENDPOINTS ==================== */}
      {activeSubTab === 'endpoints' && (
        <div className="space-y-4">
          {/* Controls Bar */}
          <div className="flex justify-between items-center bg-base-200/40 p-3 rounded-2xl border border-base-300/40">
            <div className="text-xs text-base-content/70">
              Có <strong>{endpoints.length}</strong> Custom Endpoint đã cấu hình cho tài khoản của bạn
            </div>
            <button
              onClick={() => setShowAddEpForm(v => !v)}
              className="btn btn-primary btn-xs sm:btn-sm gap-1.5 font-bold shadow-md shadow-primary/20"
            >
              <Plus className="w-3.5 h-3.5" />
              Thêm Custom Endpoint
            </button>
          </div>

          {/* Add Endpoint Form */}
          {showAddEpForm && (
            <div className="card glass-card p-5 rounded-2xl animate-fade-in-up border border-primary/20 bg-base-100/90 shadow-xl">
              <h3 className="font-extrabold text-sm mb-4 flex items-center gap-2">
                <Network className="w-4 h-4 text-primary" />
                Thêm Custom Endpoint (OpenRouter, Ollama, vLLM, DeepSeek, Anthropic...)
              </h3>
              <form onSubmit={handleAddEndpoint} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="form-control">
                  <label className="label py-1">
                    <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Tên Endpoint</span>
                  </label>
                  <input
                    type="text"
                    required
                    value={epFormData.name}
                    onChange={(e) => setEpFormData({...epFormData, name: e.target.value})}
                    placeholder="my-openrouter hoặc deepseek-local"
                    className="input input-bordered input-sm text-xs font-mono"
                  />
                  <span className="text-[10px] text-base-content/50 mt-1">Chữ thường, không dấu cách (vd: openrouter-main)</span>
                </div>

                <div className="form-control">
                  <label className="label py-1">
                    <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Định Dạng Giao Thức (API Format)</span>
                  </label>
                  <select
                    value={epFormData.api_format}
                    onChange={(e) => setEpFormData({...epFormData, api_format: e.target.value})}
                    className="select select-bordered select-sm text-xs"
                  >
                    <option value="openai">OpenAI Compatible (OpenRouter, Ollama, vLLM, DeepSeek)</option>
                    <option value="anthropic">Anthropic Native (api.anthropic.com)</option>
                  </select>
                </div>

                <div className="form-control sm:col-span-2">
                  <label className="label py-1">
                    <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">Base URL</span>
                  </label>
                  <input
                    type="url"
                    required
                    value={epFormData.base_url}
                    onChange={(e) => setEpFormData({...epFormData, base_url: e.target.value})}
                    placeholder="https://openrouter.ai/api/v1 hoặc http://localhost:11434/v1"
                    className="input input-bordered input-sm text-xs font-mono"
                  />
                </div>

                <div className="form-control sm:col-span-2">
                  <label className="label py-1">
                    <span className="label-text text-[11px] font-bold text-base-content/60 uppercase">API Key</span>
                  </label>
                  <input
                    type="password"
                    required
                    value={epFormData.auth_key}
                    onChange={(e) => setEpFormData({...epFormData, auth_key: e.target.value})}
                    placeholder="sk-or-v1-... hoặc sk-ant-..."
                    className="input input-bordered input-sm text-xs font-mono"
                  />
                </div>

                <div className="sm:col-span-2 flex gap-2 justify-end pt-2">
                  <button type="button" onClick={() => setShowAddEpForm(false)} className="btn btn-xs sm:btn-sm btn-ghost">
                    Hủy
                  </button>
                  <button type="submit" className="btn btn-xs sm:btn-sm btn-primary">
                    Thêm & Kiểm Tra Models
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* Endpoints List */}
          <div className="grid grid-cols-1 gap-4">
            {endpoints.length === 0 ? (
              <div className="card glass-card p-10 text-center text-base-content/50 border border-base-300/40 rounded-2xl">
                <Network className="w-10 h-10 mx-auto mb-3 opacity-30 text-primary" />
                <div className="font-bold text-sm mb-1">Chưa có Custom Endpoint nào</div>
                <div className="text-xs max-w-md mx-auto">
                  Bạn có thể thêm endpoint OpenRouter, Ollama, vLLM hoặc Anthropic của chính mình để gọi và spoof model name.
                </div>
                <button onClick={() => setShowAddEpForm(true)} className="btn btn-primary btn-sm gap-2 mx-auto mt-4">
                  <Plus className="w-4 h-4" />
                  Thêm Endpoint Đầu Tiên
                </button>
              </div>
            ) : (
              endpoints.map(ep => {
                const epModels = ep.models || [];
                const isLoading = epLoadingStates[ep.name];

                return (
                  <div key={ep.name} className="card glass-card p-5 rounded-2xl border border-base-300/50 bg-base-100/70 shadow-sm space-y-4">
                    {/* Header */}
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-base-300/40 pb-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <Server className="w-4 h-4 text-primary" />
                          <h3 className="font-extrabold text-sm font-mono text-primary">{ep.name}</h3>
                          <span className="badge badge-sm badge-outline text-[10px] font-mono">{ep.api_format || 'openai'}</span>
                          {ep.enabled ? (
                            <span className="badge badge-xs badge-success">Active</span>
                          ) : (
                            <span className="badge badge-xs badge-ghost">Disabled</span>
                          )}
                        </div>
                        <div className="text-[11px] text-base-content/60 font-mono mt-0.5 truncate max-w-xl">
                          {ep.base_url}
                        </div>
                      </div>

                      <div className="flex items-center gap-1.5 self-end sm:self-auto">
                        <button
                          onClick={() => handleRefreshEpModels(ep.name)}
                          disabled={isLoading}
                          className="btn btn-xs btn-outline gap-1"
                          title="Lấy danh sách models từ URL"
                        >
                          <RefreshCw className={`w-3 h-3 ${isLoading ? 'animate-spin' : ''}`} />
                          Fetch Models
                        </button>
                        <button
                          onClick={() => handleDeleteEndpoint(ep.name)}
                          className="btn btn-xs btn-ghost text-error hover:bg-error/10"
                          title="Xóa Endpoint"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>

                    {/* Models on this endpoint */}
                    <div className="space-y-2">
                      <div className="text-[11px] font-bold text-base-content/70 flex items-center justify-between">
                        <span>Danh sách Models ({epModels.length}):</span>
                        <span className="text-[10px] text-base-content/50">Nhấn model để tạo Alias spoof</span>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        {epModels.length === 0 ? (
                          <div className="text-[11px] text-base-content/40 italic py-1">
                            Chưa có models nào. Nhấn "Fetch Models" hoặc nhập tên model bên dưới để thêm.
                          </div>
                        ) : (
                          epModels.map(m => (
                            <div
                              key={m}
                              className="group flex items-center gap-1.5 px-2.5 py-1 bg-base-200/80 hover:bg-primary/10 hover:border-primary/30 border border-base-300/60 rounded-xl transition-all"
                            >
                              <code className="text-[11px] font-mono text-base-content/80 group-hover:text-primary">{m}</code>
                              <button
                                onClick={() => handleCreateAliasFromEpModel(ep.name, m)}
                                className="btn btn-ghost btn-xs p-0.5 text-primary opacity-60 group-hover:opacity-100"
                                title="Tạo Alias cho model này"
                              >
                                <ArrowRight className="w-3 h-3" />
                              </button>
                            </div>
                          ))
                        )}
                      </div>

                      {/* Manual Add Model Input */}
                      <div className="flex gap-2 pt-2 max-w-md">
                        <input
                          type="text"
                          value={newModelInput[ep.name] || ''}
                          onChange={(e) => setNewModelInput({...newModelInput, [ep.name]: e.target.value})}
                          placeholder="Thêm model thủ công (VD: anthropic/claude-3.5-sonnet)"
                          className="input input-bordered input-xs flex-1 text-xs font-mono"
                          onKeyDown={(e) => { if (e.key === 'Enter') handleAddCustomModelToEp(ep.name); }}
                        />
                        <button
                          onClick={() => handleAddCustomModelToEp(ep.name)}
                          className="btn btn-xs btn-primary font-bold"
                        >
                          + Thêm Model
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
