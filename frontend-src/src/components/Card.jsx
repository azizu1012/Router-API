import React, { useRef } from 'react';

const variants = {
  glass: 'card glass-card border border-base-content/8 shadow-lg shadow-black/10 backdrop-blur-xl',
  elevated: 'bg-base-100/80 border border-base-content/10 shadow-xl shadow-base-300/15 backdrop-blur-xl',
  flat: 'bg-base-200/40 border border-base-content/6',
};

const paddings = {
  none: '',
  xs: 'p-3',
  sm: 'p-4',
  md: 'p-5',
  lg: 'p-6',
  xl: 'p-8',
};

export default function Card({
  variant = 'glass',
  padding = 'md',
  hoverable,
  className = '',
  children,
  ...props
}) {
  const cardRef = useRef(null);

  const handleMouseMove = (e) => {
    if (!cardRef.current) return;
    const rect = cardRef.current.getBoundingClientRect();
    cardRef.current.style.setProperty('--card-mouse-x', `${e.clientX - rect.left}px`);
    cardRef.current.style.setProperty('--card-mouse-y', `${e.clientY - rect.top}px`);
  };

  return (
    <div
      ref={cardRef}
      onMouseMove={handleMouseMove}
      className={`relative group/card overflow-hidden rounded-2xl text-left transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)] ${variants[variant] || variants.glass} ${paddings[padding] || paddings.md} ${hoverable ? 'hover:-translate-y-0.5 hover:shadow-xl hover:border-primary/30 cursor-pointer active:translate-y-0 active:scale-[0.99]' : ''} ${className}`}
      {...props}
    >
      {/* Dynamic Cursor Spotlight Overlay */}
      <div
        className="pointer-events-none absolute -inset-px rounded-2xl opacity-0 group-hover/card:opacity-100 transition-opacity duration-400 -z-10"
        style={{
          background: 'radial-gradient(350px circle at var(--card-mouse-x, -200px) var(--card-mouse-y, -200px), rgba(99, 102, 241, 0.08), transparent 80%)',
        }}
      />
      {children}
    </div>
  );
}

Card.Header = function CardHeader({ title, subtitle, action, className = '' }) {
  return (
    <div className={`flex items-start justify-between gap-4 mb-4 ${className}`}>
      <div className="space-y-1 min-w-0 flex-1">
        {title && <h3 className="font-extrabold text-sm text-base-content/90 truncate">{title}</h3>}
        {subtitle && <p className="text-[10px] text-base-content/50">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
};

Card.Body = function CardBody({ children, className = '' }) {
  return <div className={`space-y-3 ${className}`}>{children}</div>;
};

Card.Section = function CardSection({ children, className = '' }) {
  return (
    <div className={`p-3 rounded-xl bg-base-200/30 border border-base-content/5 ${className}`}>
      {children}
    </div>
  );
};

Card.Row = function CardRow({ label, value, color, className = '' }) {
  return (
    <div className={`flex justify-between items-center py-1.5 ${className}`}>
      <span className="text-xs text-base-content/50">{label}</span>
      <span className={`text-xs font-bold ${color || 'text-base-content/90'}`}>{value}</span>
    </div>
  );
};
