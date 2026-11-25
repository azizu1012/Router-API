import { useState, useEffect, useRef, useCallback, useId } from 'react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import { api } from '../utils/api';
import { copyText } from '../utils/clipboard';
import { BookOpen, ChevronDown, Copy, Check, AlertTriangle, Link2 } from 'lucide-react';
import Loading from '../components/Loading';
import PageHeader from '../components/PageHeader';
import Badge from '../components/Badge';
import Card from '../components/Card';

/**
 * API reference, grouped by the dialect each endpoint speaks.
 *
 * The catalog comes from /api/help on the server rather than being written here,
 * for one reason: a curl example copied out of a JS bundle silently rots when a
 * route is renamed. On the server it sits next to a test that compares every
 * path against the app's own route table, so a rename fails the build instead of
 * shipping a 404 to whoever copies the example.
 *
 * The origin inside the examples is the one this page was served from — the
 * server fills it in per request. Nothing here hardcodes a host, so the same
 * build works on localhost, on a LAN address, and behind HTTPS.
 */

function CopyButton({ text }) {
  const { lang } = useApp();
  const [state, setState] = useState('idle'); // idle | copied | failed
  const timer = useRef(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = async () => {
    const ok = await copyText(text);
    setState(ok ? 'copied' : 'failed');
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), ok ? 1600 : 2600);
  };

  const label = state === 'copied' ? t('api_copied', lang)
    : state === 'failed' ? t('api_copy_failed', lang)
      : t('api_copy', lang);

  return (
    <button
      type="button"
      onClick={copy}
      className="btn btn-xs btn-ghost gap-1 text-[0.6875rem] shrink-0"
      style={{ color: state === 'copied' ? 'var(--c-good)'
        : state === 'failed' ? 'var(--c-critical)' : 'var(--c-ink-3)' }}
      title={label}
      aria-label={label}
    >
      {state === 'copied' ? <Check className="w-3.5 h-3.5" />
        : state === 'failed' ? <AlertTriangle className="w-3.5 h-3.5" />
          : <Copy className="w-3.5 h-3.5" />}
      {label}
    </button>
  );
}

/* Method is an identifier, not a status: GET and the write verbs get equal
   weight in different tints, never a "safe vs dangerous" colour scale. */
function Method({ method }) {
  const isRead = method === 'GET';
  return (
    <span
      className="inline-flex items-center font-mono font-semibold tracking-wide text-[0.625rem] px-1.5 py-0.5 rounded"
      style={{
        background: isRead ? 'var(--c-surface-3)' : 'var(--c-accent-soft)',
        color: isRead ? 'var(--c-ink-2)' : 'var(--c-accent)',
      }}
    >
      {method}
    </span>
  );
}

function CodeBlock({ label, value }) {
  const id = useId();
  if (!value) return null;
  return (
    <div className="mt-2">
      {label && (
        <div className="label-micro mb-1" id={id}>{label}</div>
      )}
      <div className="relative group">
        <pre
          className="rounded-lg p-3 pr-20 text-[0.6875rem] leading-relaxed overflow-x-auto font-mono"
          style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)', color: 'var(--c-ink-2)' }}
          aria-labelledby={label ? id : undefined}
        >
          {value}
        </pre>
        <div className="absolute top-1.5 right-1.5">
          <CopyButton text={value} />
        </div>
      </div>
    </div>
  );
}

function Endpoint({ ep }) {
  const { lang } = useApp();
  return (
    <div className="py-3" style={{ borderTop: '1px solid var(--c-line)' }}>
      <div className="flex items-center gap-2 flex-wrap">
        <Method method={ep.method} />
        <code className="text-xs font-medium" style={{ color: 'var(--c-ink)' }}>{ep.path}</code>
        {(ep.aliases || []).map(a => (
          <span key={a} className="text-[0.625rem] font-mono px-1.5 py-0.5 rounded"
                style={{ background: 'var(--c-surface-3)', color: 'var(--c-ink-3)' }}>
            {a}
          </span>
        ))}
      </div>
      {ep.note && (
        <p className="text-[0.6875rem] mt-1.5 leading-relaxed" style={{ color: 'var(--c-ink-3)' }}>
          {ep.note}
        </p>
      )}
      <CodeBlock value={ep.curl} />
      <CodeBlock label={t('api_streaming', lang)} value={ep.curl_stream} />
      <CodeBlock label={t('api_with_search', lang)} value={ep.curl_search} />
    </div>
  );
}

