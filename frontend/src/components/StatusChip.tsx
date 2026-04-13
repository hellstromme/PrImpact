import type { Severity } from '../lib/types'

interface VerdictChipProps {
  verdict: 'clean' | 'has_blockers' | null
}

interface SeverityChipProps {
  severity: Severity
  label?: string
}

// Rectangular chips — no pill shapes per Hacker Sleek constraint
const chipBase = 'inline-flex items-center px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-[2px]'

export function VerdictChip({ verdict }: VerdictChipProps) {
  if (verdict === 'clean') {
    return (
      <span className={`${chipBase} bg-primary-container/20 text-primary border border-primary/20`}>
        CLEAN
      </span>
    )
  }
  if (verdict === 'has_blockers') {
    return (
      <span className={`${chipBase} bg-tertiary-container/20 text-tertiary border border-tertiary/30`}>
        BLOCKER
      </span>
    )
  }
  return (
    <span className={`${chipBase} bg-surface-container-high text-on-surface-variant`}>
      PENDING
    </span>
  )
}

export function SeverityChip({ severity, label }: SeverityChipProps) {
  const display = label ?? severity.toUpperCase()
  if (severity === 'high') {
    return (
      <span className={`${chipBase} bg-tertiary-container/20 text-tertiary border border-tertiary/30`}>
        {display}
      </span>
    )
  }
  if (severity === 'medium') {
    return (
      <span className={`${chipBase} bg-secondary-container/20 text-secondary`}>
        {display}
      </span>
    )
  }
  return (
    <span className={`${chipBase} bg-primary-container/20 text-primary`}>
      {display}
    </span>
  )
}

export function DistanceChip({ distance }: { distance: number }) {
  const colors = [
    'bg-primary-container/20 text-primary',
    'bg-secondary-container/20 text-secondary',
    'bg-tertiary-container/20 text-tertiary border border-tertiary/30',
  ]
  const cls = colors[Math.min(distance - 1, 2)]
  return (
    <span className={`${chipBase} ${cls}`}>
      D{distance}
    </span>
  )
}
