/** Format an ISO 8601 date as a human-readable relative time string. */
export function relativeTime(iso: string): string {
  const now = Date.now()
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'unknown time'
  const diffMs = now - then
  if (diffMs < 0) return 'in the future'
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHr = Math.floor(diffMin / 60)
  const diffDays = Math.floor(diffHr / 24)

  if (diffSec < 60) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  if (diffHr < 24) return `${diffHr}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return new Date(iso).toLocaleDateString()
}

/** Truncate a string to maxLen characters, appending ellipsis if needed. */
export function truncate(s: string, maxLen: number): string {
  if (maxLen <= 0) return ''
  if (s.length <= maxLen) return s
  return s.slice(0, maxLen - 1) + '…'
}

/** Shorten a file path by showing only the last N segments. */
export function shortPath(path: string, segments = 3): string {
  const parts = path.replace(/\\/g, '/').split('/')
  if (parts.length <= segments) return path
  return '…/' + parts.slice(-segments).join('/')
}

/** Return a colour class name for a severity string. */
export function severityColor(severity: string): string {
  switch (severity) {
    case 'high':
      return 'text-tertiary-container'
    case 'medium':
      return 'text-secondary'
    case 'low':
      return 'text-primary'
    default:
      return 'text-on-surface-variant'
  }
}

/** Return background colour class for a severity chip. */
export function severityBg(severity: string): string {
  switch (severity) {
    case 'high':
      return 'bg-tertiary-container/20 text-tertiary border border-tertiary/30'
    case 'medium':
      return 'bg-secondary-container/20 text-secondary'
    case 'low':
      return 'bg-primary-container/20 text-primary'
    default:
      return 'bg-surface-container-high text-on-surface-variant'
  }
}
