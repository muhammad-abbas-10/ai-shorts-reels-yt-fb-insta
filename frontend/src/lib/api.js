async function request(path, options = {}) {
  let response
  try {
    response = await fetch(path, options)
  } catch {
    throw new Error('Cannot reach the local API. Start the Python backend on port 8000.')
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = data.detail
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => item.msg).join(', ')
        : `Request failed (${response.status})`
    throw new Error(message)
  }
  return data
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  overview: () => request('/api/overview'),
  health: () => request('/api/health'),
  jobs: (limit = 60) => request(`/api/jobs?limit=${limit}`),
  job: (id) => request(`/api/jobs/${encodeURIComponent(id)}`),
  create: (body) => request('/api/jobs', json(body)),
  publish: (id, body) => request(`/api/jobs/${encodeURIComponent(id)}/publish`, json(body)),
  retry: (id) => request(`/api/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  uploadFootage: (file) => {
    const body = new FormData()
    body.append('file', file)
    return request('/api/uploads/footage', { method: 'POST', body })
  },
}
