/* One page header for every tab. Before this existed each tab spelled its own
   title out — five different sizes, three colour treatments — so the app read
   as twelve products. */

export default function PageHeader({ title, subtitle, actions, className = '' }) {
  return (
    <div className={`flex flex-wrap items-start justify-between gap-3 ${className}`}>
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--c-ink)' }}>{title}</h1>
        {subtitle && (
          <p className="text-xs mt-1 leading-relaxed" style={{ color: 'var(--c-ink-3)' }}>{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}