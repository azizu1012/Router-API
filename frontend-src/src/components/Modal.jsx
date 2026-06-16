import { useEffect } from 'react';
import { X } from 'lucide-react';

const sizes = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  full: 'max-w-4xl',
};

export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  footer,
  size = 'md',
  className = '',
}) {
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in-up">
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className={`relative w-full ${sizes[size] || sizes.md} bg-base-100/90 backdrop-blur-xl border border-base-content/10 rounded-2xl shadow-2xl ${className}`}>
        {title && (
          <div className="flex items-center justify-between p-4 border-b border-base-content/5">
            <h3 className="text-sm font-extrabold text-base-content/90">{title}</h3>
            <button onClick={onClose} className="btn btn-ghost btn-xs btn-square text-base-content/40 hover:text-base-content/80">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
        <div className="p-4 max-h-[75vh] overflow-y-auto">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-3 p-4 border-t border-base-content/5">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
