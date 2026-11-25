import { useState, useEffect, useRef } from 'react';
import { Terminal as TerminalIcon, X, Download, Pause, Play, Trash2 } from 'lucide-react';
import { useApp } from '../context/AppContext';
import { t } from '../utils/i18n';
import LogTerminal from './LogTerminal';

const LOG_CHANNELS = [
  { id: 'proxy', adminOnly: false, label: 'proxy' },
  { id: 'api', adminOnly: false, label: 'api' },
  { id: 'system', adminOnly: true, label: 'system' },
  { id: 'keys', adminOnly: true, label: 'keys' },
  { id: 'web', adminOnly: true, label: 'web' },
];

/* The header actions act on the terminal's own buffer through the controller
   LogTerminal hands back — scraping `.xterm-rows` was reading classes xterm
   does not render, so Download produced an empty file and Clear removed
   nothing. */
export default function LogHistoryModal({ endpoint: initialEndpoint, onClose }) {
  const { token, lang, user } = useApp();
  const isAdmin = user?.tier === 'admin';
  const availableChannels = LOG_CHANNELS.filter(c => isAdmin || !c.adminOnly);

  const [pickedFile, setPickedFile] = useState('proxy');
  const [paused, setPaused] = useState(false);
  const [activeEndpoint, setActiveEndpoint] = useState(initialEndpoint || '');
  const [endpoints, setEndpoints] = useState([]);
  const controller = useRef(null);

  // Derived, not corrected in an effect: a non-admin who somehow holds a
  // privileged channel in state still streams proxy. The select can only
  // offer channels the caller may read.
  const permitted = isAdmin || !LOG_CHANNELS.find(c => c.id === pickedFile)?.adminOnly;
  const activeFile = permitted ? pickedFile : 'proxy';

  // Escape closes: this panel covers the page, so a keyboard user must not
  // have to find the × in the corner.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    fetch('/dashboard/endpoints', {
      headers: {
        'X-Dashboard-Token': token,
        'Authorization': `Bearer ${token}`
      }
    })
      .then(r => r.json())
      .then(data => setEndpoints(data.endpoints || []))
      .catch(() => {});
  }, [token]);

  const tabClass = (active) => `text-[0.6875rem] font-medium rounded-md px-2.5 py-1 whitespace-nowrap transition-colors ${
    active ? '' : 'hover:opacity-100'
  }`;
  const tabStyle = (active) => ({
    background: active ? 'var(--c-accent-soft)' : 'transparent',
    border: `1px solid ${active ? 'color-mix(in srgb, var(--c-accent) 35%, transparent)' : 'var(--c-line)'}`,
    color: active ? 'var(--c-accent)' : 'var(--c-ink-3)',
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 animate-fade-in-up"
         style={{ background: 'rgba(0,0,0,0.62)' }}>
      <div className="glass-card w-full max-w-7xl max-h-[92vh] rounded-xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 shrink-0"
             style={{ borderBottom: '1px solid var(--c-line)' }}>
          <div className="flex items-center gap-2 min-w-0">
            <TerminalIcon className="w-4 h-4 shrink-0" style={{ color: 'var(--c-ink-3)' }} />
            <h2 className="text-xs font-semibold truncate" style={{ color: 'var(--c-ink)' }}>
              {t('lg_title', lang)}{initialEndpoint ? ` · ${initialEndpoint}` : ''}
            </h2>
            <span className="inline-flex items-center gap-1.5 text-[0.625rem] font-medium shrink-0"
                  style={{ color: paused ? 'var(--c-warn)' : 'var(--c-good)' }}>
              <span className="w-1.5 h-1.5 rounded-full"
                    style={{ background: 'currentColor' }}
                    aria-hidden="true" />
              {paused ? t('lg_paused', lang) : t('lg_live', lang)}
            </span>
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            <select
              value={activeFile}
              onChange={(e) => setPickedFile(e.target.value)}
              className="select select-xs text-[0.6875rem]"
              aria-label={t('lg_channel', lang)}
            >
              {availableChannels.map(f => (
                <option key={f.id} value={f.id}>{f.id}</option>
              ))}
            </select>

            <button
              onClick={() => setPaused(p => !p)}
              className="btn btn-ghost btn-xs btn-square"
              style={{ color: paused ? 'var(--c-warn)' : 'var(--c-ink-3)' }}
              title={paused ? t('lg_resume', lang) : t('lg_pause', lang)}
              aria-label={paused ? t('lg_resume', lang) : t('lg_pause', lang)}
              aria-pressed={paused}
            >
              {paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
            </button>
            <button
              onClick={() => controller.current?.clear()}
              className="btn btn-ghost btn-xs btn-square"
              style={{ color: 'var(--c-ink-3)' }}
              title={t('lg_clear', lang)}
              aria-label={t('lg_clear', lang)}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => controller.current?.download()}
              className="btn btn-ghost btn-xs btn-square"
              style={{ color: 'var(--c-ink-3)' }}
              title={t('lg_download', lang)}
              aria-label={t('lg_download', lang)}
            >
              <Download className="w-3.5 h-3.5" />
            </button>
            <button onClick={onClose}
                    className="btn btn-ghost btn-xs btn-square"
                    style={{ color: 'var(--c-ink-3)' }}
                    title={t('lg_close', lang)}
                    aria-label={t('lg_close', lang)}>
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Endpoint tabs */}
        {endpoints.length > 0 && (
          <div className="flex gap-1.5 px-3 py-2 shrink-0 overflow-x-auto"
               style={{ borderBottom: '1px solid var(--c-line)' }}>
            <button onClick={() => setActiveEndpoint('')} className={tabClass(!activeEndpoint)}
                    style={tabStyle(!activeEndpoint)}>
              {t('lg_all', lang)}
            </button>
            {endpoints.map(ep => (
              <button
                key={ep.name}
                onClick={() => setActiveEndpoint(ep.name)}
                className={tabClass(activeEndpoint === ep.name)}
                style={tabStyle(activeEndpoint === ep.name)}
              >
                {ep.name}
              </button>
            ))}
          </div>
        )}

        {/* Terminal */}
        <div className="flex-1 min-h-0">
          <LogTerminal
            endpoint={activeEndpoint}
            token={token}
            logFile={activeFile}
            height="100%"
            paused={paused}
            controllerRef={controller}
          />
        </div>

        {/* Status bar */}
        <div className="flex items-center justify-between gap-4 px-3 py-2 text-[0.625rem] font-mono shrink-0"
             style={{ borderTop: '1px solid var(--c-line)', color: 'var(--c-ink-3)' }}>
          <span className="truncate">{t('lg_file', lang)}: {activeFile}.log</span>
          <span className="truncate">{t('lg_endpoint', lang)}: {activeEndpoint || t('lg_all', lang)}</span>
          <span className="truncate shrink-0">{paused ? t('lg_paused', lang) : t('lg_live', lang)}</span>
        </div>
      </div>
    </div>
  );
}