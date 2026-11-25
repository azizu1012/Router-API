import { useState, useEffect, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import Button from '../components/Button';
import Alert from '../components/Alert';
import { Save, RefreshCw, Sliders, Cpu, HardDrive, KeyRound, Info } from 'lucide-react';

/* Settings is a form, not a report: one column of labelled controls, a value
   chip that states what the control currently is, and hint text under each.
   Every control is a real input — a number typed here is what gets saved. */

function Section({ icon: Icon, title, children, cols = 'lg:col-span-1' }) {
  return (
    <Card padding="lg" className={cols}>
      <div className="flex items-center gap-2 mb-4 pb-2.5" style={{ borderBottom: '1px solid var(--c-line)' }}>
        <Icon className="w-4 h-4 shrink-0" style={{ color: 'var(--c-ink-3)' }} />
        <h2 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{title}</h2>
      </div>
      <div className="space-y-5">{children}</div>
    </Card>
  );
}

function Slider({ label, hint, value, min, max, step = 1, unit, onChange }) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between gap-3 mb-2">
        <span className="label-micro">{label}</span>
        <span className="text-[0.6875rem] font-medium tabular-nums px-1.5 py-0.5 rounded shrink-0"
              style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink-2)' }}>
          {value}{unit ? ` ${unit}` : ''}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value ?? min}
        onChange={e => onChange(e.target.value)}
        className="range-token"
      />
      <span className="block text-[0.6875rem] leading-snug mt-1.5" style={{ color: 'var(--c-ink-3)' }}>
        {hint}
      </span>
    </label>
  );
}

function NumberField({ label, hint, value, unit, onChange }) {
  return (
    <label className="block">
      <span className="label-micro block mb-1.5">{label}</span>
      <span className="relative block">
        <input
          type="number"
          inputMode="numeric"
          value={value ?? ''}
          onChange={e => onChange(e.target.value)}
          className="input input-sm w-full text-xs font-mono tabular-nums"
          style={{ paddingRight: unit ? '3.5rem' : undefined }}
        />
        {unit && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-[0.625rem]"
                style={{ color: 'var(--c-ink-3)' }}>
            {unit}
          </span>
        )}
      </span>
      {hint && (
        <span className="block text-[0.6875rem] leading-snug mt-1.5" style={{ color: 'var(--c-ink-3)' }}>
          {hint}
        </span>
      )}
    </label>
  );
}

