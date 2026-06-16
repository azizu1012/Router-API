import { AlertCircle, CheckCircle2, Info, AlertTriangle, X } from 'lucide-react';

const types = {
  success: { icon: CheckCircle2, classes: 'bg-success/10 text-success border-success/20' },
  error: { icon: AlertCircle, classes: 'bg-error/10 text-error border-error/20' },
  warning: { icon: AlertTriangle, classes: 'bg-warning/10 text-warning border-warning/20' },
  info: { icon: Info, classes: 'bg-info/10 text-info border-info/20' },
};

export default function Alert({ type = 'info', message, onClose, className = '' }) {
  const cfg = types[type] || types.info;
  const Icon = cfg.icon;

  if (!message) return null;

  return (
    <div className={`flex items-start gap-2.5 p-3 rounded-xl border text-xs font-semibold ${cfg.classes} ${className}`}>
      <Icon className="w-4 h-4 mt-0.5 shrink-0" />
      <span className="flex-1">{message}</span>
      {onClose && (
        <button onClick={onClose} className="shrink-0 opacity-50 hover:opacity-100 transition-opacity">
          <X className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}
