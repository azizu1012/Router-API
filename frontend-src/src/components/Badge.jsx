const variants = {
  primary: 'bg-primary/10 text-primary border border-primary/20',
  success: 'bg-success/10 text-success border border-success/20',
  warning: 'bg-warning/10 text-warning border border-warning/20',
  error: 'bg-error/10 text-error border border-error/20',
  info: 'bg-info/10 text-info border border-info/20',
  ghost: 'bg-base-content/5 text-base-content/60 border border-base-content/10',
};

const sizes = {
  xs: 'px-1.5 py-0.5 text-[9px]',
  sm: 'px-2 py-0.5 text-[10px]',
  md: 'px-2.5 py-1 text-xs',
};

export default function Badge({
  variant = 'ghost',
  size = 'xs',
  dot,
  icon: Icon,
  children,
  className = '',
}) {
  return (
    <span className={`inline-flex items-center gap-1 font-extrabold uppercase rounded-full ${variants[variant] || variants.ghost} ${sizes[size] || sizes.xs} ${className}`}>
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current" />}
      {Icon && <Icon className="w-3 h-3" />}
      {children}
    </span>
  );
}

Badge.Status = function StatusBadge({ active, children, ...props }) {
  return (
    <Badge variant={active ? 'success' : 'ghost'} size="sm" dot={!active} {...props}>
      {children || (active ? 'Active' : 'Inactive')}
    </Badge>
  );
};
