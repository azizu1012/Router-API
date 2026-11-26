import { useState, useEffect, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import {
  Plus, Trash2, Edit3, Sparkles, Copy, Check, Info, AlertCircle,
  Network, RefreshCw, Server, ArrowRight, Tag
} from 'lucide-react';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Badge from '../components/Badge';
import Alert from '../components/Alert';
import Card from '../components/Card';
import Button from '../components/Button';
import { copyText } from '../utils/clipboard';

/* Template suggestions for quick setup.
   Display strings are i18n keys (`label_key` / `description_key`), not values:
   `label` used to be a Vietnamese literal that was both rendered AND submitted
   to the server, so switching to EN/JP left Vietnamese on screen and stored a
   Vietnamese label on the alias. The key is resolved at render and at submit. */
const ALIAS_TEMPLATES = [
  // --- CLAUDE (Anthropic) ---
  {
    id: 'claude-3-7-sonnet',
    category: 'claude',
    name: 'Claude 3.7 Sonnet (Hybrid Reasoning)',
    alias_name: 'claude-3-7-sonnet-20250219',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label_key: 'mal_tpl_claude37_label',
    description_key: 'mal_tpl_claude37_desc'
  },
  {
    id: 'claude-3-5-sonnet',
    category: 'claude',
    name: 'Claude 3.5 Sonnet v2',
    alias_name: 'claude-3-5-sonnet-20241022',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label_key: 'mal_tpl_claude35_label',
    description_key: 'mal_tpl_claude35_desc'
  },
  {
    id: 'claude-sonnet-5-5',
    category: 'claude',
    name: 'Claude Sonnet 5.5 (Next-Gen Relay)',
    alias_name: 'claude-sonnet-5-5',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label_key: 'mal_tpl_sonnet55_label',
    description_key: 'mal_tpl_sonnet55_desc'
  },
  {
    id: 'claude-sonnet-5',
    category: 'claude',
    name: 'Claude Sonnet 5 (Next-Gen)',
    alias_name: 'claude-sonnet-5',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label_key: 'mal_tpl_sonnet5_label',
    description_key: 'mal_tpl_sonnet5_desc'
  },
  {
    id: 'claude-3-5-haiku',
    category: 'claude',
    name: 'Claude 3.5 Haiku (Fast)',
    alias_name: 'claude-3-5-haiku-20241022',
    target_model: 'gemini-flash-lite',
    target_endpoint: null,
    label_key: 'mal_tpl_haiku35_label',
    description_key: 'mal_tpl_haiku35_desc'
  },
  {
    id: 'claude-haiku-5-5',
    category: 'claude',
    name: 'Claude Haiku 5.5 (Next-Gen Fast)',
    alias_name: 'claude-haiku-5-5',
    target_model: 'gemini-flash-lite',
    target_endpoint: null,
    label_key: 'mal_tpl_haiku55_label',
    description_key: 'mal_tpl_haiku55_desc'
  },
  {
    id: 'claude-opus-5-5',
    category: 'claude',
    name: 'Claude Opus 5.5 (Deep Reasoning)',
    alias_name: 'claude-opus-5-5',
    target_model: 'gemini-4-hydrogen',
    target_endpoint: null,
    label_key: 'mal_tpl_opus55_label',
    description_key: 'mal_tpl_opus55_desc'
  },
  {
    id: 'claude-3-opus',
    category: 'claude',
    name: 'Claude 3 Opus (Classic Flagship)',
    alias_name: 'claude-3-opus-20240229',
    target_model: 'gemini-4-hydrogen',
    target_endpoint: null,
    label_key: 'mal_tpl_opus3_label',
    description_key: 'mal_tpl_opus3_desc'
  },

  // --- OPENAI (GPT & Reasoning) ---
  {
    id: 'gpt-4-5-preview',
    category: 'openai',
    name: 'GPT-4.5 Preview (Orion Flagship)',
    alias_name: 'gpt-4.5-preview',
    target_model: 'gemini-4-hydrogen',
    target_endpoint: null,
    label_key: 'mal_tpl_gpt45_label',
    description_key: 'mal_tpl_gpt45_desc'
  },
  {
    id: 'o3-mini',
    category: 'openai',
    name: 'OpenAI o3-mini (Reasoning Coder)',
    alias_name: 'o3-mini',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label_key: 'mal_tpl_o3mini_label',
    description_key: 'mal_tpl_o3mini_desc'
  },
  {
    id: 'o1',
    category: 'openai',
    name: 'OpenAI o1 (Full Reasoning)',
    alias_name: 'o1',
    target_model: 'gemini-4-hydrogen',
    target_endpoint: null,
    label_key: 'mal_tpl_o1_label',
    description_key: 'mal_tpl_o1_desc'
  },
  {
    id: 'gpt-4o',
    category: 'openai',
    name: 'GPT-4o (Omni Flagship)',
    alias_name: 'gpt-4o',
    target_model: 'gemini-flash',
    target_endpoint: null,
    label_key: 'mal_tpl_gpt4o_label',
    description_key: 'mal_tpl_gpt4o_desc'
  },
  {
    id: 'gpt-4o-mini',
    category: 'openai',
    name_key: 'mal_tpl_gpt4omini_name',
    alias_name: 'gpt-4o-mini',
    target_model: 'gemini-flash-lite',
    target_endpoint: null,
    label_key: 'mal_tpl_gpt4omini_label',
    description_key: 'mal_tpl_gpt4omini_desc'
  },
  {
    id: 'gpt-5',
    category: 'openai',
    name: 'GPT-5 (Speculative Next-Gen)',
    alias_name: 'gpt-5',
    target_model: 'gemini-4-hydrogen',
    target_endpoint: null,
    label_key: 'mal_tpl_gpt5_label',
    description_key: 'mal_tpl_gpt5_desc'
  },

  // --- CUSTOM ENDPOINTS ---
  {
    id: 'custom-claude-to-gpt',
    category: 'custom',
    name: 'Custom Endpoint: GPT-4o → Claude Sonnet',
    alias_name: 'gpt-4o',
    target_model: 'claude-sonnet-5-5',
    target_endpoint: '',
    label_key: 'mal_tpl_cust_a_label',
    description_key: 'mal_tpl_cust_a_desc'
  },
  {
    id: 'custom-gpt-to-claude',
    category: 'custom',
    name: 'Custom Endpoint: Claude 3.7 → Custom DeepSeek',
    alias_name: 'claude-3-7-sonnet-20250219',
    target_model: 'deepseek-v4.1-flash',
    target_endpoint: '',
    label_key: 'mal_tpl_cust_b_label',
    description_key: 'mal_tpl_cust_b_desc'
  }
];

export default function ModelAliasesTab() {
  const { token, lang, user } = useApp();
  const isAdmin = user?.tier === 'admin';

  const [activeSubTab, setActiveSubTab] = useState('aliases'); // 'aliases' | 'endpoints'
  const [aliases, setAliases] = useState([]);
  const [endpoints, setEndpoints] = useState([]);
  const [internalTargetModels, setInternalTargetModels] = useState([
    'gemini-flash',
    'gemini-flash-lite',
    'gemini-4-hydrogen',
    'gemini-flash-25',
    'gemma-4-26b',
  ]);
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
    enabled: true,
    alias_type: 'single',
    strategy: 'waterfall',
    targets: [{ model: '', endpoint: '', priority: 1, weight: 1 }]
  });

  const isCombo = formData.alias_type === 'combo';
  const parseTargets = (alias) => {
    try {
      const parsed = typeof alias.targets === 'string' ? JSON.parse(alias.targets || '[]') : alias.targets;
      return Array.isArray(parsed) && parsed.length
        ? parsed.map((target, index) => ({
            model: target.model || '',
            endpoint: target.endpoint || target.target_endpoint || '',
            priority: target.priority || index + 1,
            weight: target.weight || 1
          }))
        : [{ model: alias.target_model || '', endpoint: alias.target_endpoint || '', priority: 1, weight: 1 }];
    } catch {
      return [{ model: alias.target_model || '', endpoint: alias.target_endpoint || '', priority: 1, weight: 1 }];
    }
  };

  const updateTarget = (index, field, value) => {
    setFormData(prev => ({
      ...prev,
      targets: prev.targets.map((target, i) => i === index ? { ...target, [field]: value } : target)
    }));
  };

  const addTarget = () => setFormData(prev => ({
    ...prev,
    targets: [...prev.targets, { model: '', endpoint: '', priority: prev.targets.length + 1, weight: 1 }]
  }));

  const removeTarget = (index) => setFormData(prev => ({
    ...prev,
    targets: prev.targets.length <= 1 ? prev.targets : prev.targets.filter((_, i) => i !== index)
  }));

  const setMsg = useCallback((text, type = 'info') => {
    setMsgState({ text, type });
    if (type === 'success') {
      setTimeout(() => setMsgState({ text: '', type: '' }), 4000);
    }
  }, []);

  /* Both loaders take an `isCurrent` predicate. Only the mount effect passes
     one — it must not paint a response that arrives after the token or the
     admin flag has changed. The mutation handlers below call them bare. */
  const loadAliases = useCallback(async (isCurrent = () => true) => {
    try {
      setLoading(true);
      const url = isAdmin ? '/dashboard/admin/aliases' : '/api/me/aliases';
      const data = await api(url, { method: 'GET' }, token);
      if (!isCurrent()) return;
      const list = Array.isArray(data) ? data : (data.aliases || []);
      setAliases(list);
    } catch (err) {
      if (!isCurrent()) return;
      setMsg(`${t('mal_load_err', lang)}${err.message}`, 'error');
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [isAdmin, token, setMsg, lang]);

  const loadEndpoints = useCallback(async (isCurrent = () => true) => {
    try {
      const url = isAdmin ? '/dashboard/admin/endpoints' : '/api/me/endpoints';
      const data = await api(url, { method: 'GET' }, token);
      if (!isCurrent()) return;
      const list = Array.isArray(data) ? data : (data.endpoints || data.ep || []);
      setEndpoints(list);
    } catch (err) {
      // A missing endpoint list degrades the pickers, not the page, so this
      // stays out of the banner the operator sees.
      if (!isCurrent()) return;
      setMsg(`${t('mal_ep_load_err', lang)}${err.message}`, 'error');
    }
  }, [isAdmin, token, setMsg, lang]);

  useEffect(() => {
    let alive = true;
    (async () => {
      await loadAliases(() => alive);
      await loadEndpoints(() => alive);
      try {
        const poolRes = await api('/api/model-pools', { method: 'GET' }, token);
        if (!alive) return;
        if (Array.isArray(poolRes?.target_models) && poolRes.target_models.length > 0) {
          setInternalTargetModels(poolRes.target_models);
        }
      } catch {
        /* keep fallback internal targets */
      }
    })();
    return () => { alive = false; };
  }, [loadAliases, loadEndpoints, token]);

  const handleAddAlias = async (e) => {
    e.preventDefault();
    const comboTargets = formData.targets.filter(tg => tg.model.trim()).map(tg => ({
      model: tg.model.trim(),
      endpoint: tg.endpoint || null,
      priority: Number(tg.priority) || 1,
      weight: Number(tg.weight) || 1
    }));
    if (!formData.alias_name.trim() || (isCombo ? comboTargets.length === 0 : !formData.target_model.trim())) {
      setMsg(t(isCombo ? 'mal_err_combo_need' : 'mal_err_target_need', lang), 'error');
      return;
    }
    const primaryTarget = comboTargets[0];
    try {
      const url = isAdmin ? '/dashboard/admin/aliases' : '/api/me/aliases';
      await api(url, {
        method: 'POST',
        body: JSON.stringify({
          alias_name: formData.alias_name.trim(),
          target_model: isCombo ? primaryTarget.model : formData.target_model.trim(),
          target_endpoint: isCombo ? (primaryTarget.endpoint || null) : (formData.target_endpoint || null),
          label: formData.label.trim(),
          account_key_id: formData.key_id || null,
          enabled: true,
          targets: isCombo ? comboTargets : '',
          strategy: isCombo ? formData.strategy : 'waterfall'
        })
      }, token);
      setMsg(t('mal_created', lang), 'success');
      resetForm();
      loadAliases();
      setShowAddForm(false);
    } catch (err) {
      setMsg(`${t('mal_err_create', lang)}${err.message}`, 'error');
    }
  };

  const handleUpdateAlias = async (e) => {
    e.preventDefault();
    const comboTargets = formData.targets.filter(tg => tg.model.trim()).map(tg => ({
      model: tg.model.trim(),
      endpoint: tg.endpoint || null,
      priority: Number(tg.priority) || 1,
      weight: Number(tg.weight) || 1
    }));
    if (isCombo && comboTargets.length === 0) {
      setMsg(t('mal_err_combo_need', lang), 'error');
      return;
    }
    const primaryTarget = comboTargets[0];
    try {
      const url = isAdmin
        ? `/dashboard/admin/aliases/${editingAlias.alias_id}`
        : `/api/me/aliases/${editingAlias.alias_id}`;
      await api(url, {
        method: 'PATCH',
        body: JSON.stringify({
          target_model: isCombo ? primaryTarget.model : formData.target_model.trim(),
          target_endpoint: isCombo ? (primaryTarget.endpoint || null) : (formData.target_endpoint || null),
          label: formData.label.trim(),
          enabled: formData.enabled,
          targets: isCombo ? comboTargets : '',
          strategy: isCombo ? formData.strategy : 'waterfall'
        })
      }, token);
      setMsg(t('mal_updated', lang), 'success');
      resetForm();
      loadAliases();
      setEditingAlias(null);
      setShowAddForm(false);
    } catch (err) {
      setMsg(`${t('mal_err_update', lang)}${err.message}`, 'error');
    }
  };

  const handleDeleteAlias = async (aliasId) => {
    if (!window.confirm(t('mal_delete_confirm', lang))) return;
    try {
      const url = isAdmin
        ? `/dashboard/admin/aliases/${aliasId}`
        : `/api/me/aliases/${aliasId}`;
      await api(url, { method: 'DELETE' }, token);
      setMsg(t('mal_deleted', lang), 'success');
      loadAliases();
    } catch (err) {
      setMsg(`${t('mal_err_delete', lang)}${err.message}`, 'error');
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
      setMsg(t('mal_ep_added', lang), 'success');
      setEpFormData({ name: '', base_url: '', auth_key: '', api_format: 'openai' });
      setShowAddEpForm(false);
      loadEndpoints();
    } catch (err) {
      setMsg(`${t('mal_err_ep_add', lang)}${err.message}`, 'error');
    }
  };

  const handleDeleteEndpoint = async (name) => {
    if (!window.confirm(t('mal_ep_delete_confirm', lang).replace('{name}', name))) return;
    try {
      const url = isAdmin ? '/dashboard/admin/endpoints/delete' : `/api/me/endpoints/${name}`;
      const options = isAdmin
        ? { method: 'POST', body: JSON.stringify({ name }) }
        : { method: 'DELETE' };
      await api(url, options, token);
      setMsg(t('mal_ep_deleted', lang).replace('{name}', name), 'success');
      loadEndpoints();
    } catch (err) {
      setMsg(`${t('mal_err_ep_delete', lang)}${err.message}`, 'error');
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
      setMsg(t('mal_ep_refreshed', lang).replace('{n}', count).replace('{name}', name), 'success');
      loadEndpoints();
    } catch (err) {
      setMsg(`${t('mal_err_ep_refresh', lang)}${err.message}`, 'error');
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
      setMsg(t('mal_model_added', lang).replace('{model}', modelId).replace('{name}', name), 'success');
      setNewModelInput(prev => ({ ...prev, [name]: '' }));
      loadEndpoints();
    } catch (err) {
      setMsg(`${t('mal_err_model_add', lang)}${err.message}`, 'error');
    }
  };

  const handleCreateAliasFromEpModel = (endpointName, modelId) => {
    setFormData({
      alias_name: '',
      target_model: modelId,
      target_endpoint: endpointName,
      label: `Spoof for ${endpointName} / ${modelId}`,
      key_id: null,
      enabled: true,
      alias_type: 'single',
      strategy: 'waterfall',
      targets: [{ model: modelId, endpoint: endpointName, priority: 1, weight: 1 }]
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
      label: t(template.label_key, lang),
      key_id: null,
      enabled: true,
      alias_type: 'single',
      strategy: 'waterfall',
      targets: [{ model: template.target_model, endpoint: template.target_endpoint || '', priority: 1, weight: 1 }]
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
      enabled: true,
      alias_type: 'single',
      strategy: 'waterfall',
      targets: [{ model: '', endpoint: '', priority: 1, weight: 1 }]
    });
    setEditingAlias(null);
  };

  const startEdit = (alias) => {
    setEditingAlias(alias);
    const parsedTargets = parseTargets(alias);
    setFormData({
      alias_name: alias.alias_name,
      target_model: alias.target_model || parsedTargets[0]?.model || '',
      target_endpoint: alias.target_endpoint,
      label: alias.label || '',
      key_id: alias.account_key_id,
      enabled: alias.enabled,
      /* Prefer the stored type. Inferring from `targets` alone promoted a
         legacy 1-1 alias to "combo" on every edit, because the backend fills
         a single-element targets array even for single aliases — so saving an
         untouched row silently changed what it was. */
      alias_type: alias.alias_type
        ? alias.alias_type
        : (parsedTargets.length > 1 ? 'combo' : 'single'),
      strategy: alias.strategy || 'waterfall',
      targets: parsedTargets
    });
    setShowAddForm(true);
    setShowTemplates(false);
  };

  const copyToClipboard = async (text, id) => {
    if (!await copyText(text)) return;
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Get available models for selected endpoint in form
  const selectedEndpointObj = endpoints.find(e => e.name === formData.target_endpoint);
  const availableTargetModels = selectedEndpointObj
    ? (selectedEndpointObj.enabled_models && selectedEndpointObj.enabled_models.length > 0
        ? selectedEndpointObj.enabled_models
        : (selectedEndpointObj.models || []))
    : internalTargetModels;

if (loading && aliases.length === 0) {
    return <Loading message={t('loading', lang)} />;
  }

  const codeCls = 'px-1 rounded font-mono';
  const codeStyle = { background: 'var(--c-surface-3)', color: 'var(--c-ink)' };
  // The explanation is one sentence with two inline code slots, so translators
  // get the whole sentence and the markup stays out of the string table.
  const [spoofA, spoofRest = ''] = t('mal_spoof_body', lang).split('{alias}');
  const [spoofB, spoofC = ''] = spoofRest.split('{target}');

  const segStyle = (active) => ({
    background: active ? 'var(--c-surface)' : 'transparent',
    color: active ? 'var(--c-ink)' : 'var(--c-ink-3)',
    boxShadow: active ? 'var(--c-shadow)' : 'none',
  });

  return (
    <div className="space-y-5 animate-tab-in">
      <PageHeader
        title={t('mal_title', lang)}
        subtitle={t('mal_sub', lang)}
        actions={
          <div className="flex p-1 gap-0.5 rounded-lg"
               style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
            <button
              onClick={() => setActiveSubTab('aliases')}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors"
              style={segStyle(activeSubTab === 'aliases')}
              aria-pressed={activeSubTab === 'aliases'}
            >
              <Tag className="w-3.5 h-3.5" />
              {t('mal_tab_aliases', lang)}
              <span className="tabular-nums" style={{ color: 'var(--c-ink-3)' }}>{aliases.length}</span>
            </button>
            <button
              onClick={() => setActiveSubTab('endpoints')}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors"
              style={segStyle(activeSubTab === 'endpoints')}
              aria-pressed={activeSubTab === 'endpoints'}
            >
              <Server className="w-3.5 h-3.5" />
              {t('mal_tab_endpoints', lang)}
              <span className="tabular-nums" style={{ color: 'var(--c-ink-3)' }}>{endpoints.length}</span>
            </button>
          </div>
        }
      />

      {msgState.text && <Alert type={msgState.type} message={msgState.text} />}

      {/* ==================== SUB-TAB 1: MODEL ALIASES ==================== */}
      {activeSubTab === 'aliases' && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
              {t('mal_count_aliases', lang).replace('{n}', aliases.length)}
            </p>
            <div className="flex gap-2">
              <Button
                variant={showTemplates ? 'primary' : 'outline'}
                icon={Sparkles}
                onClick={() => { setShowTemplates(v => !v); setShowAddForm(false); }}
                aria-pressed={showTemplates}
              >
                {t('mal_btn_templates', lang)}
              </Button>
              <Button
                icon={Plus}
                onClick={() => { setShowAddForm(v => !v); setShowTemplates(false); resetForm(); }}
              >
                {t('mal_btn_new_alias', lang)}
              </Button>
            </div>
          </div>

          {showTemplates && (
            <Card padding="lg" className="animate-fade-in-up">
              <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
                <h3 className="text-xs font-semibold flex items-center gap-1.5" style={{ color: 'var(--c-ink)' }}>
                  <Sparkles className="w-4 h-4" style={{ color: 'var(--c-accent)' }} />
                  {t('mal_tpl_title', lang)}
                </h3>
                <span className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
                  {t('mal_tpl_latest', lang)}
                </span>
              </div>
              <p className="text-[0.6875rem] mb-3" style={{ color: 'var(--c-ink-3)' }}>
                {t('mal_tpl_hint', lang)}
              </p>
              <div className="flex gap-1.5 flex-wrap mb-3.5">
                {/* Filter values stay English ids; only the visible chip goes
                    through t(), so the comparison never depends on language. */}
                {[
                  { id: 'All', label: t('mal_tpl_all', lang) },
                  { id: 'claude', label: t('mal_cat_claude', lang) },
                  { id: 'openai', label: t('mal_cat_openai', lang) },
                  { id: 'custom', label: t('mal_cat_custom', lang) },
                ].map(cat => (
                  <button
                    key={cat.id}
                    type="button"
                    onClick={() => setTemplateCategory(cat.id)}
                    className="px-2.5 py-1 rounded-md text-[0.6875rem] font-medium transition-colors"
                    style={{
                      background: templateCategory === cat.id ? 'var(--c-accent-soft)' : 'var(--c-surface-2)',
                      border: `1px solid ${templateCategory === cat.id ? 'color-mix(in srgb, var(--c-accent) 35%, transparent)' : 'var(--c-line)'}`,
                      color: templateCategory === cat.id ? 'var(--c-accent)' : 'var(--c-ink-3)',
                    }}
                    aria-pressed={templateCategory === cat.id}
                  >
                    {cat.label}
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {(templateCategory === 'All' ? ALIAS_TEMPLATES : ALIAS_TEMPLATES.filter(tpl => tpl.category === templateCategory)).map(template => (
                  <button
                    key={template.id}
                    onClick={() => applyTemplate(template)}
                    className="glass-card-interactive rounded-xl p-3.5 text-left"
                    style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}
                  >
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <span className="text-xs font-medium" style={{ color: 'var(--c-ink)' }}>
                        {template.name_key ? t(template.name_key, lang) : template.name}
                      </span>
                      <Badge variant="ghost" size="xs">
                        {t(`mal_cat_${template.category}`, lang)}
                      </Badge>
                    </div>
                    <div className="text-[0.6875rem] leading-relaxed mb-2" style={{ color: 'var(--c-ink-2)' }}>
                      {t(template.description_key, lang)}
                    </div>
                    <div className="flex flex-col gap-1 text-[0.6875rem] font-mono p-2 rounded-lg"
                         style={{ background: 'var(--c-surface-3)' }}>
                      <div className="truncate">
                        <span style={{ color: 'var(--c-ink-3)' }}>{t('mal_tpl_client', lang)}: </span>
                        <span style={{ color: 'var(--c-accent)' }}>{template.alias_name}</span>
                      </div>
                      <div className="truncate">
                        <span style={{ color: 'var(--c-ink-3)' }}>{t('mal_tpl_router', lang)}: </span>
                        <span style={{ color: 'var(--c-ink)' }}>{template.target_model}</span>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </Card>
          )}

          {showAddForm && (
            <Card padding="lg" className="animate-fade-in-up">
              <h3 className="text-xs font-semibold mb-4 flex items-center gap-1.5" style={{ color: 'var(--c-ink)' }}>
                {editingAlias
                  ? <Edit3 className="w-4 h-4" style={{ color: 'var(--c-warn)' }} />
                  : <Plus className="w-4 h-4" style={{ color: 'var(--c-accent)' }} />}
                {editingAlias ? t('mal_form_edit', lang) : t('mal_form_create', lang)}
              </h3>
              <form onSubmit={editingAlias ? handleUpdateAlias : handleAddAlias}
                    className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <label className="block">
                  <span className="label-micro block mb-1.5">{t('mal_f_alias_name', lang)}</span>
                  <input
                    type="text"
                    required
                    disabled={!!editingAlias}
                    value={formData.alias_name}
                    onChange={(e) => setFormData({...formData, alias_name: e.target.value})}
                    placeholder="claude-3-5-sonnet-20241022, gpt-4"
                    className="input input-sm w-full text-xs font-mono"
                    autoComplete="off"
                  />
                  <span className="block text-[0.6875rem] mt-1" style={{ color: 'var(--c-ink-3)' }}>
                    {t('mal_f_alias_hint', lang)}
                  </span>
                </label>

                <div className="sm:col-span-2 flex gap-0.5 p-1 rounded-lg"
                     style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
                  <button
                    type="button"
                    onClick={() => setFormData(prev => ({ ...prev, alias_type: 'single' }))}
                    className="flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors"
                    style={segStyle(!isCombo)}
                    aria-pressed={!isCombo}
                  >
                    {t('mal_f_type_single', lang)}
                  </button>
                  <button
                    type="button"
                    onClick={() => setFormData(prev => ({ ...prev, alias_type: 'combo' }))}
                    className="flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors"
                    style={segStyle(isCombo)}
                    aria-pressed={isCombo}
                  >
                    {t('mal_f_type_combo', lang)}
                  </button>
                </div>

                <label className="block">
                  <span className="label-micro block mb-1.5">{t('mal_f_endpoint', lang)}</span>
                  <select
                    value={formData.target_endpoint || ''}
                    onChange={(e) => setFormData({...formData, target_endpoint: e.target.value || null})}
                    className="select select-sm w-full text-xs"
                  >
                    <option value="">{t('mal_f_endpoint_default', lang)}</option>
                    {endpoints.map(ep => (
                      <option key={ep.name} value={ep.name}>{ep.name} ({ep.api_format || 'openai'})</option>
                    ))}
                  </select>
                  <span className="block text-[0.6875rem] mt-1" style={{ color: 'var(--c-ink-3)' }}>
                    {t('mal_f_endpoint_hint', lang)}
                  </span>
                </label>

                <label className="block">
                  <span className="label-micro block mb-1.5">{t('mal_f_target_model', lang)}</span>
                  <input
                    type="text"
                    required={!isCombo}
                    list="target-models-list"
                    value={formData.target_model}
                    onChange={(e) => setFormData({...formData, target_model: e.target.value})}
                    placeholder="gemini-flash, claude-3-5-sonnet"
                    className="input input-sm w-full text-xs font-mono"
                    autoComplete="off"
                  />
                  <datalist id="target-models-list">
                    {availableTargetModels.map(m => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                  <span className="block text-[0.6875rem] mt-1" style={{ color: 'var(--c-ink-3)' }}>
                    {t('mal_f_target_hint', lang)}
                  </span>
                </label>

                {isCombo && (
                  <div className="sm:col-span-2 rounded-lg p-3 space-y-3"
                       style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <span className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>
                          {t('mal_f_combo_targets', lang)}
                        </span>
                        <p className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
                          {t('mal_f_combo_hint', lang)}
                        </p>
                      </div>
                      <select className="select select-xs text-xs" value={formData.strategy}
                              onChange={e => setFormData(prev => ({ ...prev, strategy: e.target.value }))}
                              aria-label={t('mal_f_strategy', lang)}>
                        <option value="waterfall">Waterfall</option>
                        <option value="load_balance">Load balance</option>
                      </select>
                    </div>
                    {formData.targets.map((target, index) => (
                      <div key={index} className="grid grid-cols-1 sm:grid-cols-12 gap-2 items-end">
                        <input className="input input-xs sm:col-span-5 font-mono" placeholder={t('mal_f_model', lang)}
                               aria-label={t('mal_f_model', lang)}
                               value={target.model} required
                               onChange={e => updateTarget(index, 'model', e.target.value)} />
                        <select className="select select-xs sm:col-span-4" value={target.endpoint || ''}
                                aria-label={t('mal_th_endpoint', lang)}
                                onChange={e => updateTarget(index, 'endpoint', e.target.value)}>
                          <option value="">{t('mal_pool', lang)}</option>
                          {endpoints.map(ep => <option key={ep.name} value={ep.name}>{ep.name}</option>)}
                        </select>
                        <input type="number" min="1" className="input input-xs sm:col-span-2"
                               placeholder={t('mal_f_weight', lang)}
                               aria-label={t('mal_f_weight', lang)}
                               value={target.weight}
                               onChange={e => updateTarget(index, 'weight', e.target.value)} />
                        <Button type="button" size="xs" variant="ghost" icon={Trash2}
                                className="btn-square"
                                aria-label={t('mal_f_remove_target', lang)}
                                title={t('mal_f_remove_target', lang)}
                                disabled={formData.targets.length <= 1}
                                onClick={() => removeTarget(index)}
                                style={{ color: 'var(--c-critical)' }} />
                      </div>
                    ))}
                    <Button type="button" size="xs" variant="outline" icon={Plus} onClick={addTarget}>
                      {t('mal_f_add_target', lang)}
                    </Button>
                  </div>
                )}

                <label className="block">
                  <span className="label-micro block mb-1.5">{t('mal_f_label', lang)}</span>
                  <input
                    type="text"
                    value={formData.label}
                    onChange={(e) => setFormData({...formData, label: e.target.value})}
                    placeholder={t('mal_f_label_ph', lang)}
                    className="input input-sm w-full text-xs"
                    autoComplete="off"
                  />
                </label>

                {editingAlias && (
                  <div className="block">
                    <span className="label-micro block mb-1.5">{t('mal_f_status', lang)}</span>
                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        className="toggle toggle-primary toggle-sm"
                        checked={formData.enabled}
                        onChange={(e) => setFormData({...formData, enabled: e.target.checked})}
                      />
                      <span className="text-xs" style={{ color: 'var(--c-ink-2)' }}>
                        {formData.enabled ? t('mal_f_active', lang) : t('mal_f_inactive', lang)}
                      </span>
                    </label>
                  </div>
                )}

                <div className="sm:col-span-2 flex gap-2 justify-end pt-3"
                     style={{ borderTop: '1px solid var(--c-line)' }}>
                  <Button type="button" variant="ghost"
                          onClick={() => { resetForm(); setShowAddForm(false); }}>
                    {t('btn_cancel', lang)}
                  </Button>
                  <Button type="submit">
                    {editingAlias ? t('mal_f_save', lang) : t('mal_f_create', lang)}
                  </Button>
                </div>
              </form>
            </Card>
          )}

          <Card variant="flat" padding="sm">
            <div className="flex items-start gap-2.5 text-[0.6875rem] leading-relaxed"
                 style={{ color: 'var(--c-ink-2)' }}>
              <Info className="w-4 h-4 shrink-0 mt-px" style={{ color: 'var(--c-accent)' }} />
              <div className="min-w-0">
                <strong style={{ color: 'var(--c-ink)' }}>{t('mal_spoof_title', lang)}</strong>{' '}
                {spoofA}
                <code className={codeCls} style={codeStyle}>claude-3-5-sonnet-20241022</code>
                {spoofB}
                <code className={codeCls} style={codeStyle}>gemini-flash</code>
                {spoofC}
              </div>
            </div>
          </Card>

          <div className="glass-card rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="table w-full min-w-[48rem]">
                <thead>
                  <tr>
                    <th className="min-w-[14rem]">{t('mal_th_alias', lang)}</th>
                    <th className="min-w-[11rem]">{t('mal_th_target', lang)}</th>
                    <th className="min-w-[8rem]">{t('mal_th_endpoint', lang)}</th>
                    <th className="min-w-[8rem]">{t('mal_th_note', lang)}</th>
                    <th className="min-w-[7rem]">{t('mal_th_scope', lang)}</th>
                    <th className="min-w-[6rem]">{t('mal_th_status', lang)}</th>
                    <th className="text-right min-w-[6rem]">{t('th_actions', lang)}</th>
                  </tr>
                </thead>
                <tbody>
                  {aliases.length === 0 ? (
                    <tr>
                      <td colSpan="7" className="text-center py-10" style={{ color: 'var(--c-ink-3)' }}>
                        <AlertCircle className="w-6 h-6 mx-auto mb-2" style={{ color: 'var(--c-ink-3)' }} />
                        {t('mal_empty', lang)}
                      </td>
                    </tr>
                  ) : (
                    aliases.map(alias => (
                      <tr key={alias.alias_id}>
                        <td>
                          <div className="flex items-center gap-1.5">
                            <code className="px-1.5 py-0.5 rounded font-mono text-[0.6875rem] font-medium"
                                  style={{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }}>
                              {alias.alias_name}
                            </code>
                            <Button size="xs" variant="ghost" className="btn-square"
                                    icon={copiedId === alias.alias_id ? Check : Copy}
                                    aria-label={t('mal_copy', lang)}
                                    title={t('mal_copy', lang)}
                                    onClick={() => copyToClipboard(alias.alias_name, alias.alias_id)}
                                    style={{ color: copiedId === alias.alias_id ? 'var(--c-good)' : 'var(--c-ink-3)' }} />
                          </div>
                        </td>
                        <td>
                          {alias.targets && alias.targets !== '[]' ? (
                            <div className="flex flex-wrap items-center gap-1">
                              <Badge variant="primary" size="xs">{t('mal_combo', lang)}</Badge>
                              {parseTargets(alias).map((target, index) => (
                                <code key={index} className="px-1 py-0.5 rounded font-mono text-[0.625rem]"
                                      style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink-2)' }}>
                                  {target.model}
                                </code>
                              ))}
                            </div>
                          ) : (
                            <code className="px-1.5 py-0.5 rounded font-mono text-[0.6875rem]"
                                  style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink-2)' }}>
                              {alias.target_model}
                            </code>
                          )}
                        </td>
                        <td>
                          {alias.target_endpoint ? (
                            <code className="font-mono text-[0.6875rem]" style={{ color: 'var(--c-ink-2)' }}>
                              {alias.target_endpoint}
                            </code>
                          ) : (
                            <span className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>{t('mal_pool', lang)}</span>
                          )}
                        </td>
                        <td className="max-w-[12rem] truncate" style={{ color: 'var(--c-ink-2)' }}>{alias.label || '—'}</td>
                        <td>
                          <span className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
                            {alias.account_key_id ? t('mal_key_only', lang) : t('mal_account_wide', lang)}
                          </span>
                        </td>
                        <td>
                          {alias.enabled
                            ? <Badge variant="success" size="xs" dot>{t('mal_on', lang)}</Badge>
                            : <Badge variant="ghost" size="xs">{t('mal_off', lang)}</Badge>}
                        </td>
                        <td className="text-right">
                          <div className="flex justify-end gap-1">
                            <Button size="xs" variant="ghost" className="btn-square" icon={Edit3}
                                    aria-label={t('mal_edit', lang)} title={t('mal_edit', lang)}
                                    onClick={() => startEdit(alias)} />
                            <Button size="xs" variant="ghost" className="btn-square" icon={Trash2}
                                    aria-label={t('mal_delete', lang)} title={t('mal_delete', lang)}
                                    onClick={() => handleDeleteAlias(alias.alias_id)}
                                    style={{ color: 'var(--c-critical)' }} />
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
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
              {t('mal_ep_count', lang).replace('{n}', endpoints.length)}
            </p>
            <Button icon={Plus} onClick={() => setShowAddEpForm(v => !v)} aria-pressed={showAddEpForm}>
              {t('mal_ep_add', lang)}
            </Button>
          </div>

          {showAddEpForm && (
            <Card padding="lg" className="animate-fade-in-up">
              <h3 className="text-xs font-semibold mb-1 flex items-center gap-1.5" style={{ color: 'var(--c-ink)' }}>
                <Network className="w-4 h-4" style={{ color: 'var(--c-accent)' }} />
                {t('mal_ep_form_title', lang)}
              </h3>
              <p className="text-[0.6875rem] mb-4" style={{ color: 'var(--c-ink-3)' }}>
                {t('mal_ep_form_sub', lang)}
              </p>
              <form onSubmit={handleAddEndpoint} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <label className="block">
                  <span className="label-micro block mb-1.5">{t('mal_ep_name', lang)}</span>
                  <input
                    type="text"
                    required
                    value={epFormData.name}
                    onChange={(e) => setEpFormData({...epFormData, name: e.target.value})}
                    placeholder="my-openrouter, deepseek-local"
                    className="input input-sm w-full text-xs font-mono"
                    autoComplete="off"
                  />
                  <span className="block text-[0.6875rem] mt-1" style={{ color: 'var(--c-ink-3)' }}>
                    {t('mal_ep_name_hint', lang)}
                  </span>
                </label>

                <label className="block">
                  <span className="label-micro block mb-1.5">{t('mal_ep_format', lang)}</span>
                  <select
                    value={epFormData.api_format}
                    onChange={(e) => setEpFormData({...epFormData, api_format: e.target.value})}
                    className="select select-sm w-full text-xs"
                  >
                    <option value="openai">{t('mal_ep_format_openai', lang)} — {t('mal_ep_format_openai_eg', lang)}</option>
                    <option value="anthropic">{t('mal_ep_format_anthropic', lang)}</option>
                  </select>
                </label>

                <label className="block sm:col-span-2">
                  <span className="label-micro block mb-1.5">{t('mal_ep_base_url', lang)}</span>
                  <input
                    type="url"
                    required
                    value={epFormData.base_url}
                    onChange={(e) => setEpFormData({...epFormData, base_url: e.target.value})}
                    placeholder="https://openrouter.ai/api/v1, http://localhost:11434/v1"
                    className="input input-sm w-full text-xs font-mono"
                    autoComplete="off"
                  />
                </label>

                <label className="block sm:col-span-2">
                  <span className="label-micro block mb-1.5">{t('mal_ep_key', lang)}</span>
                  <input
                    type="password"
                    required
                    value={epFormData.auth_key}
                    onChange={(e) => setEpFormData({...epFormData, auth_key: e.target.value})}
                    placeholder="sk-or-v1-… , sk-ant-…"
                    className="input input-sm w-full text-xs font-mono"
                    autoComplete="new-password"
                  />
                </label>

                <div className="sm:col-span-2 flex gap-2 justify-end pt-3"
                     style={{ borderTop: '1px solid var(--c-line)' }}>
                  <Button type="button" variant="ghost" onClick={() => setShowAddEpForm(false)}>
                    {t('btn_cancel', lang)}
                  </Button>
                  <Button type="submit">{t('mal_ep_submit', lang)}</Button>
                </div>
              </form>
            </Card>
          )}

          <div className="grid grid-cols-1 gap-4">
            {endpoints.length === 0 ? (
              <Card padding="xl">
                <div className="text-center py-6">
                  <Network className="w-10 h-10 mx-auto mb-3" style={{ color: 'var(--c-ink-3)', opacity: 0.5 }} />
                  <div className="text-sm font-semibold mb-1" style={{ color: 'var(--c-ink)' }}>
                    {t('mal_ep_empty_title', lang)}
                  </div>
                  <p className="text-xs max-w-md mx-auto leading-relaxed" style={{ color: 'var(--c-ink-3)' }}>
                    {t('mal_ep_empty_body', lang)}
                  </p>
                  <Button className="mx-auto mt-4" icon={Plus} onClick={() => setShowAddEpForm(true)}>
                    {t('mal_ep_empty_cta', lang)}
                  </Button>
                </div>
              </Card>
            ) : (
              endpoints.map(ep => {
                const epModels = ep.models || [];
                const isLoading = epLoadingStates[ep.name];

                return (
                  <Card key={ep.name} padding="lg">
                    <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3 pb-3"
                         style={{ borderBottom: '1px solid var(--c-line)' }}>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <Server className="w-4 h-4 shrink-0" style={{ color: 'var(--c-ink-3)' }} />
                          <h3 className="text-sm font-semibold font-mono truncate" style={{ color: 'var(--c-ink)' }}>
                            {ep.name}
                          </h3>
                          <Badge variant="ghost" size="xs">{ep.api_format || 'openai'}</Badge>
                          {ep.enabled
                            ? <Badge variant="success" size="xs" dot>{t('mal_on', lang)}</Badge>
                            : <Badge variant="ghost" size="xs">{t('mal_off', lang)}</Badge>}
                        </div>
                        <div className="text-[0.6875rem] font-mono mt-1 truncate"
                             style={{ color: 'var(--c-ink-3)' }}>
                          {ep.base_url}
                        </div>
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <Button size="xs" variant="outline" icon={RefreshCw}
                                loading={isLoading}
                                onClick={() => handleRefreshEpModels(ep.name)}
                                title={t('mal_ep_fetch_title', lang)}>
                          {t('mal_ep_fetch', lang)}
                        </Button>
                        <Button size="xs" variant="ghost" className="btn-square" icon={Trash2}
                                onClick={() => handleDeleteEndpoint(ep.name)}
                                title={t('mal_ep_delete_title', lang)}
                                aria-label={t('mal_ep_delete_title', lang)}
                                style={{ color: 'var(--c-critical)' }} />
                      </div>
                    </div>

                    <div className="space-y-2 pt-3">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <span className="text-[0.6875rem] font-medium" style={{ color: 'var(--c-ink-2)' }}>
                          {t('mal_ep_models', lang).replace('{n}', epModels.length)}
                        </span>
                        <span className="text-[0.6875rem]" style={{ color: 'var(--c-ink-3)' }}>
                          {t('mal_ep_models_hint', lang)}
                        </span>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        {epModels.length === 0 ? (
                          <p className="text-[0.6875rem] italic py-1" style={{ color: 'var(--c-ink-3)' }}>
                            {t('mal_ep_no_models', lang)}
                          </p>
                        ) : (
                          epModels.map(m => (
                            <div key={m}
                                 className="group flex items-center gap-1.5 pl-2.5 pr-1 py-1 rounded-lg transition-colors"
                                 style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
                              <code className="text-[0.6875rem] font-mono" style={{ color: 'var(--c-ink-2)' }}>{m}</code>
                              <Button size="xs" variant="ghost" className="btn-square" icon={ArrowRight}
                                      onClick={() => handleCreateAliasFromEpModel(ep.name, m)}
                                      title={t('mal_ep_create_alias_title', lang)}
                                      aria-label={t('mal_ep_create_alias_title', lang)} />
                            </div>
                          ))
                        )}
                      </div>

                      <div className="flex gap-2 pt-1 max-w-md">
                        <input
                          type="text"
                          value={newModelInput[ep.name] || ''}
                          onChange={(e) => setNewModelInput({...newModelInput, [ep.name]: e.target.value})}
                          placeholder={t('mal_ep_add_model_ph', lang)}
                          aria-label={t('mal_ep_add_model', lang)}
                          className="input input-xs flex-1 text-xs font-mono"
                          autoComplete="off"
                          onKeyDown={(e) => { if (e.key === 'Enter') handleAddCustomModelToEp(ep.name); }}
                        />
                        <Button size="xs" icon={Plus} onClick={() => handleAddCustomModelToEp(ep.name)}>
                          {t('mal_ep_add_model', lang)}
                        </Button>
                      </div>
                    </div>
                  </Card>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}