/* Shared skeleton primitives. Every class here is static — Tailwind's JIT
   cannot see an interpolated `md:grid-cols-${n}` and never emits it, so the
   column count is resolved through a lookup table instead. */

const COLS = {
  1: 'md:grid-cols-1',
  2: 'md:grid-cols-2',
  3: 'md:grid-cols-3',
  4: 'md:grid-cols-4',
  5: 'md:grid-cols-5',
  6: 'md:grid-cols-6',
};

export default function Loading({ message = 'Loading...' }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[24rem] gap-3" aria-busy="true" aria-live="polite">
      <span className="loading loading-spinner loading-md" style={{ color: 'var(--c-accent)' }} />
      <span className="text-xs" style={{ color: 'var(--c-ink-3)' }}>{message}</span>
    </div>
  );
}

export function CardSkeleton({ count = 3, columns = 3 }) {
  return (
    <div className={`grid grid-cols-1 ${COLS[columns] || COLS[3]} gap-3`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="glass-card rounded-xl p-4 animate-pulse">
          <div className="flex items-center justify-between mb-3">
            <div className="h-2.5 w-20 rounded" style={{ background: 'var(--c-surface-3)' }} />
            <div className="h-7 w-7 rounded-md" style={{ background: 'var(--c-surface-3)' }} />
          </div>
          <div className="h-6 w-16 rounded mb-2.5" style={{ background: 'var(--c-surface-3)' }} />
          <div className="h-1 w-full rounded-full mb-2.5" style={{ background: 'var(--c-surface-3)' }} />
          <div className="h-2.5 w-24 rounded" style={{ background: 'var(--c-surface-3)' }} />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 5, cols = 4 }) {
  return (
    <div className="space-y-1.5 animate-pulse">
      {Array.from({ length: rows }).map((_, ri) => (
        <div key={ri} className="flex gap-4 p-2.5">
          {Array.from({ length: cols }).map((_, ci) => (
            <div key={ci} className="h-3 flex-1 rounded" style={{ background: 'var(--c-surface-2)' }} />
          ))}
        </div>
      ))}
    </div>
  );
}