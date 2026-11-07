import { useCallback, useEffect, useState } from 'react'
import { Check, Film, RefreshCw } from 'lucide-react'
import { api } from '../lib/api.js'

export default function StockFootageStatus({ hasUpload }) {
  const [status, setStatus] = useState({ state: 'loading', sources: [], visionReady: false })

  const refresh = useCallback(async () => {
    setStatus((current) => ({ ...current, state: 'loading' }))
    try {
      const health = await api.health()
      setStatus({ state: 'ready', sources: health.stock_footage_sources || [], visionReady: Boolean(health.stock_vision_configured) })
    } catch {
      setStatus({ state: 'unavailable', sources: [], visionReady: false })
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const configured = status.sources.length > 0
  const state = hasUpload ? 'override' : status.state === 'ready' ? (configured ? (status.visionReady ? 'connected' : 'missing') : 'missing') : status.state
  const heading = {
    connected: 'Topic-matched footage is ready',
    override: 'Your clip will be used',
    missing: configured ? 'Visual verification needs setup' : 'Original topic visuals are ready',
    loading: 'Checking stock footage',
    unavailable: 'Stock status unavailable',
  }[state]
  const detail = {
    connected: `${status.sources.join(' + ')} clips are checked against the topic, keywords, and sampled video frames. Unverified scenes use topic visuals.`,
    override: 'Your uploaded video replaces the automatic stock clips for this project.',
    missing: configured ? 'Install the Python vision extra, or configure an image-capable LLM_API_KEY and STOCK_VISION_MODEL.' : 'Add PIXABAY_API_KEY in .env to enable verified stock clips, or use original topic visuals.',
    loading: 'Checking your local backend configuration…',
    unavailable: 'Start the backend to check whether automatic clips are available.',
  }[state]

  return (
    <div className={`stock-card stock-card-${state}`} role="status" aria-live="polite">
      <span className="stock-card-icon">{state === 'connected' || state === 'override' ? <Check size={19} /> : <Film size={19} />}</span>
      <span className="stock-card-copy"><strong>{heading}</strong><small>{detail}</small></span>
      {!hasUpload && <button className="stock-refresh" type="button" onClick={refresh} disabled={status.state === 'loading'} aria-label="Refresh stock footage status" title="Refresh status"><RefreshCw size={15} /></button>}
    </div>
  )
}
