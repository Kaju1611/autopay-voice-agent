import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, inr, useLive } from '../api'
import { Chip, Err } from '../components.jsx'

export default function Customers() {
  const { data, error, reload } = useLive('/analytics/overview', 3000)
  const [msg, setMsg] = useState('')
  const start = async (id) => {
    try {
      await api(`/recovery/${id}`, { method: 'POST', body: {} })
      setMsg(`Recovery queued for ${id}`)
      setTimeout(reload, 500)
    } catch (e) { setMsg(e.message) }
  }
  return (
    <>
      <h1>Customers</h1>
      <Err msg={error} />
      {msg && <div className="info-bar">{msg}</div>}
      <section className="panel flush">
        <table>
          <thead><tr><th>Customer</th><th>Payment</th><th className="num">Amount</th><th>Status</th><th>Call status</th><th>Recovery</th><th></th></tr></thead>
          <tbody>{(data || []).map(r => (
            <tr key={r.customer_id}>
              <td><Link to={`/customers/${r.customer_id}`}>{r.name}</Link><div className="muted small mono">{r.customer_id}</div></td>
              <td className="mono">{r.payment_id}</td>
              <td className="num">{inr(r.amount, r.currency)}</td>
              <td><Chip v={r.payment_status} /></td>
              <td><Chip v={r.call_status === 'NO_CALL' ? null : r.call_status} /></td>
              <td><Chip v={r.recovery_status} /></td>
              <td className="right">
                <button disabled={r.payment_status !== 'FAILED' || r.recovery_status === 'IN_PROGRESS'} onClick={() => start(r.customer_id)}>
                  Start recovery call
                </button>
              </td>
            </tr>))}
          </tbody>
        </table>
      </section>
    </>
  )
}