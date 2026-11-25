import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { fmt } from '../utils/format';
import { BarChart3, Users, Award, Zap } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';

/* A leaderboard measures ONE quantity (tokens) across many accounts. Painting
   each account a different hue would encode rank as colour and imply a
   categorical difference that is not there — so every bar wears the same
   accent, and the length carries the comparison. Rank is a numeral in a quiet
   chip, not a coloured badge. */
export default function TokenAnalysisTab() {
  const { tabData, lang } = useApp();
  const ovData = tabData.ov;

  if (!ovData) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[24rem] gap-3">
        <span className="loading loading-spinner loading-md" style={{ color: 'var(--c-accent)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--c-ink-3)' }}>{t('loading', lang)}</span>
      </div>
    );
  }

  const topKeys = ovData.top_keys || [];

  if (topKeys.length === 0) {
    return (
      <Card padding="xl" className="text-center">
        <BarChart3 className="w-8 h-8 mx-auto mb-3" style={{ color: 'var(--c-ink-3)' }} />
        <p className="text-sm font-medium" style={{ color: 'var(--c-ink-2)' }}>{t('no_data', lang)}</p>
      </Card>
    );
  }

  const maxTokens = Math.max(...topKeys.map(k => k.t || 1));
  const totalTokens = topKeys.reduce((a, b) => a + (b.t || 0), 0);
  const totalReq = topKeys.reduce((a, b) => a + (b.req || 0), 0);

  const kpis = [
    { icon: Award, label: t('ta_top_consumer', lang), value: topKeys[0]?.account_name || '—', sub: `${fmt(topKeys[0]?.t || 0)} tokens`, color: 'var(--c-accent)' },
    { icon: Users, label: t('ta_active_accounts', lang), value: topKeys.length, sub: t('ta_last_30d', lang), color: 'var(--s-1)' },
    { icon: Zap, label: t('ta_total_usage', lang), value: fmt(totalTokens), sub: `${totalReq.toLocaleString()} ${t('requests_count', lang)}`, color: 'var(--s-3)' },
  ];

  return (
    <div className="space-y-5">
      <PageHeader title={t('nav_us', lang)} subtitle={t('ta_sub', lang)} />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 animate-fade-in-up">
        {kpis.map(({ icon: Icon, label, value, sub, color }) => (
          <Card key={label} padding="md" className="glass-card-interactive">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
                    style={{ background: `color-mix(in srgb, ${color} 12%, transparent)`, color }}>
                <Icon className="w-3.5 h-3.5" />
              </span>
              <span className="label-micro">{label}</span>
            </div>
            <div className="text-[1.5rem] leading-none font-semibold truncate"
                 style={{ color: 'var(--c-ink)', letterSpacing: '-0.02em' }}>
              {value}
            </div>
            <p className="text-[0.6875rem] mt-2 tabular-nums" style={{ color: 'var(--c-ink-3)' }}>{sub}</p>
          </Card>
        ))}
      </div>

      <Card padding="none" className="animate-fade-in-up cascade-1">
        <div className="flex items-center gap-2 px-4 pt-4 pb-3">
          <BarChart3 className="w-3.5 h-3.5" style={{ color: 'var(--c-ink-3)' }} />
          <h2 className="text-xs font-semibold" style={{ color: 'var(--c-ink-2)' }}>{t('ta_leaderboard', lang)}</h2>
        </div>

        <div className="px-4 pb-4 space-y-3">
          {topKeys.map((k, i) => {
            const pct = Math.round(((k.t || 0) / maxTokens) * 100) || 0;

            return (
              <div key={k.account_name || i} className="space-y-1.5">
                <div className="flex items-baseline justify-between items-center flex-wrap gap-2">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="w-5 h-5 rounded flex items-center justify-center text-[0.625rem] font-semibold tabular-nums shrink-0"
                          style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink-3)' }}>
                      {i + 1}
                    </span>
                    <span className="text-xs font-medium truncate" style={{ color: 'var(--c-ink)' }}>
                      {k.account_name}
                    </span>
                    <code className="text-[0.625rem] font-mono px-1.5 py-0.5 rounded shrink-0"
                          style={{ background: 'var(--c-surface-2)', color: 'var(--c-ink-3)' }}>
                      {k.key_masked}
                    </code>
                  </div>
                  <div className="text-[0.6875rem] tabular-nums shrink-0" style={{ color: 'var(--c-ink-3)' }}>
                    <span style={{ color: 'var(--c-ink)', fontWeight: 600 }}>{fmt(k.t || 0)}</span> tokens
                    <span className="mx-1.5 opacity-40">·</span>
                    <span style={{ color: 'var(--c-ink-2)' }}>{k.req?.toLocaleString()}</span> {t('requests_count', lang)}
                  </div>
                </div>

                <div className="meter" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
                     aria-label={`${k.account_name} share ${pct}%`}>
                  <span style={{ width: `${pct}%`, background: 'var(--c-accent)' }} />
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}