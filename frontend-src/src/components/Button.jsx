const variants = {
  primary: 'bg-primary text-primary-content border border-primary/40 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.28),0_4px_12px_rgba(99,102,241,0.25)] hover:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.36),0_6px_18px_rgba(99,102,241,0.35)] hover:brightness-105 active:translate-y-[1px] active:shadow-[inset_0_1px_0_0_rgba(255,255,255,0.15),0_2px_4px_rgba(99,102,241,0.2)]',
  secondary: 'bg-base-200/80 text-base-content border border-base-content/10 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)] hover:bg-base-300/80 hover:border-base-content/20 active:translate-y-[1px]',
  ghost: 'text-base-content/75 hover:bg-base-content/6 hover:text-base-content border border-transparent active:translate-y-[1px]',
  outline: 'border border-base-content/15 text-base-content/85 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)] hover:border-primary/40 hover:text-primary hover:bg-primary/5 active:translate-y-[1px]',
  danger: 'bg-error/15 text-error border border-error/25 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)] hover:bg-error/25 active:translate-y-[1px]',
  success: 'bg-success/15 text-success border border-success/25 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.06)] hover:bg-success/25 active:translate-y-[1px]',
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
      className={`btn inline-flex items-center justify-center font-semibold transition-all duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] active:scale-[0.98] select-none disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 disabled:active:translate-y-0 ${variants[variant] || variants.primary} ${sizes[size] || sizes.sm} ${className}`}
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
