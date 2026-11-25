/* Buttons inherit their colour from the daisyUI tokens set in index.css, so
   `--c-accent` / `--c-critical` drive every state. No hard-coded hue. */

export default function Button({
  variant = 'primary',
  size = 'sm',
  icon: Icon,
  children,
  loading,
  disabled,
  className = '',
  ...props
}) {
  const base = 'btn inline-flex items-center justify-center font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
  const classes = {
    primary: 'btn-primary',
    secondary: 'btn-neutral',
    ghost: 'btn-ghost',
    outline: 'btn-outline',
    danger: 'btn-error',
    success: 'btn-success',
  };
  const sizes = {
    xs: 'btn-xs',
    sm: 'btn-sm',
    md: '',
    lg: 'btn-lg',
  };

  return (
    <button
      className={`${base} ${classes[variant] || classes.primary} ${sizes[size] || sizes.sm} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <span className="loading loading-spinner loading-xs" />
      ) : Icon ? (
        <Icon className="w-3.5 h-3.5" />
      ) : null}
      {children}
    </button>
  );
}