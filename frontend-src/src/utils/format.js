/* Number + date formatting shared by every tab.

   `fmt` is for quantities (tokens, requests) — it abbreviates at 1e3/1e6.
   `fmtD` is for instants (epoch seconds) and is what date columns must use;
   feeding an epoch into `fmt` is how a table once rendered 1791.21M instead
   of a date. */

export function fmt(v) {
  if (v === undefined || v === null || v === '') return '0';
  const n = Number(v);
  if (!Number.isFinite(n)) return '0';
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

const LOCALES = { vi: 'vi-VN', en: 'en-GB', ja: 'ja-JP' };

/* Epoch seconds → readable local date-time. */
export function fmtD(epochSeconds, lang = 'vi') {
  if (!epochSeconds) return '—';
  const d = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(LOCALES[lang] || 'vi-VN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/* Countdown to an epoch second. `expired` is the caller's word for zero-ish. */
export function relt(targetEpoch, expired = 'Expired') {
  if (!targetEpoch) return '—';
  const diff = targetEpoch - Date.now() / 1000;
  if (diff <= 0) return expired;

  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const s = Math.floor(diff % 60);

  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}