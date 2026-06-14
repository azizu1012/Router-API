import { useState, useEffect } from 'react';
import { Terminal, X, Download, Pause, Play, Trash2 } from 'lucide-react';
import { useApp } from '../context/AppContext';
import LogTerminal from './LogTerminal';

const LOG_FILES = [
  { id: 'proxy', label: 'Proxy' },
  { id: 'system', label: 'System' },
  { id: 'api', label: 'API Calls' },
  { id: 'keys', label: 'Keys' },
  { id: 'web', label: 'Web' },
];

export default function LogHistoryModal({ endpoint: initialEndpoint, onClose }) {
  const { token, lang } = useApp();
  const [activeFile, setActiveFile] = useState('proxy');
  const [paused, setPaused] = useState(false);
  const [activeEndpoint, setActiveEndpoint] = useState(initialEndpoint || '');
  const [endpoints, setEndpoints] = useState([]);

  useEffect(() => {
    fetch('/dashboard/endpoints', {
      headers: { Authorization: `Bearer ${token}` }
    })
      .then(r => r.json())
      .then(data => setEndpoints(data.endpoints || []))
      .catch(() => {});
  }, [token]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-2 sm:p-4 animate-fade-in-up">
      <div className="card glass-card w-full max-w-7xl max-h-[95vh] p-3 sm:p-4 rounded-2xl sm:rounded-3xl border border-green-500/15 shadow-2xl flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between mb-3 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <Terminal className="w-4 h-4 text-green-400 shrink-0" />
            <h2 className="font-extrabold text-sm truncate">
              Log Stream{initialEndpoint ? `: ${initialEndpoint}` : ''}
            </h2>
            <span className={`w-2 h-2 rounded-full shrink-0 ${paused ? 'bg-yellow-400' : 'bg-green-400 animate-pulse'}`} />
          </div>

          <div className="flex items-center gap-1.5">
            {/* File selector */}
            <select
              value={activeFile}
              onChange={(e) => setActiveFile(e.target.value)}
              className="select select-ghost select-xs text-[10px] font-bold bg-base-200/30 rounded-lg"
            >
              {LOG_FILES.map(f => (
                <option key={f.id} value={f.id}>{f.label}</option>
              ))}
            </select>

            <div className="divider divider-horizontal mx-0.5 h-5" />

            <button
              onClick={() => setPaused(p => !p)}
              className="btn btn-ghost btn-xs btn-square text-base-content/60 hover:text-yellow-400"
              title={paused ? 'Resume' : 'Pause'}
            >
              {paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
            </button>
            <button
              onClick={() => {
                const term = document.querySelector('.xterm')?.querySelector('.xterm-rows');
                if (term) term.innerHTML = '';
              }}
              className="btn btn-ghost btn-xs btn-square text-base-content/60 hover:text-error"
              title="Clear"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => {
                const el = document.querySelector('.xterm-rows');
                if (!el) return;
                const text = Array.from(el.querySelectorAll('.xterm-char-element') || [])
                  .map(c => c.textContent).join('');
                const blob = new Blob([text], { type: 'text/plain' });
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = `log_${initialEndpoint || 'all'}_${activeFile}.txt`;
                a.click();
                URL.revokeObjectURL(a.href);
              }}
              className="btn btn-ghost btn-xs btn-square text-base-content/60 hover:text-primary"
              title="Download"
            >
              <Download className="w-3.5 h-3.5" />
            </button>

            <div className="divider divider-horizontal mx-0.5 h-5" />

            <button onClick={onClose} className="btn btn-ghost btn-xs btn-square text-base-content/60 hover:text-error">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Dynamic endpoint tabs */}
        {endpoints.length > 0 && (
          <div className="flex gap-1.5 mb-2 shrink-0 overflow-x-auto pb-1 [&::-webkit-scrollbar]:h-1">
            <button
              onClick={() => setActiveEndpoint('')}
              className={`btn btn-ghost btn-xs font-bold rounded-full px-3 transition-all whitespace-nowrap ${
                !activeEndpoint
                  ? 'text-primary bg-primary/10 border border-primary/30'
                  : 'text-base-content/50 hover:text-base-content hover:bg-base-200/30 border border-transparent'
              }`}
            >
              All
            </button>
            {endpoints.map(ep => (
              <button
                key={ep.name}
                onClick={() => setActiveEndpoint(ep.name)}
                className={`btn btn-ghost btn-xs font-bold rounded-full px-3 transition-all whitespace-nowrap ${
                  activeEndpoint === ep.name
                    ? 'text-primary bg-primary/10 border border-primary/30'
                    : 'text-base-content/50 hover:text-base-content hover:bg-base-200/30 border border-transparent'
                }`}
              >
                {ep.name}
              </button>
            ))}
          </div>
        )}

        {/* Terminal */}
        <div className="flex-1 min-h-0 rounded-xl overflow-hidden border border-green-500/10">
          <LogTerminal
            endpoint={activeEndpoint}
            token={token}
            logFile={activeFile}
            height="100%"
          />
        </div>

        {/* Status bar */}
        <div className="flex items-center justify-between mt-2 text-[10px] text-base-content/40 shrink-0">
          <span>File: {activeFile}.log</span>
          <span>Endpoint: {activeEndpoint || 'all'}</span>
          <span>WS: {paused ? 'Paused' : 'Live'}</span>
        </div>
      </div>
    </div>
  );
}
