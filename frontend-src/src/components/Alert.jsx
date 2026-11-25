import { AlertCircle, CheckCircle2, Info, AlertTriangle, X } from 'lucide-react';

const types = {
  success: { icon: CheckCircle2, color: 'var(--c-good)' },
  error: { icon: AlertCircle, color: 'var(--c-critical)' },
  warning: { icon: AlertTriangle, color: 'var(--c-warn)' },
  info: { icon: Info, color: 'var(--c-accent)' },
};

export default function Alert({ type = 'info', message, onClose, className = '' }) {
  const cfg = types[type] || types.info;
  const Icon = cfg.icon;

  if (!message) return null;

  return (
    <div
      className={`flex items-start gap-2.5 p-3 rounded-lg text-xs ${className}`}
      style={{
        background: `color-mix(in srgb, ${cfg.color} 10%, transparent)`,
        border: `1px solid color-mix(in srgb, ${cfg.color} 26%, transparent)`,
        color: 'var(--c-ink)',
      }}
      role={type === 'error' ? 'alert' : 'status'}
    >
      <Icon className="w-4 h-4 mt-px shrink-0" style={{ color: cfg.color }} />
      <span className="flex-1 leading-relaxed">{message}</span>
      {onClose && (
        <button onClick={onClose} className="shrink-0 transition-opacity hover:opacity-100 opacity-60"
                style={{ color: 'var(--c-ink-2)' }} aria-label="Dismiss">
          <X className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}