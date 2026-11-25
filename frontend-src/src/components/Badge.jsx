/* Tier / status chips. Colour is a tint of the token role plus the label, so a
   chip never relies on hue alone. */

const variants = {
  primary: { bg: 'var(--c-accent-soft)', fg: 'var(--c-accent)', bd: 'transparent' },
  success: { bg: 'color-mix(in srgb, var(--c-good) 12%, transparent)', fg: 'var(--c-good)', bd: 'transparent' },
  warning: { bg: 'color-mix(in srgb, var(--c-warn) 14%, transparent)', fg: 'var(--c-warn)', bd: 'transparent' },
  error: { bg: 'color-mix(in srgb, var(--c-critical) 12%, transparent)', fg: 'var(--c-critical)', bd: 'transparent' },
  info: { bg: 'color-mix(in srgb, var(--c-accent) 12%, transparent)', fg: 'var(--c-accent)', bd: 'transparent' },
  ghost: { bg: 'var(--c-surface-2)', fg: 'var(--c-ink-3)', bd: 'var(--c-line)' },
};

const sizes = {
  xs: 'px-1.5 py-0.5 text-[0.625rem]',
  sm: 'px-2 py-0.5 text-[0.6875rem]',
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
  const v = variants[variant] || variants.ghost;
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-semibold tracking-wide rounded-md whitespace-nowrap ${sizes[size] || sizes.xs} ${className}`}
      style={{ background: v.bg, color: v.fg, border: `1px solid ${v.bd}` }}
    >
      {dot && (
        <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-60"
                style={{ background: 'currentColor' }} />
          <span className="relative inline-flex rounded-full h-1.5 w-1.5" style={{ background: 'currentColor' }} />
        </span>
      )}
      {Icon && <Icon className="w-3 h-3" />}
      {children}
    </span>
  );
}

Badge.Status = function StatusBadge({ active, children, ...props }) {
  return (
    <Badge variant={active ? 'success' : 'ghost'} size="sm" dot={Boolean(active)} {...props}>
      {children || (active ? 'Active' : 'Inactive')}
    </Badge>
  );
};