const variants = {
  primary: 'bg-primary text-primary-content hover:brightness-110 shadow-lg shadow-primary/25 border border-primary/30',
  secondary: 'bg-base-200/70 text-base-content hover:bg-base-300/70 border border-base-content/10',
  ghost: 'text-base-content/70 hover:bg-base-content/5 hover:text-base-content border border-transparent',
  outline: 'border border-base-content/20 text-base-content/80 hover:border-primary/40 hover:text-primary bg-transparent',
  danger: 'bg-error/15 text-error hover:bg-error/25 border border-error/20',
  success: 'bg-success/15 text-success hover:bg-success/25 border border-success/20',
};

const sizes = {
  xs: 'h-7 px-2.5 text-[10px] rounded-lg gap-1',
  sm: 'h-8 px-3.5 text-xs rounded-xl gap-1.5',
  md: 'h-10 px-5 text-sm rounded-xl gap-2',
  lg: 'h-12 px-7 text-sm rounded-2xl gap-2.5',
};

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
  return (
    <button
      className={`btn inline-flex items-center justify-center font-bold transition-all duration-150 ease-out active:scale-[0.97] disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 ${variants[variant] || variants.primary} ${sizes[size] || sizes.sm} ${className}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <span className="loading loading-spinner loading-xs" />
      ) : Icon ? (
        <Icon className={`${size === 'xs' ? 'w-3.5 h-3.5' : size === 'sm' ? 'w-4 h-4' : 'w-5 h-5'}`} />
      ) : null}
      {children && <span>{children}</span>}
    </button>
  );
}
