import { useSyncExternalStore } from 'react';

/* Canvas charts and SVG paths cannot wear Tailwind classes — both want literal
   colours — so the design tokens are read off the live document instead. The
   series ramp is the validated eight-slot palette from index.css: fixed order,
   never cycled.

   This is an external store rather than state-in-an-effect. The palette changes
   when <html> changes theme or root font size, and only then; routing that
   through useSyncExternalStore means the very first paint already has the right
   colours, instead of a render with no chart followed by one with it. One store
   serves every consumer, so three tabs watching the theme cost one observer. */

const SERIES = [1, 2, 3, 4, 5, 6, 7, 8];

const readTokens = () => {
  const cs = getComputedStyle(document.documentElement);
  const v = name => cs.getPropertyValue(name).trim();
  return {
    series: SERIES.map(i => v(`--s-${i}`)),
    ink: v('--c-ink'),
    ink2: v('--c-ink-2'),
    ink3: v('--c-ink-3'),
    surface: v('--c-surface'),
    line: v('--c-line'),
    grid: v('--c-grid'),
    accent: v('--c-accent'),
    accentSoft: v('--c-accent-soft'),
    good: v('--c-good'),
    warn: v('--c-warn'),
    critical: v('--c-critical'),
  };
};

const tokenKey = () => {
  const el = document.documentElement;
  return `${el.getAttribute('data-theme') || 'dark'}|${getComputedStyle(el).fontSize}`;
};

let cache = { key: null, tokens: null };
const subscribers = new Set();
let observer = null;

function subscribe(cb) {
  subscribers.add(cb);
  if (!observer) {
    observer = new MutationObserver(() => {
      cache = { key: null, tokens: null };
      subscribers.forEach(fn => fn());
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'style', 'class'],
    });
  }
  return () => {
    subscribers.delete(cb);
    if (subscribers.size === 0 && observer) {
      observer.disconnect();
      observer = null;
    }
  };
}

/* The snapshot must be referentially stable between reads, hence the cache
   keyed on what can actually change the resolved values. */
function getSnapshot() {
  const key = tokenKey();
  if (cache.key !== key) cache = { key, tokens: readTokens() };
  return cache.tokens;
}

export function useThemeTokens() {
  return useSyncExternalStore(subscribe, getSnapshot, () => null);
}