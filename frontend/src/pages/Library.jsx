import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, RefreshCw, Search, SlidersHorizontal } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api.js'
import { jobState } from '../lib/format.js'
import ProjectCard from '../components/ProjectCard.jsx'
import EmptyState from '../components/EmptyState.jsx'

const filters = [
  { key: 'all', label: 'All projects' },
  { key: 'success', label: 'Ready & published' },
  { key: 'info', label: 'In progress' },
  { key: 'danger', label: 'Needs attention' },
]

export default function Library() {
  const [jobs, setJobs] = useState(null)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  async function load() {
    try {
      setRefreshing(true)
      const result = await api.jobs(100)
      setJobs(result.jobs)
      setError('')
    } catch (cause) { setError(cause.message) }
    finally { setRefreshing(false) }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 12000)
    return () => clearInterval(interval)
  }, [])

  const visible = useMemo(() => (jobs || []).filter((job) => {
    const matchQuery = `${job.topic} ${job.metadata?.title || ''}`.toLowerCase().includes(query.toLowerCase())
    const tone = jobState(job).tone
    return matchQuery && (filter === 'all' || tone === filter || (filter === 'success' && tone === 'warning'))
  }), [jobs, query, filter])

  return (
    <div className="page-content library-page">
      <div className="page-intro"><div><span className="eyebrow eyebrow-accent">YOUR CREATIVE LIBRARY</span><h1>Every story, <em>in one place.</em></h1><p>Find, preview, and publish the videos you’ve made.</p></div><Link className="button button-primary" to="/create">Create new <ArrowUpRight size={17} /></Link></div>
      <div className="library-toolbar"><div className="search-box"><Search size={18} /><input aria-label="Search projects" placeholder="Search by topic or title…" value={query} onChange={(event) => setQuery(event.target.value)} /></div><button className="icon-button refresh-button" type="button" title="Refresh projects" onClick={load} disabled={refreshing}><RefreshCw size={18} className={refreshing ? 'spin-once' : ''} /></button></div>
      <div className="filter-row"><span className="filter-heading"><SlidersHorizontal size={16} /> FILTER</span>{filters.map((item) => <button type="button" key={item.key} className={`filter-pill ${filter === item.key ? 'active' : ''}`} onClick={() => setFilter(item.key)}>{item.label}</button>)}<span className="result-count">{visible.length} {visible.length === 1 ? 'project' : 'projects'}</span></div>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      {jobs === null ? <div className="loading-cards"><div /><div /><div /></div> : visible.length ? <div className="project-grid library-grid">{visible.map((job) => <ProjectCard job={job} key={job.id} />)}</div> : jobs.length ? <div className="no-results"><Search size={29} /><h3>No matching projects</h3><p>Try a different search or filter.</p><button type="button" className="text-link" onClick={() => { setQuery(''); setFilter('all') }}>Clear filters <ArrowUpRight size={16} /></button></div> : <EmptyState />}
    </div>
  )
}
