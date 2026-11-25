/**
 * Copying to the clipboard, on every origin this dashboard actually runs on.
 *
 * `navigator.clipboard` only exists in a secure context (https, or http on
 * localhost). An install served over plain http on a LAN address — which is the
 * common case for this router — gets `undefined`, and a `try { … } catch {}`
 * around it swallows the TypeError. The button then looks dead: nothing copied,
 * no feedback, no error.
 *
 * So: try the modern API when it exists, fall back to a hidden textarea with
 * `document.execCommand('copy')`, and return a boolean either way so the caller
 * can tell the user what happened instead of guessing.
 */
export async function copyText(text) {
  if (!text) return false;

  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* permission denied or not focused — fall through */
    }
  }

  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    area.setSelectionRange(0, area.value.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

export default copyText;