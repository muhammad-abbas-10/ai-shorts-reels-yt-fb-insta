import { ArrowUpRight, Plus } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'

const titles = {
  '/': 'Overview',
  '/create': 'Create video',
  '/projects': 'Projects',
  '/channels': 'Channels',
}

export default function Topbar() {
  const { pathname } = useLocation()
  const title = pathname.startsWith('/projects/') ? 'Project details' : (titles[pathname] || 'Workspace')
  return (
    <header className="topbar">
      <div className="breadcrumbs"><span>Workspace</span><span className="crumb-divider">/</span><strong>{title}</strong></div>
      <div className="topbar-actions">
        <span className="topbar-local"><span className="live-dot" /> Running locally</span>
        <Link className="topbar-create" to="/create"><Plus size={17} /> New video <ArrowUpRight size={14} /></Link>
        <div className="avatar" title="AI Shorts Studio">AS</div>
      </div>
    </header>
  )
}
