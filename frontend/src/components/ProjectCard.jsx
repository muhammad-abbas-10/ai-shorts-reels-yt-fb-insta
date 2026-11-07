import { ArrowUpRight, Clock3, Layers3 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { dateLabel, durationLabel, platformNames } from '../lib/format.js'
import StatusBadge from './StatusBadge.jsx'

export default function ProjectCard({ job, variant = 'grid' }) {
  return (
    <Link className={`project-card project-card-${variant}`} to={`/projects/${job.id}`}>
      <div className="project-thumb">
        {job.thumbnail_url ? <img src={job.thumbnail_url} alt="" loading="lazy" /> : <div className="thumb-placeholder"><span>SHORT<br />FORM</span><i /></div>}
        <span className="thumb-open"><ArrowUpRight size={18} /></span>
      </div>
      <div className="project-content">
        <div className="project-topline"><span className="eyebrow">VIDEO PROJECT</span><StatusBadge job={job} /></div>
        <h3>{job.metadata?.title || job.topic}</h3>
        <p>{job.topic}</p>
        <div className="project-meta"><span><Clock3 size={14} />{durationLabel(job)}</span><span><Layers3 size={14} />{job.publications?.length ? job.publications.map((p) => platformNames[p.platform]).join(' · ') : 'Draft'}</span></div>
        <div className="project-bottom"><span>{dateLabel(job.created_at)}</span><span>View project <ArrowUpRight size={15} /></span></div>
      </div>
    </Link>
  )
}
