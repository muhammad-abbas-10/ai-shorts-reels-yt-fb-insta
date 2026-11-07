import { useEffect, useState } from 'react'
import { ArrowRight, ArrowUpRight, Clapperboard, Film, Play, Radio, Sparkles, WandSparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'
import ProjectCard from '../components/ProjectCard.jsx'
import EmptyState from '../components/EmptyState.jsx'
import { platformNames } from '../lib/format.js'

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    const load = () => api.overview().then((result) => {
      if (live) { setData(result); setError('') }
    }).catch((cause) => { if (live) setError(cause.message) })
    load()
    const interval = setInterval(load, 10000)
    return () => { live = false; clearInterval(interval) }
  }, [])

  const recent = data?.recent_jobs || []
  const featured = recent.find((job) => job.has_video)
  const configured = data?.accounts.filter((account) => account.configured).length || 0
  const stats = [
    { label: 'Total projects', value: data?.stats.total ?? '—', icon: Clapperboard, note: 'Ideas brought to life' },
    { label: 'Videos rendered', value: data?.stats.rendered ?? '—', icon: Film, note: 'Ready for your review' },
    { label: 'Published posts', value: data?.stats.published ?? '—', icon: Radio, note: 'Across your channels' },
    { label: 'Channels set up', value: data ? `${configured}/3` : '—', icon: Sparkles, note: 'One connected workspace' },
  ]

  return (
    <div className="page-content dashboard-page">
      <div className="page-intro intro-dashboard"><div><span className="eyebrow eyebrow-accent">YOUR CREATIVE HQ</span><h1>Good ideas deserve<br /><em>the spotlight.</em></h1><p>Make scroll stopping shorts, review every frame, and publish on your terms.</p></div><div className="intro-date">{new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric' }).format(new Date())}</div></div>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      <section className="hero-panel" aria-label="Create a new video">
        <div className="hero-copy"><span className="hero-kicker"><WandSparkles size={15} /> FROM IDEA TO EVERY FEED</span><h2>Make something<br /><em>worth watching.</em></h2><p>Your story, shaped into a vertical video with voice, captions, and an effortless path to publishing.</p><div className="hero-actions"><Link className="button button-light" to="/create">Create a video <ArrowUpRight size={18} /></Link><Link className="hero-secondary" to="/projects">Explore projects <ArrowRight size={16} /></Link></div></div>
        <div className="hero-art" aria-hidden="true"><div className="hero-halo halo-one" /><div className="hero-halo halo-two" /><div className="hero-phone"><div className="hero-phone-screen">{featured?.thumbnail_url ? <img src={featured.thumbnail_url} alt="" /> : <><div className="hero-sun" /><div className="hero-horizon" /><div className="hero-phone-title">A little idea.<br />A big feeling.</div></>}<span className="hero-play"><Play size={19} fill="currentColor" /></span></div></div><div className="hero-float float-top"><span>✦</span> AI assisted creation</div><div className="hero-float float-bottom"><span className="live-dot" /> Ready for your next idea</div></div>
      </section>
      <section className="stats-grid" aria-label="Workspace stats">{stats.map(({ label, value, icon: Icon, note }) => <div className="stat-card" key={label}><span className="stat-icon"><Icon size={19} strokeWidth={1.7} /></span><div className="stat-value">{value}</div><div className="stat-label">{label}</div><div className="stat-note">{note}</div></div>)}</section>
      <section className="section-block"><div className="section-heading"><div><span className="eyebrow">THE WORK SO FAR</span><h2>Recent projects</h2></div><Link className="text-link" to="/projects">View all projects <ArrowUpRight size={17} /></Link></div>{recent.length ? <div className="project-grid">{recent.slice(0, 3).map((job) => <ProjectCard job={job} key={job.id} />)}</div> : <EmptyState />}</section>
      <section className="channel-ribbon"><div><span className="eyebrow">PUBLISHING, SIMPLIFIED</span><h2>One studio. Three channels.</h2><p>Choose your destinations after your video is ready. Every public post starts with your review.</p></div><div className="ribbon-channels">{(data?.accounts || [{ platform: 'youtube' }, { platform: 'facebook' }, { platform: 'instagram' }]).map((account) => <span key={account.platform} className="ribbon-channel"><span className={`platform-glyph glyph-${account.platform}`}>{account.platform === 'youtube' ? '▶' : account.platform === 'facebook' ? 'f' : '◎'}</span>{platformNames[account.platform]}</span>)}</div></section>
    </div>
  )
}
