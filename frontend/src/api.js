import { useEffect, useRef, useState, useCallback } from 'react'

export const getToken = () => localStorage.getItem('token')

export async function api(path, { method = 'GET', body } = {}) {
  const r = await fetch('/api' + path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (r.status === 401 && !path.startsWith('/auth')) {
    localStorage.removeItem('token'); window.location.hash = '#/login'; throw new Error('Session expired')
  }
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : r.statusText)
  return data
}

/** Loads on mount, then re-polls every `ms` (live view while a call is running). */
export function useLive(path, ms = 3000) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const alive = useRef(true)
  const load = useCallback(async () => {
    try { const d = await api(path); if (alive.current) { setData(d); setError(null) } }
    catch (e) { if (alive.current) setError(e.message) }
  }, [path])
  useEffect(() => {
    alive.current = true; load()
    const t = setInterval(load, ms)
    return () => { alive.current = false; clearInterval(t) }
  }, [load, ms])
  return { data, error, reload: load }
}

export const inr = (n, cur = 'INR') =>
  n == null ? '-' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: cur, maximumFractionDigits: 0 }).format(n)

export const when = (iso) =>
  iso ? new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').toLocaleString() : '-'