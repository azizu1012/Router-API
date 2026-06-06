// ─── Global State ───────────────────────────────────────────────
export const state = {
  tok: sessionStorage.getItem('_rt') || null,
  usr: null,
  ch: {},       // Chart instances
  cur: null,    // Current active tab
  timer: null,  // Auto-refresh timer

  // Cached API data
  rawKeys: [],
  rawAccounts: [],
  statsData: null,
  myStatsData: null,
  rawEndpoints: null,
  rawPenalties: null,
  rawPools: null,
  rawPoolsDetail: null,
  rawMyAcc: null,

  // UI preferences (restored from localStorage)
  lang: localStorage.getItem('_rl') || 'vi',
  theme: localStorage.getItem('_rtm') || 'auto',
};

// Color palette for charts
export const CLR = [
  '#6366f1', // primary/indigo
  '#06b6d4', // cyan
  '#10b981', // emerald
  '#f59e0b', // amber
  '#f43f5e', // rose
  '#a855f7', // purple
  '#3b82f6', // blue
];

// Shorthand getElementById
export const $ = id => document.getElementById(id);
