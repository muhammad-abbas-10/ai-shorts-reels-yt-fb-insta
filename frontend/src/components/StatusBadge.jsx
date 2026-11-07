import { jobState } from '../lib/format.js'

export default function StatusBadge({ job, label, tone }) {
  const state = job ? jobState(job) : { label, tone }
  return <span className={`status-badge status-${state.tone}`}><span className="status-dot" />{state.label}</span>
}
