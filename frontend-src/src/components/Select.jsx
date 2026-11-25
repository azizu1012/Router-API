import { ChevronDown } from 'lucide-react';

const sizeClasses = {
  xs: 'h-7 text-xs',
  sm: 'h-8 text-xs',
  md: 'h-10 text-sm',
};

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
  return (
    <div className={className}>
      {label && <label className="label-micro block mb-1.5">{label}</label>}
      <div className="relative">
        <select
          value={value}
          onChange={onChange}
          disabled={disabled}
          className={`select w-full pr-8 appearance-none ${sizeClasses[size] || sizeClasses.sm}`}
          style={error ? { borderColor: 'var(--c-critical)' } : undefined}
        >
          {placeholder && <option value="">{placeholder}</option>}
          {options.map(opt => (
            <option key={opt.value} value={opt.value} disabled={opt.disabled}>
              {opt.label}
            </option>
          ))}
        </select>
        <div className="absolute inset-y-0 right-0 flex items-center pr-2.5 pointer-events-none"
             style={{ color: 'var(--c-ink-3)' }}>
          <ChevronDown className="w-3.5 h-3.5" />
        </div>
      </div>
      {error && <p className="text-[0.6875rem] mt-1" style={{ color: 'var(--c-critical)' }}>{error}</p>}
      {hint && !error && <p className="text-[0.6875rem] mt-1" style={{ color: 'var(--c-ink-3)' }}>{hint}</p>}
    </div>
  );
}