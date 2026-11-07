export default function Brand({ compact = false }) {
  return (
    <div className={`brand ${compact ? 'brand-compact' : ''}`} aria-label="AI Shorts Studio">
      <span className="brand-mark" aria-hidden="true"><span /><span /><span /><span /></span>
      {!compact && <span className="brand-wordmark">AI Shorts<span>Studio</span></span>}
    </div>
  )
}
