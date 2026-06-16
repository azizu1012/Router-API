const variants = {
  glass: 'card glass-card border border-base-content/5 shadow-md',
  elevated: 'bg-base-100/70 border border-base-content/10 shadow-xl shadow-base-300/10 backdrop-blur-md',
  flat: 'bg-base-200/30 border border-base-content/5',
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
  return (
    <div
      className={`rounded-2xl text-left transition-all duration-300 ${variants[variant] || variants.glass} ${paddings[padding] || paddings.md} ${hoverable ? 'hover:scale-[1.01] hover:border-primary/20 cursor-pointer active:scale-[0.99]' : ''} ${className}`}
      {...props}
    >
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
