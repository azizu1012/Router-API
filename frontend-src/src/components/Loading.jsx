export default function Loading({ message = 'Loading...' }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[400px] gap-3">
      <span className="loading loading-spinner loading-md text-primary" />
      <span className="text-sm font-semibold opacity-70">{message}</span>
    </div>
  );
}

export function CardSkeleton({ count = 3, columns = 3 }) {
  return (
    <div className={`grid grid-cols-1 md:grid-cols-${columns} gap-4`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-2xl border border-base-content/5 bg-base-200/20 p-5 animate-pulse">
          <div className="flex items-center justify-between mb-3">
            <div className="h-3 w-20 bg-base-300/30 rounded" />
            <div className="h-8 w-8 bg-base-300/30 rounded-lg" />
          </div>
          <div className="h-7 w-16 bg-base-300/30 rounded mb-2" />
          <div className="h-1.5 w-full bg-base-300/30 rounded-full mb-2" />
          <div className="h-3 w-24 bg-base-300/30 rounded" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 5, cols = 4 }) {
  return (
    <div className="space-y-2 animate-pulse">
      {Array.from({ length: rows }).map((_, ri) => (
        <div key={ri} className="flex gap-4 p-3">
          {Array.from({ length: cols }).map((_, ci) => (
            <div key={ci} className="h-4 flex-1 bg-base-300/20 rounded" />
          ))}
        </div>
      ))}
    </div>
  );
}
