import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { getToken } from './api'
import Login from './pages/Login.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Customers from './pages/Customers.jsx'
import CustomerDetails from './pages/CustomerDetails.jsx'
import Calls from './pages/Calls.jsx'
import Recovery from './pages/Recovery.jsx'

function Shell({ children }) {
  const nav = useNavigate()
  return (
    <div className="shell">
      <aside>
        <div className="brand">Recovery Desk<small>autopay voice agent</small></div>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/customers">Customers</NavLink>
          <NavLink to="/calls">Calls</NavLink>
          <NavLink to="/recovery">Recovery</NavLink>
        </nav>
        <div className="notice">All customers are fictional. Live calls go only to the authorised demo number.</div>
        <button className="link" onClick={() => { localStorage.removeItem('token'); nav('/login') }}>Sign out</button>
      </aside>
      <main>{children}</main>
    </div>
  )
}

const Guard = ({ children }) => (getToken() ? <Shell>{children}</Shell> : <Navigate to="/login" replace />)

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Guard><Dashboard /></Guard>} />
      <Route path="/customers" element={<Guard><Customers /></Guard>} />
      <Route path="/customers/:id" element={<Guard><CustomerDetails /></Guard>} />
      <Route path="/calls" element={<Guard><Calls /></Guard>} />
      <Route path="/recovery" element={<Guard><Recovery /></Guard>} />
    </Routes>
  )
}