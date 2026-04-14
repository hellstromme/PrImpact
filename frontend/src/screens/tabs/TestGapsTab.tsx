import { useState } from 'react'
import type { ImpactReport, TestGap, Severity } from '../../lib/types'
import { SeverityChip } from '../../components/StatusChip'

function GapCard({
  gap,
  addressed,
  onToggle,
}: {
  gap: TestGap
  addressed: boolean
  onToggle: () => void
}) {
  return (
    <div
      className={[
        'bg-surface-container-low p-4 rounded-lg border border-outline-variant/10 transition-opacity',
        addressed ? 'opacity-50' : '',
      ].join(' ')}
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <SeverityChip severity={gap.severity} />
          {gap.gap_type && (
            <span className="text-[10px] font-mono text-on-surface-variant uppercase tracking-widest">
              {gap.gap_type}
            </span>
          )}
        </div>
        <button
          onClick={onToggle}
          className={[
            'text-[10px] font-mono px-2 py-0.5 rounded border transition-colors',
            addressed
              ? 'border-primary/30 text-primary bg-primary-container/10'
              : 'border-outline-variant/20 text-on-surface-variant hover:border-primary/30',
          ].join(' ')}
          title={addressed ? 'Mark as unresolved' : 'Mark as addressed'}
        >
          {addressed ? '✓ Addressed' : 'Mark addressed'}
        </button>
      </div>
      <p className="text-sm text-on-surface mb-2">{gap.behaviour}</p>
      <p className="font-mono text-xs text-on-surface-variant">{gap.location}</p>
    </div>
  )
}

export default function TestGapsTab({ report }: { report: ImpactReport }) {
  const gaps = report.ai_analysis.test_gaps
  const [filter, setFilter] = useState<'all' | Severity>('all')
  const [addressed, setAddressed] = useState<Set<number>>(new Set())

  const filtered =
    filter === 'all' ? gaps : gaps.filter((g) => g.severity === filter)

  const maxSev: Severity =
    gaps.some((g) => g.severity === 'high')
      ? 'high'
      : gaps.some((g) => g.severity === 'medium')
      ? 'medium'
      : 'low'

  function toggleAddressed(i: number) {
    setAddressed((prev) => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }

  if (gaps.length === 0) {
    return (
      <div className="p-8 text-center text-on-surface-variant">
        <span className="material-symbols-outlined text-[48px] mb-4 block text-primary">
          check_circle
        </span>
        <p className="font-mono text-sm">No test gaps identified.</p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-5xl space-y-8">
      {/* Header stats */}
      <div className="flex items-center gap-6">
        <div>
          <span className="font-headline text-4xl font-bold text-on-surface">{gaps.length}</span>
          <span className="text-on-surface-variant text-sm ml-2">coverage gaps</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono text-on-surface-variant uppercase tracking-widest">
            Risk Factor
          </span>
          <SeverityChip severity={maxSev} />
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 border-b border-outline-variant/10 pb-0">
        {(['all', 'high', 'medium', 'low'] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={[
              'px-4 py-2 text-xs font-mono uppercase tracking-widest border-b-2 transition-colors',
              filter === f
                ? 'border-primary text-primary'
                : 'border-transparent text-on-surface-variant hover:text-on-surface',
            ].join(' ')}
          >
            {f === 'all' ? 'All' : f}
          </button>
        ))}
      </div>

      {/* Gap cards */}
      {filtered.length === 0 ? (
        <p className="text-on-surface-variant text-sm font-mono">
          No gaps at this severity level.
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered.map((gap, i) => {
            const globalIdx = gaps.indexOf(gap)
            return (
              <GapCard
                key={i}
                gap={gap}
                addressed={addressed.has(globalIdx)}
                onToggle={() => toggleAddressed(globalIdx)}
              />
            )
          })}
        </div>
      )}

      {/* Footer stats */}
      <div className="flex gap-6 pt-4 border-t border-outline-variant/10 text-xs font-mono text-on-surface-variant">
        <span>Total: {gaps.length}</span>
        <span className="text-primary">Resolved: {addressed.size}</span>
        <span>New: 0</span>
      </div>
    </div>
  )
}
