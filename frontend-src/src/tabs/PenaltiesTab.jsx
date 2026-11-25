import { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { relt } from '../utils/format';
import { AlertOctagon, CheckCircle2 } from 'lucide-react';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';

/* A penalty is a temporary demotion, not an outage — so the tone stays
   informational: the count is a number, the only red is the reduction itself
   (which is the quantity being shown), and every state carries a word. */
export default function PenaltiesTab() {
  const { tabData, lang } = useApp();
  const penalties = tabData.pe || [];

  const [timeTicker, setTimeTicker] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const interval = setInterval(() => setTimeTicker(Date.now() / 1000), 1000);
    return () => clearInterval(interval);
  }, []);

  if (!tabData.pe) return <Loading message={t('loading', lang)} />;

  const active = penalties.filter((p) => p.expires > timeTicker).length;

  return (
    <div className="space-y-5">
      <PageHeader title={t('nav_pe', lang)} subtitle={t('pe_sub', lang)} />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 animate-fade-in-up">
        <Card padding="md">
          <div className="flex items-center gap-1.5 mb-2">
            <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: 'var(--c-warn)' }} />
            <span className="label-micro">{t('pe_active_total', lang)}</span>
          </div>
          <div className="text-[1.5rem] leading-none font-semibold tabular-nums"
               style={{ color: active > 0 ? 'var(--c-warn)' : 'var(--c-good)' }}>
            {active}
          </div>
        </Card>
      </div>

      {penalties.length > 0 ? (
        <div className="glass-card rounded-xl overflow-hidden animate-fade-in-up cascade-1">
          <div className="flex items-center gap-2 px-4 py-2.5"
               style={{ borderBottom: '1px solid var(--c-line)' }}>
            <AlertOctagon className="w-3.5 h-3.5" style={{ color: 'var(--c-ink-3)' }} />
            <h2 className="text-xs font-semibold" style={{ color: 'var(--c-ink-2)' }}>
              {t('pe_detail_title', lang)}
            </h2>
          </div>
          <div className="overflow-x-auto w-full">
            <table className="table w-full">
              <thead>
                <tr>
                  <th className="min-w-[12rem]">{t('th_key_code', lang)}</th>
                  <th className="min-w-[9rem]">{t('th_model', lang)}</th>
                  <th className="min-w-[12rem]">{t('lbl_error_reason', lang)}</th>
                  <th className="min-w-[6rem] text-right">{t('th_score_reduction', lang)}</th>
                  <th className="min-w-[8rem]">{t('lbl_expires_after', lang)}</th>
                </tr>
              </thead>
              <tbody>
                {penalties.map((p) => {
                  const isExpired = p.expires <= timeTicker;

                  return (
                    <tr key={`${p.key}-${p.model_id || 'global'}-${p.expires}`}>
                      <td>
                        <code className="font-mono text-[0.6875rem]" style={{ color: 'var(--c-accent)' }}>
                          {p.key}
                        </code>
                      </td>
                      <td>
                        <code className="font-mono text-[0.6875rem] px-1.5 py-0.5 rounded"
                              style={{ background: 'var(--c-surface-2)', color: 'var(--c-ink-2)' }}>
                          {p.model_id || t('lbl_global', lang)}
                        </code>
                      </td>
                      <td className="max-w-0">
                        <span className="block truncate text-xs" title={p.reason || '—'}
                              style={{ color: p.reason ? 'var(--c-ink-2)' : 'var(--c-ink-3)' }}>
                          {p.reason || '—'}
                        </span>
                      </td>
                      <td className="text-right">
                        <span className="tabular-nums font-medium text-xs"
                              style={{ color: 'var(--c-critical)' }}>
                          −{p.score_reduction || 0}
                        </span>
                      </td>
                      <td>
                        {isExpired ? (
                          <span className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--c-ink-3)' }}>
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            {t('lbl_expired', lang)}
                          </span>
                        ) : (
                          <span className="tabular-nums text-xs" style={{ color: 'var(--c-warn)' }}>
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
        <Card padding="xl" className="text-center animate-fade-in-up">
          <span className="inline-flex w-11 h-11 rounded-full items-center justify-center mb-3"
                style={{ background: 'color-mix(in srgb, var(--c-good) 12%, transparent)' }}>
            <CheckCircle2 className="w-5 h-5" style={{ color: 'var(--c-good)' }} />
          </span>
          <h3 className="text-sm font-semibold" style={{ color: 'var(--c-ink)' }}>
            {t('no_penalties', lang)}
          </h3>
          <p className="text-[0.6875rem] mt-1.5" style={{ color: 'var(--c-ink-3)' }}>
            {t('pe_clear_sub', lang)}
          </p>
        </Card>
      )}
    </div>
  );
}