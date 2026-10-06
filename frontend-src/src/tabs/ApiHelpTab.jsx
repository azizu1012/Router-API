import React, { useState, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { api } from '../utils/api';
import { BookOpen, ChevronDown, Copy, Check, AlertTriangle } from 'lucide-react';
import Loading from '../components/Loading';

/**
 * API reference, grouped by the dialect each endpoint speaks.
 *
 * The catalog comes from /api/help on the server rather than being written here,
 * for one reason: a curl example copied out of a JS bundle silently rots when a
 * route is renamed. On the server it sits next to a test that compares every
 * path against the app's own route table, so a rename fails the build instead of
 * shipping a 404 to whoever copies the example.
 */

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button
      onClick={copy}
      className="btn btn-xs btn-ghost gap-1 font-bold shrink-0 text-base-content/60 hover:text-primary"
      title="Copy"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
      {copied ? 'Đã copy' : 'Copy'}
    </button>
  );
}

function Method({ method }) {
  const colour = method === 'GET'
    ? 'bg-emerald-500/15 text-emerald-600 border-emerald-500/30'
    : 'bg-primary/15 text-primary border-primary/30';
  return (
    <span className={`badge badge-xs font-extrabold border ${colour}`}>{method}</span>
  );
}

function CodeBlock({ label, value }) {
  if (!value) return null;
  return (
    <div className="mt-2">
      {label && (
        <div className="text-[10px] font-bold uppercase tracking-wider text-base-content/45 mb-1">
          {label}
        </div>
      )}
      <div className="relative">
        <pre className="bg-base-300/60 border border-base-content/10 rounded-lg p-3 pr-16 text-[11px] leading-relaxed overflow-x-auto font-mono text-base-content/85">
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
  return (
    <div className="border-t border-base-content/5 py-3 first:border-t-0">
      <div className="flex items-center gap-2 flex-wrap">
        <Method method={ep.method} />
        <code className="text-xs font-bold text-base-content/90">{ep.path}</code>
        {(ep.aliases || []).map(a => (
          <span key={a} className="text-[10px] font-mono text-base-content/40 bg-base-200 px-1.5 py-0.5 rounded">
            {a}
          </span>
        ))}
      </div>
      {ep.note && (
        <p className="text-[11px] text-base-content/60 mt-1.5 leading-relaxed">
          {ep.note}
        </p>
      )}
      <CodeBlock value={ep.curl} />
      <CodeBlock label="streaming" value={ep.curl_stream} />
      <CodeBlock label="có search" value={ep.curl_search} />
    </div>
  );
}

function Group({ group, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const count = group.endpoints.length;

  return (
    <div className="card glass-card rounded-2xl overflow-hidden text-left border border-base-content/10">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-3 p-4 hover:bg-base-200/40 transition-colors"
      >
        <div className="w-8 h-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0">
          <BookOpen className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-extrabold text-sm text-base-content/90">{group.group}</h3>
          <p className="text-[11px] text-base-content/55 mt-0.5">{group.summary}</p>
        </div>
        <span className="badge badge-xs badge-ghost font-bold shrink-0">{count}</span>
        <ChevronDown
          className={`w-4 h-4 text-base-content/40 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <div className="px-4 pb-4 pt-0">
          {group.endpoints.map(ep => <Endpoint key={ep.path} ep={ep} />)}
        </div>
      )}
    </div>
  );
}

export default function ApiHelpTab() {
  const { token } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    api('/api/help', {}, token)
      .then(res => { if (alive) setData(res); })
      .catch(e => { if (alive) setError(e.message || 'Không tải được'); });
    return () => { alive = false; };
  }, [token]);

  if (error) {
    return (
      <div className="card glass-card rounded-2xl p-6 border border-error/25 text-left">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-error shrink-0 mt-0.5" />
          <div>
            <h3 className="font-extrabold text-sm">Không tải được danh mục API</h3>
            <p className="text-xs text-base-content/60 mt-1">{error}</p>
          </div>
        </div>
      </div>
    );
  }

  if (!data) return <Loading message="Đang tải tài liệu API..." />;

  return (
    <div className="space-y-5">
      <div className="text-left">
        <h1 className="text-2xl font-black tracking-tight">API Reference</h1>
        <p className="text-xs text-base-content/60 mt-1">
          Mỗi endpoint theo đúng dialect nó nói. Copy curl, thay token, chạy.
        </p>
      </div>

      <div className="space-y-3">
        {(data.groups || []).map((g, i) => (
          <Group key={g.id} group={g} defaultOpen={i === 0} />
        ))}
      </div>

      <div className="card glass-card rounded-2xl p-5 text-left border border-base-content/10">
        <h3 className="font-extrabold text-sm mb-3">Lưu ý</h3>
        <div className="space-y-3">
          {(data.notes || []).map(n => (
            <div key={n.title} className="border-l-2 border-primary/30 pl-3">
              <h4 className="text-xs font-bold text-base-content/85">{n.title}</h4>
              <p className="text-[11px] text-base-content/60 leading-relaxed mt-0.5">{n.body}</p>
            </div>
          ))}
        </div>
      </div>

      <p className="text-[10px] text-base-content/40 text-left">
        Muốn xem toàn bộ 75 route của FastAPI:{' '}
        <a href="/docs" target="_blank" rel="noreferrer" className="link link-primary">
          /docs
        </a>
        {' · '}
        <a href="/openapi.json" target="_blank" rel="noreferrer" className="link link-primary">
          /openapi.json
        </a>
      </p>
    </div>
  );
}
