import { Link } from 'react-router-dom'
import { useLive, when } from '../api'
import { Chip, Empty, Err } from '../components.jsx'

export default function Recovery() {
  const { data, error } = useLive('/recovery', 3000)
  return (
    <>
      <h1>Recovery</h1>
      <Err msg={error} />
      <section className="panel flush">
        {data && data.length === 0 ? <Empty>No recovery attempts yet.</Empty> : (
          <table>
            <thead><tr><th>Payment</th><th>Customer</th><th>Recovery attempt</th><th>Result</th><th>Timestamp</th></tr></thead>
            <tbody>{(data || []).map(a => (
              <tr key={a.id}>
                <td className="mono">{a.payment_id || '-'}</td>
                <td><Link to={`/customers/${a.customer_id}`}>{a.customer_id}</Link></td>
                <td>{a.action.replaceAll('_', ' ').toLowerCase()}</td>
                <td><Chip v={a.result} /></td>
                <td className="small">{when(a.created_at)}</td>
              </tr>))}
            </tbody>
          </table>)}
      </section>
    </>
  )
}