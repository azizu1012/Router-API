import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

const sizes = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-2xl',
  full: 'max-w-4xl',
};

/* Escape closes, the backdrop closes, focus lands inside on open and returns
   to whatever opened it on close, and the page behind does not scroll. */
export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  footer,
  size = 'md',
  className = '',
}) {
  const panelRef = useRef(null);
  const restoreRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return;
    restoreRef.current = document.activeElement;

    const onKey = e => {
      if (e.key === 'Escape') { onClose?.(); return; }
      if (e.key !== 'Tab' || !panelRef.current) return;
      const nodes = panelRef.current.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      );
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };

    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Move focus into the panel after it paints.
    const t = setTimeout(() => panelRef.current?.querySelector('button, [href], input, select, textarea')?.focus(), 0);

    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
      clearTimeout(t);
      if (restoreRef.current && typeof restoreRef.current.focus === 'function') restoreRef.current.focus();
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in-up">
      <div className="fixed inset-0" style={{ background: 'rgba(0,0,0,0.55)' }} onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        className={`relative w-full ${sizes[size] || sizes.md} rounded-xl animate-fade-in-up ${className}`}
        style={{ background: 'var(--c-surface)', border: '1px solid var(--c-line-strong)', boxShadow: 'var(--c-shadow)' }}
      >
        {title && (
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--c-line)' }}>
            <h3 className="text-sm font-semibold" style={{ color: 'var(--c-ink)' }}>{title}</h3>
            <button onClick={onClose} className="btn btn-ghost btn-xs btn-square"
                    style={{ color: 'var(--c-ink-3)' }} aria-label="Close">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
        <div className="p-4 max-h-[75vh] overflow-y-auto">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 px-4 py-3" style={{ borderTop: '1px solid var(--c-line)' }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}