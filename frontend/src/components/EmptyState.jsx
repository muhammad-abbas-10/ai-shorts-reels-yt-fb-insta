import { ArrowUpRight, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'

export default function EmptyState({ title = 'Your first project starts here.', body = 'Turn a simple idea into a short video with voice, captions, and a ready to publish export.' }) {
  return (
    <div className="empty-state">
      <div className="empty-visual"><span className="empty-orbit orbit-one" /><span className="empty-orbit orbit-two" /><Sparkles size={30} /></div>
      <div className="eyebrow">NOTHING HERE YET</div>
      <h3>{title}</h3>
      <p>{body}</p>
      <Link className="button button-primary" to="/create">Create a video <ArrowUpRight size={17} /></Link>
    </div>
  )
}
