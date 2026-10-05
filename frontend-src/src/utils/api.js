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

  const response = await fetch(endpoint, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    // Admin endpoints raise HTTPException(detail=...), account routes return
    // {error: ...}. Reading only one of them turned every failure into a bare
    // "HTTP error! status: 404" with the actual reason discarded.
    const message = errorData.error
      || errorData.detail
      || (typeof errorData.detail === 'string' ? JSON.parse(errorData.detail) : null)
      || `HTTP error! status: ${response.status}`;
    throw new Error(message);
  }

  return response.json();
}
