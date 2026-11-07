import { useState } from 'react'
import { ArrowRight, ArrowUpRight, Check, ChevronDown, Film, ImagePlus, Info, Sparkles, WandSparkles, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api.js'
import StockFootageStatus from '../components/StockFootageStatus.jsx'

const channels = [
  { id: 'youtube', name: 'YouTube', sub: 'Shorts', symbol: '▶' },
  { id: 'facebook', name: 'Facebook', sub: 'Reels', symbol: 'f' },
  { id: 'instagram', name: 'Instagram', sub: 'Reels', symbol: '◎' },
]

export default function Create() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ topic: '', duration_seconds: 30, tone: 'educational', language: 'English', voice: 'en-US-AriaNeural', title: '', description: '', hashtags: '', script_text: '', source_notes: '', background_keywords: '', footage_start_seconds: 0, youtube_visibility: 'private', made_for_kids: false, contains_synthetic_media: false, new: false })
  const [platforms, setPlatforms] = useState(['youtube', 'facebook', 'instagram'])
  const [footage, setFootage] = useState(null)
  const [advanced, setAdvanced] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const toggle = (id) => setPlatforms((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  const maxDuration = platforms.includes('facebook') ? 60 : 180

  async function submit(event) {
    event.preventDefault()
    setError('')
    if (!form.topic.trim()) { setError('Give your video a topic to start.'); return }
    try {
      let footage_upload_id = null
      if (footage) {
        setBusy('Uploading your footage…')
        const uploaded = await api.uploadFootage(footage)
        footage_upload_id = uploaded.id
      }
      setBusy('Starting your video…')
      const result = await api.create({
        ...form,
        topic: form.topic.trim(),
        title: form.title.trim() || null,
        description: form.description.trim() || null,
        script_text: form.script_text.trim() || null,
        source_notes: form.source_notes.trim() || null,
        background_keywords: footage ? '' : form.background_keywords.trim(),
        hashtags: form.hashtags.split(',').map((tag) => tag.trim()).filter(Boolean).map((tag) => tag.startsWith('#') ? tag : `#${tag}`),
        duration_seconds: Number(form.duration_seconds),
        footage_start_seconds: Number(form.footage_start_seconds),
        footage_upload_id,
        platforms,
      })
      navigate(`/projects/${result.job.id}`)
    } catch (cause) {
      setError(cause.message)
      setBusy('')
    }
  }

  return (
    <div className="page-content create-page">
      <div className="page-intro"><div><span className="eyebrow eyebrow-accent">CREATE A NEW VIDEO</span><h1>What’s your <em>next story?</em></h1><p>Give us the spark. We’ll help shape it into a vertical video ready for the world.</p></div><span className="intro-step">01 / CREATE</span></div>
      <div className="create-layout">
        <form className="create-form" onSubmit={submit}>
          <div className="form-section"><div className="form-section-heading"><span className="section-number">01</span><div><h2>The idea</h2><p>What should this short be about?</p></div></div><label className="field"><span>Topic <strong>*</strong></span><input autoFocus type="text" maxLength="300" placeholder="e.g. Three habits that make mornings feel easier" value={form.topic} onChange={(event) => update('topic', event.target.value)} /></label><div className="field-grid"><label className="field"><span>Video length</span><div className="range-wrap"><input type="range" min="5" max={maxDuration} step="1" value={Math.min(form.duration_seconds, maxDuration)} onChange={(event) => update('duration_seconds', Number(event.target.value))} /><strong>{Math.min(form.duration_seconds, maxDuration)} sec</strong></div></label><label className="field"><span>Tone of voice</span><select value={form.tone} onChange={(event) => update('tone', event.target.value)}><option value="educational">Educational</option><option value="inspiring">Inspiring</option><option value="playful">Playful</option><option value="cinematic">Cinematic</option><option value="calm">Calm</option></select></label></div><div className="field-hint"><Info size={15} /> Facebook Reels are limited to 60 seconds in this studio.</div></div>
          <div className="form-section"><div className="form-section-heading"><span className="section-number">02</span><div><h2>Your content</h2><p>Optional details to make it yours.</p></div></div><label className="field"><span>Custom title <small>Optional</small></span><input type="text" maxLength="100" placeholder="We can write one for you" value={form.title} onChange={(event) => update('title', event.target.value)} /></label><label className="field"><span>Description <small>Optional</small></span><textarea rows="3" maxLength="2200" placeholder="Add a message for the caption, or let AI create it" value={form.description} onChange={(event) => update('description', event.target.value)} /></label><label className="field"><span>Hashtags <small>Optional</small></span><input type="text" placeholder="#mindful, #morning, #wellbeing" value={form.hashtags} onChange={(event) => update('hashtags', event.target.value)} /></label><label className="field"><span>Your narration script <small>Optional</small></span><textarea rows="5" maxLength="5000" placeholder="Write the exact words you want spoken. Leave blank to let AI write a script from your topic." value={form.script_text} onChange={(event) => update('script_text', event.target.value)} /></label></div>
          <div className="form-section"><div className="form-section-heading"><span className="section-number">03</span><div><h2>Visuals & destinations</h2><p>Choose the visuals for this video.</p></div></div><StockFootageStatus hasUpload={Boolean(footage)} /><label className="field"><span>Background keywords <small>Optional</small></span><textarea rows="2" maxLength="400" placeholder="e.g. chatbot screen, document search, data dashboard" value={form.background_keywords} onChange={(event) => update('background_keywords', event.target.value)} disabled={Boolean(footage)} /></label><p className="field-hint footage-note"><Info size={15} /> {footage ? 'Remove your uploaded clip to use background keywords.' : 'Separate up to four visual subjects with commas, in scene order. Unmatched scenes get topic visuals.'}</p><label className={`upload-zone ${footage ? 'has-file' : ''}`}><input type="file" accept="video/mp4,video/quicktime,video/x-matroska,video/webm,.mp4,.mov,.mkv,.webm" onChange={(event) => setFootage(event.target.files?.[0] || null)} /><span className="upload-icon"><ImagePlus size={22} /></span><span><strong>{footage ? footage.name : 'Upload your own clip instead'}</strong><small>{footage ? 'This clip will be used as the background' : 'MP4, MOV, MKV, or WebM · optional'}</small></span>{footage && <button type="button" className="icon-button" title="Remove footage" onClick={(event) => { event.preventDefault(); setFootage(null); event.currentTarget.parentElement.querySelector('input').value = '' }}><X size={16} /></button>}</label><p className="field-hint footage-note"><Info size={15} /> Uploaded clips use generated narration instead of their original audio.</p><div className="field"><span>Publishing destinations</span><div className="platform-selector">{channels.map((channel) => <button type="button" key={channel.id} className={`platform-select ${platforms.includes(channel.id) ? 'selected' : ''}`} onClick={() => { toggle(channel.id); if (channel.id === 'facebook' && !platforms.includes('facebook') && form.duration_seconds > 60) update('duration_seconds', 60) }}><span className={`platform-glyph glyph-${channel.id}`}>{channel.symbol}</span><span><strong>{channel.name}</strong><small>{channel.sub}</small></span><span className="platform-check">{platforms.includes(channel.id) && <Check size={13} />}</span></button>)}</div></div>{platforms.includes('youtube') && <label className="field visibility-field"><span>YouTube visibility</span><select value={form.youtube_visibility} onChange={(event) => update('youtube_visibility', event.target.value)}><option value="private">Private — only you can see it</option><option value="unlisted">Unlisted — anyone with the link</option><option value="public">Public — visible to everyone</option></select></label>}<div className="publish-note"><Sparkles size={17} /><span>Videos are generated for review first. Facebook and Instagram posts are public only when you choose to publish.</span></div></div>
          <div className="advanced"><button className="advanced-toggle" type="button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><span>More creative controls</span><ChevronDown size={18} className={advanced ? 'rotate' : ''} /></button>{advanced && <div className="advanced-body"><label className="field"><span>Factual notes for AI <small>Optional</small></span><textarea rows="4" maxLength="12000" value={form.source_notes} onChange={(event) => update('source_notes', event.target.value)} placeholder="Add verified facts or source notes for the script." /></label><div className="field-grid"><label className="field"><span>Language</span><input value={form.language} onChange={(event) => update('language', event.target.value)} /></label><label className="field"><span>Edge voice</span><input value={form.voice} onChange={(event) => update('voice', event.target.value)} /></label></div>{footage && <label className="field"><span>Start footage at (seconds)</span><input type="number" min="0" max="7200" step="0.1" value={form.footage_start_seconds} onChange={(event) => update('footage_start_seconds', event.target.value)} /></label>}<label className="check-row"><input type="checkbox" checked={form.made_for_kids} onChange={(event) => update('made_for_kids', event.target.checked)} /> This YouTube video is made for kids</label><label className="check-row"><input type="checkbox" checked={form.contains_synthetic_media} onChange={(event) => update('contains_synthetic_media', event.target.checked)} /> Disclose realistic altered or synthetic media</label><label className="check-row"><input type="checkbox" checked={form.new} onChange={(event) => update('new', event.target.checked)} /> Create a fresh job even if these inputs match an earlier one</label></div>}</div>
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          <div className="form-footer"><span><Info size={16} /> You’ll review the final video before publishing.</span><button className="button button-primary button-big" type="submit" disabled={Boolean(busy)}>{busy || 'Generate video'} {busy ? <span className="button-spinner" /> : <ArrowUpRight size={19} />}</button></div>
        </form>
        <aside className="create-aside"><div className="aside-card"><div className="aside-heading"><span className="eyebrow">YOUR VIDEO, VISUALIZED</span><Film size={17} /></div><div className="concept-phone"><div className="concept-screen"><span className="concept-top">AI SHORTS STUDIO <span>● REC</span></span><div className="concept-glow" /><div className="concept-caption">{form.topic.trim() || 'Your big idea starts here.'}</div><span className="concept-bottom">VERTICAL STORY · 9:16</span></div></div><div className="aside-details"><div><span>FORMAT</span><strong>Vertical video · 9:16</strong></div><div><span>LENGTH</span><strong>{Math.min(form.duration_seconds, maxDuration)} seconds</strong></div><div><span>CHANNELS</span><strong>{platforms.length || 'None selected'}</strong></div></div></div><div className="aside-tip"><WandSparkles size={19} /><div><strong>Made for your workflow</strong><p>Write your own script or start with a topic. You’ll always get the final say before it goes live.</p></div></div><div className="aside-steps"><span>IDEA</span><ArrowRight size={14} /><span>CREATE</span><ArrowRight size={14} /><span>REVIEW</span><ArrowRight size={14} /><span>PUBLISH</span></div></aside>
      </div>
    </div>
  )
}
