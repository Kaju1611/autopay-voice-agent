const TONE = {
  SUCCESS: 'good', RECOVERED: 'good', COMPLETED: 'neutral', FAILED: 'bad', RETRY_FAILED: 'bad', DECLINED: 'bad',
  WRONG_NUMBER: 'bad', NO_RESPONSE: 'warn', RETRY_LATER: 'warn', HUMAN_AGENT: 'warn', ALREADY_PAID: 'warn',
  IN_PROGRESS: 'info', NOT_CONTACTED: 'neutral', RETRY_REQUESTED: 'info', QUEUED: 'info', DIALING: 'info', SCHEDULED: 'warn',
}

export const Chip = ({ v }) =>
  v ? <span className={`chip ${TONE[v] || 'info'}`}>{String(v).replaceAll('_', ' ').toLowerCase()}</span>
    : <span className="muted">-</span>

export const Err = ({ msg }) => (msg ? <div className="err">{msg}</div> : null)
export const Empty = ({ children }) => <div className="empty">{children}</div>