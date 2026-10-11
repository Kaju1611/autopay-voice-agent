import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, inr, useLive, when } from '../api'
import { Chip, Empty, Err } from '../components.jsx'

export default function CustomerDetails() {
  const { id } = useParams()
  const { data, error, reload } = useLive(`/customers/${id}/details`, 2000)
  const [msg, setMsg] = useState('')
  if (!data) return <><Err msg={error} /><p className="muted">Loading...</p></>

  const { customer: c, payments, calls, recovery_attempts: att, latest_call: lc } = data
  const failed = payments.find(p => p.status === 'FAILED')
  const live = calls[0] && calls[0].status !== 'COMPLETED'
  const start = async () => {
    try {
      await api(`/recovery/${id}`, { method: 'POST', body: {} })
      setMsg('Recovery queued. The call will start in a few seconds.')
      setTimeout(reload, 500)
    } catch (e) { setMsg(e.message) }
  }
  const states = (lc?.events || []).filter(e => e.event_type.startsWith('STATE:'))
  const tools = (lc?.events || []).filter(e => e.event_type === 'function_call')

  return (
    <>
      <p><Link to="/customers">Customers</Link> / {c.customer_id}</p>
      <div className="head">
        <h1>{c.name}</h1>
        <button className="primary" disabled={!failed || live} onClick={start}>{live ? 'Call in progress...' : 'Start recovery call'}</button>
      </div>
      {msg && <div className="info-bar">{msg}</div>}
      <Err msg={error} />

      <div className="grid2">
        <section className="panel">
          <h2>Customer</h2>
          <dl>
            <dt>ID</dt><dd className="mono">{c.customer_id}</dd>
            <dt>Email</dt><dd>{c.email}</dd>
            <dt>Phone (fictional)</dt><dd className="mono">{c.phone}</dd>
            <dt>Autopay</dt><dd>{c.autopay_enabled ? 'enabled' : 'disabled'}</dd>
            <dt>Call permission</dt><dd>{c.call_permission ? 'granted' : 'not granted'}</dd>
          </dl>
        </section>
        <section className="panel">
          <h2>Payments</h2>
          <table>
            <thead><tr><th>Payment</th><th className="num">Amount</th><th>Status</th><th>Reason</th></tr></thead>
            <tbody>{payments.map(p => (
              <tr key={p.payment_id}><td className="mono">{p.payment_id}</td><td className="num">{inr(p.amount, p.currency)}</td><td><Chip v={p.status} /></td><td className="small">{p.failure_reason || '-'}</td></tr>
            ))}</tbody>
          </table>
        </section>
      </div>

      <div className="grid2">
        <section className="panel">
          <h2>Call history</h2>
          {calls.length === 0 ? <Empty>No calls yet</Empty> : (
            <table>
              <thead><tr><th>Call</th><th>State</th><th>Outcome</th><th className="num">Sec</th></tr></thead>
              <tbody>{calls.map(k => (
                <tr key={k.call_id}><td className="mono small">{k.call_id}</td><td><Chip v={k.status} /></td><td><Chip v={k.outcome} /></td><td className="num">{k.duration ?? '-'}</td></tr>
              ))}</tbody>
            </table>)}
        </section>
        <section className="panel">
          <h2>Recovery attempts</h2>
          {att.length === 0 ? <Empty>None</Empty> : (
            <table>
              <thead><tr><th>Action</th><th>Result</th><th>When</th></tr></thead>
              <tbody>{att.map(a => (
                <tr key={a.id}><td className="small">{a.action.replaceAll('_', ' ').toLowerCase()}</td><td><Chip v={a.result} /></td><td className="small">{when(a.created_at)}</td></tr>
              ))}</tbody>
            </table>)}
        </section>
      </div>

      {lc && (
        <div className="grid2">
          <section className="panel">
            <h2>Transcript {live && <span className="chip info">live</span>}</h2>
            <div className="transcript">
              {(lc.transcript || '').split('\n').filter(Boolean).map((l, i) => {
                const [who, ...rest] = l.split(': ')
                return <p key={i} className={who === 'Agent' ? 'agent' : 'cust'}><b>{who}</b>{rest.join(': ')}</p>
              })}
              {!lc.transcript && <Empty>Waiting for the conversation...</Empty>}
            </div>
          </section>
          <section className="panel">
            <h2>State timeline</h2>
            <ol className="timeline">{states.map((e, i) => (
              <li key={i}>{e.payload.to.replaceAll('_', ' ').toLowerCase()}<span className="muted small">{when(e.created_at)}</span></li>
            ))}</ol>
            {tools.length > 0 && (
              <>
                <h3>Tool calls</h3>
                <table><tbody>{tools.map((e, i) => (
                  <tr key={i}><td className="mono">{e.payload.name}</td><td className="num small">{e.payload.latency_ms} ms</td></tr>
                ))}</tbody></table>
              </>
            )}
          </section>
        </div>
      )}
    </>
  )
}