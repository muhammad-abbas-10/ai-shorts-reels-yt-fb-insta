import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowUpRight, Check, ChevronRight, CircleAlert, Clock3, Download, Film, RefreshCw, Sparkles } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api.js'
import { dateLabel, durationLabel, platformNames, stageLabel, timeLabel } from '../lib/format.js'
import StatusBadge from '../components/StatusBadge.jsx'

const platforms = [
  { id: 'youtube', symbol: '▶', sub: 'Shorts' },
  { id: 'facebook', symbol: 'f', sub: 'Reels' },
  { id: 'instagram', symbol: '◎', sub: 'Reels' },
]

const stages = ['Writing script', 'Creating voiceover', 'Timing captions', 'Finding visuals', 'Rendering video', 'Checking export']
const stageKeys = ['generating_script', 'generating_audio', 'generating_captions', 'retrieving_footage', 'rendering', 'validating']

export default function JobDetail() {
  const { id } = useParams()
  const [job, setJob] = useState(null)
  const [selected, setSelected] = useState([])
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const initialSelection = useRef(false)

  useEffect(() => {
    initialSelection.current = false
    setJob(null)
    setSelected([])
    setReviewed(false)
    let live = true
    const load = () => api.job(id).then((result) => {
      if (!live) return
      setJob(result)
      if (!initialSelection.current) {
        setSelected(result.config?.platforms?.length ? result.config.platforms : ['youtube'])
        initialSelection.current = true
      }
    }).catch((cause) => { if (live) setError(cause.message) })
    load()
    const interval = setInterval(load, 3500)
    return () => { live = false; clearInterval(interval) }
  }, [id])

  async function publish() {
    if (!selected.length || !reviewed) return
    setError(''); setNotice(''); setBusy('Publishing…')
    try {
      const result = await api.publish(id, { platforms: selected, reviewed: true })
      setNotice(result.queued ? 'Publishing started. Status will update here automatically.' : 'These destinations were already published.')
      setJob(result.job)
      setReviewed(false)
    } catch (cause) { setError(cause.message) }
    finally { setBusy('') }
  }

  async function retry() {
    setError(''); setBusy('Retrying…')
    try {
      await api.retry(id)
      setNotice('Retry started. This page will update automatically.')
    } catch (cause) { setError(cause.message) }
    finally { setBusy('') }
  }

  const successCount = job?.publications?.filter((row) => row.status === 'succeeded').length || 0
  const allSelectedPublished = selected.length > 0 && selected.every((name) => job?.publications?.some((row) => row.platform === name && row.status === 'succeeded'))
  const currentStage = job?.has_video ? stages.length : stageKeys.indexOf(job?.status)

  return (
    <div className="page-content detail-page">
      <Link className="back-link" to="/projects"><ArrowLeft size={16} /> Back to projects</Link>
      {!job ? <div className="detail-loading"><span className="button-spinner" /> Loading project…</div> : <>
        <div className="detail-heading"><div><span className="eyebrow eyebrow-accent">PROJECT / {id.slice(0, 8).toUpperCase()}</span><h1>{job.metadata?.title || job.topic}</h1><div className="detail-subhead"><StatusBadge job={job} /><span>Created {dateLabel(job.created_at)}</span><span>{durationLabel(job)}</span></div></div>{job.has_video && <a href={job.video_url} download={`${(job.metadata?.title || 'short').replace(/[^a-z0-9-]/gi, '-')}.mp4`} className="button button-outline"><Download size={17} /> Download MP4</a>}</div>
        {error && <div className="alert alert-error" role="alert">{error}</div>}
        {notice && <div className="alert alert-success" role="status"><Check size={17} />{notice}</div>}
        {job.error && <div className="alert alert-error" role="alert"><CircleAlert size={18} />{job.error}</div>}
        <div className="detail-layout">
          <div className="detail-main"><div className="preview-panel"><div className="panel-heading"><div><span className="eyebrow">THE FINAL FRAME</span><h2>Video preview</h2></div><span className="preview-ratio">9:16 · VERTICAL</span></div><div className="video-stage">{job.has_video ? <video key={job.video_url} src={job.video_url} poster={job.thumbnail_url} controls playsInline preload="metadata" aria-label="Video preview" /> : <div className="video-waiting"><div className="waiting-orb"><Film size={35} /></div><strong>Your video is taking shape</strong><span>{stageLabel(job.status)}</span></div>}</div><div className="preview-foot"><span><Clock3 size={15} /> {job.has_video ? `${durationLabel(job)} · ${job.metadata?.validation?.width} × ${job.metadata?.validation?.height}` : 'Working in the background'}</span><span>{job.has_video ? 'MP4 EXPORT' : 'AUTO REFRESH ON'}</span></div></div>
            <div className="info-panel"><div className="panel-heading"><div><span className="eyebrow">PRODUCTION NOTES</span><h2>About this video</h2></div></div><div className="info-grid"><div><span>TOPIC</span><strong>{job.topic}</strong></div><div><span>TARGET LENGTH</span><strong>{job.config?.duration_seconds} sec</strong></div><div><span>TONE</span><strong>{job.config?.tone || '—'}</strong></div><div><span>CREATED</span><strong>{timeLabel(job.created_at)}</strong></div>{job.metadata?.voice_provider && <div><span>VOICE SOURCE</span><strong>{job.metadata.voice_provider === 'windows_sapi' ? 'Windows voice fallback' : 'Edge neural voice'}</strong></div>}{job.metadata?.visual_source && <div><span>VISUAL SOURCE</span><strong>{{ stock: 'Verified stock', mixed: 'Stock + topic visuals', explainer: 'Narration visuals', original_explainer: 'Narration visuals', gradient: 'Gradient', uploaded: 'Your uploaded clip' }[job.metadata.visual_source] || job.metadata.visual_source}</strong></div>}{job.config?.background_keywords?.length > 0 && <div><span>BACKGROUND KEYWORDS</span><strong>{job.config.background_keywords.join(', ')}</strong></div>}</div><div className="description-block"><span>DESCRIPTION</span><p>{job.metadata?.description || 'Description will appear when the video is ready.'}</p></div>{job.metadata?.hashtags?.length > 0 && <div className="tag-list">{job.metadata.hashtags.map((tag) => <span key={tag}>{tag}</span>)}</div>}</div>
          </div>
          <div className="detail-side"><div className="progress-panel"><div className="side-title"><span className="eyebrow">THE JOURNEY</span><h2>Production progress</h2></div><div className="timeline">{stages.map((stage, index) => <div key={stage} className={`timeline-step ${currentStage >= index ? 'done' : ''} ${currentStage === index && !job.has_video ? 'current' : ''}`}><span className="timeline-marker">{currentStage > index || job.has_video ? <Check size={13} /> : String(index + 1).padStart(2, '0')}</span><div><strong>{stage}</strong>{currentStage === index && !job.has_video && <small>In progress</small>}</div></div>)}</div>{!job.has_video && job.status === 'failed' && <button className="button button-outline full-width" type="button" onClick={retry} disabled={Boolean(busy)}><RefreshCw size={16} /> {busy || 'Retry generation'}</button>}</div>
            {job.has_video && <div className="publish-panel"><div className="side-title"><span className="eyebrow">SHARE YOUR WORK</span><h2>Publish this video</h2><p>Select where it goes. YouTube uses {job.metadata?.visibility?.youtube || 'private'} visibility; Facebook and Instagram Reels are public.</p></div><div className="publish-choices">{platforms.map((platform) => { const row = job.publications.find((item) => item.platform === platform.id); const already = row?.status === 'succeeded'; return <button type="button" key={platform.id} className={`publish-choice ${selected.includes(platform.id) ? 'selected' : ''}`} onClick={() => setSelected((current) => current.includes(platform.id) ? current.filter((item) => item !== platform.id) : [...current, platform.id])}><span className={`platform-glyph glyph-${platform.id}`}>{platform.symbol}</span><span className="choice-name"><strong>{platformNames[platform.id]}</strong><small>{already ? 'Published' : row?.status === 'processing' ? 'Processing' : platform.sub}</small></span><span className="choice-check">{selected.includes(platform.id) && <Check size={13} />}</span></button> })}</div><label className="review-check"><input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} /><span>I reviewed the video, title, description, and public destinations.</span></label><button className="button button-primary full-width" onClick={publish} disabled={Boolean(busy) || !selected.length || !reviewed || allSelectedPublished}>{busy || (allSelectedPublished ? 'Already published' : 'Publish selected')} <ArrowUpRight size={17} /></button><p className="publish-safety"><Sparkles size={15} /> Successful destinations will not be uploaded twice.</p></div>}
            {job.publications.length > 0 && <div className="publication-panel"><div className="side-title"><span className="eyebrow">CHANNEL ACTIVITY</span><h2>Publication status</h2></div>{job.publications.map((row) => <div className="publication-row" key={row.platform}><span className={`platform-glyph glyph-${row.platform}`}>{row.platform === 'youtube' ? '▶' : row.platform === 'facebook' ? 'f' : '◎'}</span><div><strong>{platformNames[row.platform]}</strong><small>{row.error || (row.status === 'succeeded' ? 'Published successfully' : row.status === 'processing' ? 'Finishing on platform' : stageLabel(row.status))}</small></div>{row.url ? <a href={row.url} target="_blank" rel="noreferrer" aria-label={`Open ${platformNames[row.platform]} post`}><ArrowUpRight size={17} /></a> : <ChevronRight size={16} />}</div>)}{successCount > 0 && <div className="publication-summary"><Check size={15} /> {successCount} live {successCount === 1 ? 'destination' : 'destinations'}</div>}</div>}
          </div>
        </div>
      </>}
    </div>
  )
}
