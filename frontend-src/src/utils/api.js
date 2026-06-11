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
    throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
  }

  return response.json();
}