function Group({ group, groupKey, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const count = group.endpoints.length;
  const panelId = `api-group-${groupKey}`;

  return (
    <div className="glass-card rounded-xl overflow-hidden text-left">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-controls={panelId}
        className="w-full flex items-center gap-3 p-4 text-left transition-colors hover:bg-[var(--c-surface-2)]"
      >
        <span className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
              style={{ background: 'var(--c-accent-soft)', color: 'var(--c-accent)' }}>
          <BookOpen className="w-3.5 h-3.5" />
        </span>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold truncate" style={{ color: 'var(--c-ink)' }}>{group.group}</h3>
          <p className="text-[0.6875rem] mt-0.5 truncate" style={{ color: 'var(--c-ink-3)' }}>{group.summary}</p>
        </div>
        <Badge>{count}</Badge>
        <ChevronDown
          className={`w-4 h-4 shrink-0 transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
          style={{ color: 'var(--c-ink-3)' }}
        />
      </button>
      {open && (
        <div className="px-4 pb-4" id={panelId}>
          {group.endpoints.map((ep, i) => (
            <div key={ep.path} style={i === 0 ? { borderTop: 'none' } : undefined}>
              <Endpoint ep={ep} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ApiHelpTab() {
  const { token, lang } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  /* lang is a fetch dependency on purpose: the prose and the sample prompts in
     the examples come from the server, so switching language refetches instead
     of leaving half the page in the old one. */
  const load = useCallback(() => {
    let alive = true;
    api(`/api/help?lang=${encodeURIComponent(lang)}`, {}, token)
      .then(res => { if (alive) { setData(res); setError(''); } })
      .catch(e => { if (alive) setError(e.message || t('load_error', lang)); });
    return () => { alive = false; };
  }, [token, lang]);

  useEffect(load, [load]);

  if (error) {
    return (
      <Card padding="lg" className="text-left">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" style={{ color: 'var(--c-critical)' }} />
          <div>
            <h3 className="text-sm font-semibold" style={{ color: 'var(--c-ink)' }}>{t('api_load_error', lang)}</h3>
            <p className="text-xs mt-1" style={{ color: 'var(--c-ink-3)' }}>{error}</p>
          </div>
        </div>
      </Card>
    );
  }

  if (!data) return <Loading message={t('api_loading', lang)} />;

  const baseUrl = data.base_url;

  return (
    <div className="space-y-5">
      <PageHeader title={t('api_title', lang)} subtitle={t('api_sub', lang)} />

      {/* The examples are absolute URLs, so say which host they were written
          against. Explains why a deployed instance shows its own domain. */}
      {baseUrl && (
        <div className="flex items-start gap-2.5 rounded-lg px-3 py-2.5 text-left"
             style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
          <Link2 className="w-3.5 h-3.5 shrink-0 mt-0.5" style={{ color: 'var(--c-accent)' }} />
          <p className="text-[0.6875rem] leading-relaxed min-w-0" style={{ color: 'var(--c-ink-3)' }}>
            {t('api_base_url_hint', lang)}{' '}
            <code className="font-mono font-semibold break-all" style={{ color: 'var(--c-ink)' }}>{baseUrl}</code>
          </p>
        </div>
      )}

      <div className="space-y-3">
        {(data.groups || []).map((g, i) => (
          <Group key={g.id} group={g} groupKey={g.id} defaultOpen={i === 0} />
        ))}
      </div>

      {(data.notes || []).length > 0 && (
        <Card padding="lg" className="text-left">
          <h3 className="text-xs font-semibold mb-3" style={{ color: 'var(--c-ink-2)' }}>
            {t('api_notes', lang)}
          </h3>
          <div className="space-y-3">
            {data.notes.map(n => (
              <div key={n.title} className="pl-3" style={{ borderLeft: '2px solid var(--c-line-strong)' }}>
                <h4 className="text-xs font-semibold" style={{ color: 'var(--c-ink)' }}>{n.title}</h4>
                <p className="text-[0.6875rem] leading-relaxed mt-0.5" style={{ color: 'var(--c-ink-3)' }}>{n.body}</p>
              </div>
            ))}
          </div>
        </Card>
      )}

      <p className="text-[0.6875rem] text-left" style={{ color: 'var(--c-ink-3)' }}>
        {t('api_footer_pre', lang)}{' '}
        <a href="/docs" target="_blank" rel="noreferrer" className="link" style={{ color: 'var(--c-accent)' }}>
          /docs
        </a>
        {' · '}
        <a href="/openapi.json" target="_blank" rel="noreferrer" className="link" style={{ color: 'var(--c-accent)' }}>
          /openapi.json
        </a>
      </p>
    </div>
  );
}