export default function SettingsTab() {
  const { token, lang } = useApp();
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState({ text: '', type: '' });

  /* One loader serves the first fetch and both buttons ("Reload" in the header,
     "Discard" in the save bar), so it lives in a callback rather than inside the
     effect. The effect only fires it — guarded so a token change mid-flight does
     not let a stale response land on the new settings. */
  const fetchSettings = useCallback(async (isCurrent = () => true) => {
    setLoading(true);
    try {
      const data = await api('/dashboard/admin/settings', {}, token);
      if (!isCurrent()) return;
      setSettings(data);
      setMsg({ text: '', type: '' });
    } catch (err) {
      if (!isCurrent()) return;
      setMsg({ text: `${t('stg_load_err', lang)}${err.message}`, type: 'error' });
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [token, lang]);

  useEffect(() => {
    let alive = true;
    (async () => { await fetchSettings(() => alive); })();
    return () => { alive = false; };
  }, [fetchSettings]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMsg({ text: t('stg_saving', lang), type: 'info' });
    try {
      await api('/dashboard/admin/settings', {
        method: 'POST',
        body: JSON.stringify(settings)
      }, token);
      setMsg({ text: t('stg_saved', lang), type: 'success' });
      setTimeout(() => setMsg(prev => (prev.type === 'success' ? { text: '', type: '' } : prev)), 4000);
    } catch (err) {
      setMsg({ text: `${t('stg_save_err', lang)}${err.message}`, type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  /* Empty input is kept as an empty string so the field can be cleared while
     typing; the server treats it as "leave unchanged". */
  const handleChange = (key, value) => {
    setSettings(prev => ({ ...prev, [key]: value === '' ? '' : Number(value) }));
  };

  const change = (key) => (value) => handleChange(key, value);

  if (loading) return <Loading message={t('stg_loading', lang)} />;

  if (!settings) {
    return (
      <div className="space-y-5">
        <PageHeader title={t('stg_title', lang)} />
        <Alert type="error" message={t('stg_no_access', lang)} />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title={t('stg_title', lang)}
        subtitle={t('stg_sub', lang)}
        actions={<Button variant="outline" icon={RefreshCw} onClick={fetchSettings}>{t('stg_reload', lang)}</Button>}
      />

      {msg.text && <Alert type={msg.type} message={msg.text} onClose={() => setMsg({ text: '', type: '' })} />}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Section icon={Sliders} title={t('stg_general', lang)}>
            <Slider
              label={t('stg_retries', lang)}
              hint={t('stg_retries_hint', lang)}
              value={settings.ROUTER_API_MAX_RETRIES}
              min={1} max={20}
              unit={t('stg_unit_times', lang)}
              onChange={change('ROUTER_API_MAX_RETRIES')}
            />
            <Slider
              label={t('stg_timeout', lang)}
              hint={t('stg_timeout_hint', lang)}
              value={settings.ROUTER_API_REQUEST_TIMEOUT_SEC}
              min={30} max={600} step={30}
              unit="s"
              onChange={change('ROUTER_API_REQUEST_TIMEOUT_SEC')}
            />
          </Section>

          <Section icon={Cpu} title={t('stg_resilience', lang)}>
            <Slider
              label={t('stg_swap', lang)}
              hint={t('stg_swap_hint', lang)}
              value={settings.POOL_SWAP_FAILURES}
              min={1} max={30}
              unit={t('stg_unit_times', lang)}
              onChange={change('POOL_SWAP_FAILURES')}
            />
            <Slider
              label={t('stg_attempts', lang)}
              hint={t('stg_attempts_hint', lang)}
              value={settings.POOL_MAX_ATTEMPTS}
              min={5} max={120}
              unit={t('stg_unit_times', lang)}
              onChange={change('POOL_MAX_ATTEMPTS')}
            />
          </Section>

          <Section icon={HardDrive} title={t('stg_compaction', lang)} cols="lg:col-span-2">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <NumberField
                label={t('stg_threshold', lang)}
                hint={t('stg_threshold_hint', lang)}
                value={settings.COMPACTION_TOKEN_THRESHOLD}
                unit={t('stg_unit_tokens', lang)}
                onChange={change('COMPACTION_TOKEN_THRESHOLD')}
              />
              <NumberField
                label={t('stg_target', lang)}
                hint={t('stg_target_hint', lang)}
                value={settings.COMPACTION_TARGET_LIMIT}
                unit={t('stg_unit_tokens', lang)}
                onChange={change('COMPACTION_TARGET_LIMIT')}
              />
              <NumberField
                label={t('stg_emergency', lang)}
                hint={t('stg_emergency_hint', lang)}
                value={settings.EMERGENCY_MAX_INPUT_TOKENS}
                unit={t('stg_unit_tokens', lang)}
                onChange={change('EMERGENCY_MAX_INPUT_TOKENS')}
              />
            </div>

            <div className="rounded-lg p-4"
                 style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
              <div className="flex items-start gap-2 mb-3">
                <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" style={{ color: 'var(--c-ink-3)' }} />
                <div className="min-w-0">
                  <h3 className="text-[0.6875rem] font-semibold" style={{ color: 'var(--c-ink-2)' }}>
                    {t('stg_claude', lang)}
                  </h3>
                  <p className="text-[0.6875rem] leading-snug mt-0.5" style={{ color: 'var(--c-ink-3)' }}>
                    {t('stg_claude_hint', lang)}
                  </p>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <NumberField
                  label={t('stg_claude_threshold', lang)}
                  value={settings.CLAUDE_CODE_COMPACTION_THRESHOLD}
                  unit={t('stg_unit_tokens', lang)}
                  onChange={change('CLAUDE_CODE_COMPACTION_THRESHOLD')}
                />
                <NumberField
                  label={t('stg_claude_target', lang)}
                  value={settings.CLAUDE_CODE_COMPACTION_TARGET_LIMIT}
                  unit={t('stg_unit_tokens', lang)}
                  onChange={change('CLAUDE_CODE_COMPACTION_TARGET_LIMIT')}
                />
                <NumberField
                  label={t('stg_claude_emergency', lang)}
                  value={settings.CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS}
                  unit={t('stg_unit_tokens', lang)}
                  onChange={change('CLAUDE_CODE_EMERGENCY_MAX_INPUT_TOKENS')}
                />
              </div>
            </div>
          </Section>

          <Section icon={KeyRound} title={t('stg_client', lang)} cols="lg:col-span-2">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <Slider
                label={t('stg_rpm', lang)}
                hint={t('stg_rpm_hint', lang)}
                value={settings.CLIENT_DEFAULT_RPM}
                min={1} max={60}
                unit="RPM"
                onChange={change('CLIENT_DEFAULT_RPM')}
              />
              <Slider
                label={t('stg_burst', lang)}
                hint={t('stg_burst_hint', lang)}
                value={settings.CLIENT_BURST_RPM}
                min={2} max={120}
                unit="RPM"
                onChange={change('CLIENT_BURST_RPM')}
              />
            </div>
          </Section>
        </div>

        {/* Save bar stays in reach at the bottom of a long form. */}
        <div className="sticky bottom-0 z-10 flex items-center justify-end gap-2 py-3"
             style={{
               borderTop: '1px solid var(--c-line)',
               background: 'color-mix(in srgb, var(--c-canvas) 88%, transparent)',
               backdropFilter: 'blur(8px)',
             }}>
          <Button type="button" variant="ghost" onClick={fetchSettings} disabled={saving}>
            {t('stg_discard', lang)}
          </Button>
          <Button type="submit" icon={Save} loading={saving}>
            {t('stg_save', lang)}
          </Button>
        </div>
      </form>
    </div>
  );
}