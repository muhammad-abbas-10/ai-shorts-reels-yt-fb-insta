import { Clapperboard, LayoutDashboard, Plus, Radio, Video, ArrowUpRight } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import Brand from './Brand.jsx'

const links = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/create', label: 'Create video', icon: Plus },
  { to: '/projects', label: 'Projects', icon: Clapperboard },
  { to: '/channels', label: 'Channels', icon: Radio },
]

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar-top"><Brand /><span className="sidebar-edition">CREATOR WORKSPACE</span></div>
      <div className="sidebar-section-label">WORKSPACE</div>
      <nav className="sidebar-nav" aria-label="Main navigation">
        {links.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            <Icon size={19} strokeWidth={1.8} /><span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-spacer" />
      <div className="sidebar-feature">
        <div className="sidebar-feature-icon"><Video size={19} /></div>
        <strong>Made to move.</strong>
        <p>From one idea to a video on every channel.</p>
        <NavLink to="/create">Start creating <ArrowUpRight size={15} /></NavLink>
      </div>
      <div className="sidebar-footer"><span className="live-dot" /> Local workspace <span className="footer-version">v0.2</span></div>
    </aside>
  )
}
