export async function api(endpoint, options = {}, token = null) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };

  const activeToken = token || sessionStorage.getItem('_rt');
  if (activeToken) {
    headers['Authorization'] = `Bearer ${activeToken}`;
    headers['X-Dashboard-Token'] = activeToken;
  }

  const response = await fetch(endpoint, { ...options, headers });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    // Admin endpoints raise HTTPException(detail=...), which FastAPI may send as
    // a string OR as a JSON-encoded string inside `detail`; account routes
    // return {error: ...}. Read all three rather than guessing, so a failure
    // surfaces the server's actual reason instead of a bare status code.
    const detail = errorData.detail;
    let message = errorData.error;
    if (!message && typeof detail === 'string') {
      try {
        const parsed = JSON.parse(detail);
        message = parsed?.error || parsed?.detail || parsed?.message || detail;
      } catch {
        message = detail;
      }
    }
    if (!message && detail && typeof detail === 'object') {
      message = detail.message || detail.error || JSON.stringify(detail);
    }
    throw new Error(message || `HTTP error! status: ${response.status}`);
  }

  return response.json();
}