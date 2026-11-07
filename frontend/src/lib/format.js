export const platformNames = {
  youtube: 'YouTube',
  facebook: 'Facebook',
  instagram: 'Instagram',
}

export function dateLabel(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(date)
}

export function timeLabel(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date)
}

export function durationLabel(job) {
  const seconds = job?.metadata?.validation?.duration_seconds ?? job?.config?.duration_seconds
  return seconds ? `${Math.round(seconds)} sec` : '—'
}

export function jobState(job) {
  if (!job) return { label: 'Loading', tone: 'neutral' }
  const publications = job.publications || []
  if (job.status === 'failed') return { label: 'Needs attention', tone: 'danger' }
  if (job.status === 'partially_completed') return { label: 'Partially published', tone: 'warning' }
  if (job.status === 'publishing' || publications.some((p) => p.status === 'processing' || p.status === 'attempting')) {
    return { label: 'Publishing', tone: 'info' }
  }
  if (job.has_video) {
    if (publications.some((p) => p.status === 'succeeded')) return { label: 'Published', tone: 'success' }
    return { label: 'Ready to publish', tone: 'success' }
  }
  if (job.status === 'cancelled') return { label: 'Cancelled', tone: 'neutral' }
  return { label: 'Creating', tone: 'info' }
}

export function stageLabel(status) {
  return ({
    queued: 'Queued',
    generating_script: 'Writing script',
    generating_audio: 'Creating voiceover',
    generating_captions: 'Timing captions',
    retrieving_footage: 'Finding visuals',
    rendering: 'Rendering video',
    validating: 'Checking export',
    ready_to_publish: 'Ready for review',
    publishing: 'Publishing',
    completed: 'Complete',
    partially_completed: 'Partially complete',
    failed: 'Needs attention',
    cancelled: 'Cancelled',
  })[status] || status
}
