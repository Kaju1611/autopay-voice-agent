import { Link } from 'react-router-dom'
import { useLive, when } from '../api'
import { Chip, Empty, Err } from '../components.jsx'

export default function Calls() {
  const { data, error } = useLive('/calls', 3000)
  return (
    <>
      <h1>Calls</h1>
      <Err msg={error} />
      <section className="panel flush">
        {data && data.length === 0 ? <Empty>No calls yet. Start one from the Customers page.</Empty> : (
          <table>
            <thead><tr><th>Call ID</th><th>Customer</th><th>Status</th><th className="num">Duration (s)</th><th>Outcome</th><th>Started</th></tr></thead>
            <tbody>{(data || []).map(c => (
              <tr key={c.call_id}>
                <td className="mono small">{c.call_id}</td>
                <td><Link to={`/customers/${c.customer_id}`}>{c.customer_id}</Link></td>
                <td><Chip v={c.status} /></td>
                <td className="num">{c.duration ?? '-'}</td>
                <td><Chip v={c.outcome} /></td>
                <td className="small">{when(c.started_at || c.created_at)}</td>
              </tr>))}
            </tbody>
          </table>)}
      </section>
    </>
  )
}