import { useEffect, useState } from 'react'
import { ArrowUpRight, Check, CircleAlert, LockKeyhole, Radio, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'

const descriptions = {
  youtube: 'Upload vertical videos as Shorts. Visibility is private by default, with public and unlisted options when you create a project.',
  facebook: 'Publish public Reels to your configured Facebook Page after reviewing the final video.',
  instagram: 'Share public Reels on the professional account linked to your Facebook Page.',
}

export default function Channels() {
  const [health, setHealth] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { api.health().then(setHealth).catch((cause) => setError(cause.message)) }, [])
  const accounts = health?.accounts || []
  return (
    <div className="page-content channels-page"><div className="page-intro"><div><span className="eyebrow eyebrow-accent">YOUR DISTRIBUTION</span><h1>Ready for <em>every feed.</em></h1><p>Your publishing destinations, all in one private local workspace.</p></div><span className="intro-step">03 / PUBLISH</span></div>{error && <div className="alert alert-error">{error}</div>}<div className="channels-hero"><div><span className="eyebrow">ONE WORKFLOW, MORE REACH</span><h2>Create once.<br /><em>Share everywhere.</em></h2><p>Each channel keeps its own publishing state, so you can see exactly what went live.</p><Link to="/create" className="button button-light">Create a video <ArrowUpRight size={17} /></Link></div><div className="channels-orbit" aria-hidden="true"><span className="orbit-center"><Radio size={30} /></span><span className="orbit-bubble bubble-youtube">▶</span><span className="orbit-bubble bubble-facebook">f</span><span className="orbit-bubble bubble-instagram">◎</span></div></div><div className="section-heading channel-heading"><div><span className="eyebrow">PUBLISHING CHANNELS</span><h2>Your connections</h2></div><span className="connection-count">{accounts.filter((item) => item.configured).length} of 3 configured</span></div><div className="channel-cards">{accounts.map((account) => <div className="channel-card" key={account.platform}><div className="channel-card-top"><span className={`channel-icon glyph-${account.platform}`}>{account.platform === 'youtube' ? '▶' : account.platform === 'facebook' ? 'f' : '◎'}</span><span className={`connection-badge ${account.configured ? 'on' : 'off'}`}>{account.configured ? <Check size={14} /> : <CircleAlert size={14} />}{account.configured ? 'Configured' : 'Setup needed'}</span></div><h3>{account.label}</h3><p>{descriptions[account.platform]}</p><div className="channel-card-bottom"><span>{account.detail}</span><ArrowUpRight size={16} /></div></div>)}</div><div className="security-strip"><span className="security-icon"><LockKeyhole size={21} /></span><div><strong>Your credentials stay here.</strong><p>Keys and account tokens are read by the local Python backend. They are never sent to the browser.</p></div><ShieldCheck size={22} className="security-trailing" /></div></div>
  )
}
