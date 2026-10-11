import { useState } from 'react'
import { api, useLive } from '../api'
import { Err } from '../components.jsx'

function Latency() {
  const { data } = useLive('/analytics/latency', 5000)
  if (!data) return null
  const rows = []
  for (const [svc, m] of Object.entries(data))
    for (const [route, s] of Object.entries(m.routes || {})) rows.push({ svc, route, ...s })
  const critical = rows.filter(r => r.route.startsWith('function:') || r.budget_p95).sort((a, b) => b.p95 - a.p95)
  const counters = Object.entries(data).flatMap(([svc, m]) => Object.entries(m.counters || {}).map(([k, v]) => [`${svc}: ${k}`, v]))
  return (
    <section className="panel">
      <h2>Latency <small className="muted">rolling window, ms</small></h2>
      {critical.length === 0 ? <p className="muted">Run a recovery call to populate voice tool-call latency.</p> : (
        <table>
          <thead><tr><th>Service</th><th>Route / tool</th><th className="num">Calls</th><th className="num">p50</th><th className="num">p95</th><th className="num">p99</th><th className="num">Budget p95</th></tr></thead>
          <tbody>{critical.slice(0, 12).map(r => (
            <tr key={r.svc + r.route}>
              <td>{r.svc}</td><td className="mono">{r.route}</td><td className="num">{r.count}</td><td className="num">{r.p50}</td>
              <td className={'num ' + (r.within_budget === false ? 'bad-text' : '')}>{r.p95}</td><td className="num">{r.p99}</td>
              <td className="num">{r.budget_p95 ? <span className={'chip ' + (r.within_budget ? 'good' : 'bad')}>{r.budget_p95}</span> : <span className="muted">-</span>}</td>
            </tr>))}
          </tbody>
        </table>)}
      {counters.length > 0 && <p className="muted small">{counters.map(([k, v]) => `${k}=${v}`).join('  |  ')}</p>}
    </section>
  )
}

function MockControl() {
  const [msg, setMsg] = useState('')
  const set = async (outcome) => {
    try {
      await api('/demo/payment-outcome', { method: 'POST', body: { outcome } })
      setMsg(outcome ? `Mock payments now return ${outcome}` : 'Per-customer defaults restored')
    } catch (e) { setMsg(e.message) }
  }
  return (
    <section className="panel">
      <h2>Mock payment provider</h2>
      <p className="muted">Force the outcome of every retry for the demo (no real money moves).</p>
      <div className="row">
        {['SUCCESS', 'FAILED', 'DECLINED'].map(o => <button key={o} onClick={() => set(o)}>{o.toLowerCase()}</button>)}
        <button className="link" onClick={() => set(null)}>reset</button>
      </div>
      {msg && <p className="small">{msg}</p>}
    </section>
  )
}

export default function Dashboard() {
  const { data: a, error } = useLive('/analytics', 3000)
  const K = ({ label, value, sub }) => (
    <div className="kpi"><div className="v">{value ?? '-'}</div><div className="l">{label}</div>{sub && <div className="s">{sub}</div>}</div>
  )
  return (
    <>
      <h1>Dashboard</h1>
      <Err msg={error} />
      {a && (
        <div className="kpis">
          <K label="Total customers" value={a.total_customers} />
          <K label="Failed payments" value={a.failed_payments} sub="awaiting recovery" />
          <K label="Calls attempted" value={a.calls_attempted} sub={`${a.calls_answered} answered`} />
          <K label="Successful recoveries" value={a.successful_recoveries} />
          <K label="Recovery rate" value={`${a.recovery_rate}%`} sub="recoveries / calls attempted" />
        </div>
      )}
      {a && Object.keys(a.outcomes).length > 0 && (
        <section className="panel">
          <h2>Call outcomes</h2>
          <div className="bars">{Object.entries(a.outcomes).map(([k, v]) => (
            <div key={k} className="bar"><span>{k.replaceAll('_', ' ').toLowerCase()}</span><i style={{ width: `${Math.max(6, v / a.calls_attempted * 100)}%` }} /><b>{v}</b></div>
          ))}</div>
        </section>
      )}
      <div className="grid2"><Latency /><MockControl /></div>
    </>
  )
}