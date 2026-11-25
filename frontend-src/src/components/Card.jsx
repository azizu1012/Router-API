/* One surface treatment for the whole cockpit: hairline border, 0.75 rem
   radius, a shadow that reads as depth. Variants change elevation only. */

const variants = {
  glass: 'glass-card',
  elevated: 'glass-card',
  flat: '',
};

const paddings = {
  none: '',
  xs: 'p-3',
  sm: 'p-4',
  md: 'p-4',
  lg: 'p-5',
  xl: 'p-6',
};

export default function Card({
  variant = 'glass',
  padding = 'md',
  hoverable,
  className = '',
  children,
  style,
  ...props
}) {
  const flat = variant === 'flat';

  return (
    <div
      className={`relative rounded-xl text-left ${flat ? '' : variants[variant] || variants.glass} ${paddings[padding] ?? paddings.md} ${hoverable ? 'glass-card-interactive' : ''} ${className}`}
      style={flat
        ? { background: 'var(--c-surface-2)', border: '1px solid var(--c-line)', ...style }
        : style}
      {...props}
    >
      {children}
    </div>
  );
}

Card.Header = function CardHeader({ title, subtitle, action, className = '' }) {
  return (
    <div className={`flex items-start justify-between gap-3 mb-3 ${className}`}>
      <div className="space-y-0.5 min-w-0 flex-1">
        {title && (
          <h3 className="text-xs font-semibold truncate" style={{ color: 'var(--c-ink)' }}>{title}</h3>
        )}
        {subtitle && (
          <p className="text-[0.6875rem] leading-relaxed" style={{ color: 'var(--c-ink-3)' }}>{subtitle}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
};

Card.Body = function CardBody({ children, className = '' }) {
  return <div className={`space-y-2.5 ${className}`}>{children}</div>;
};

Card.Section = function CardSection({ children, className = '' }) {
  return (
    <div className={`p-3 rounded-lg ${className}`}
         style={{ background: 'var(--c-surface-2)', border: '1px solid var(--c-line)' }}>
      {children}
    </div>
  );
};

Card.Row = function CardRow({ label, value, color = '', className = '' }) {
  // `color` may be a utility-class string ("text-warning font-mono") or a raw
  // CSS colour; only pass it as a style value when it clearly is one.
  const isClass = /(^|\s)(text|font|bg)-/.test(color);
  return (
    <div className={`flex justify-between items-baseline gap-3 py-1.5 ${className}`}>
      <span className="text-xs" style={{ color: 'var(--c-ink-3)' }}>{label}</span>
      <span
        className={`text-xs font-medium tabular-nums text-right ${isClass ? color : ''}`}
        style={isClass || !color ? undefined : { color }}
      >
        {value}
      </span>
    </div>
  );
};