import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { Err } from '../components.jsx'

export default function Login() {
  const nav = useNavigate()
  const [f, setF] = useState({ username: 'admin', password: '' })
  const [err, setErr] = useState('')
  const submit = async (e) => {
    e.preventDefault()
    try {
      const r = await api('/auth/login', { method: 'POST', body: f })
      localStorage.setItem('token', r.access_token)
      nav('/')
    } catch (x) { setErr(x.message) }
  }
  return (
    <div className="login">
      <form onSubmit={submit} className="panel">
        <h1>Recovery Desk</h1>
        <p className="muted">Sign in with ADMIN_USERNAME / ADMIN_PASSWORD from your .env</p>
        <label>Username<input value={f.username} onChange={e => setF({ ...f, username: e.target.value })} /></label>
        <label>Password<input type="password" value={f.password} onChange={e => setF({ ...f, password: e.target.value })} /></label>
        <Err msg={err} />
        <button className="primary">Sign in</button>
      </form>
    </div>
  )
}