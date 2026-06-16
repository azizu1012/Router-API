import { ChevronDown } from 'lucide-react';

export default function Select({
  options = [],
  value,
  onChange,
  label,
  hint,
  error,
  disabled,
  placeholder,
  className = '',
  size = 'sm',
}) {
  const sizeClasses = {
    xs: 'h-7 text-[10px] min-w-[90px]',
    sm: 'h-9 text-xs min-w-[110px]',
    md: 'h-11 text-sm min-w-[130px]',
  };

  return (
    <div className={`${className}`}>
      {label && (
        <label className="block text-[10px] font-bold text-base-content/60 mb-1.5 uppercase tracking-wider">
          {label}
        </label>
      )}
      <div className="relative">
        <select
          value={value}
          onChange={onChange}
          disabled={disabled}
          className={`w-full px-3 pr-8 appearance-none rounded-xl border transition-all duration-150
            bg-base-200/50 text-base-content font-bold
            focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/30
            disabled:opacity-50 disabled:cursor-not-allowed
            ${error ? 'border-error/40 focus:ring-error/20 focus:border-error/40' : 'border-base-content/10 hover:border-base-content/20'}
            ${sizeClasses[size] || sizeClasses.sm}`}
        >
          {placeholder && <option value="">{placeholder}</option>}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value} disabled={opt.disabled}>
              {opt.label}
            </option>
          ))}
        </select>
        <div className="absolute inset-y-0 right-0 flex items-center pr-2.5 pointer-events-none text-base-content/40">
          <ChevronDown className="w-3.5 h-3.5" />
        </div>
      </div>
      {error && <p className="text-[10px] text-error mt-1 font-medium">{error}</p>}
      {hint && !error && <p className="text-[10px] text-base-content/40 mt-1">{hint}</p>}
    </div>
  );
}
