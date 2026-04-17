import { useState } from 'react'
import type { ImpactReport, Severity } from '../../lib/types'
import { SeverityChip } from '../../components/StatusChip'

export default function AnomaliesTab({ report }: { report: ImpactReport }) {
  const anomalies = report.ai_analysis.anomalies
  const [filter, setFilter] = useState<'all' | Severity>('all')

  const filtered = filter === 'all' ? anomalies : anomalies.filter((a) => a.severity === filter)

  const highCount = anomalies.filter((a) => a.severity === 'high').length
  const medCount = anomalies.filter((a) => a.severity === 'medium').length
  const lowCount = anomalies.filter((a) => a.severity === 'low').length

  const maxSev: Severity = highCount > 0 ? 'high' : medCount > 0 ? 'medium' : 'low'

  if (anomalies.length === 0) {
    return (
      <div className="p-8 text-center text-on-surface-variant">
        <span className="material-symbols-outlined text-[48px] mb-4 block text-primary">
          check_circle
        </span>
        <p className="font-mono text-sm">No anomalies detected.</p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-5xl space-y-8">
      {/* Header stats */}
      <div className="flex items-center gap-6">
        <div>
          <span className="font-headline text-4xl font-bold text-on-surface">{anomalies.length}</span>
          <span className="text-on-surface-variant text-sm ml-2">anomalies</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono text-on-surface-variant uppercase tracking-widest">
            Highest severity
          </span>
          <SeverityChip severity={maxSev} />
        </div>
        <div className="flex items-center gap-3 ml-auto text-xs font-mono text-on-surface-variant">
          {highCount > 0 && <span className="text-red-400">{highCount} high</span>}
          {medCount > 0 && <span className="text-yellow-400">{medCount} medium</span>}
          {lowCount > 0 && <span className="text-blue-400">{lowCount} low</span>}
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 border-b border-outline-variant/10">
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
            {f === 'all' ? `All (${anomalies.length})` : `${f} (${anomalies.filter((a) => a.severity === f).length})`}
          </button>
        ))}
      </div>

      {/* Anomaly cards */}
      {filtered.length === 0 ? (
        <p className="text-on-surface-variant text-sm font-mono">No anomalies at this severity level.</p>
      ) : (
        <div className="space-y-3">
          {filtered.map((anomaly, i) => (
            <div
              key={i}
              className="bg-surface-container-low rounded-lg border border-outline-variant/10 p-4"
            >
              <div className="flex items-start gap-3">
                <div className="shrink-0 mt-0.5">
                  <SeverityChip severity={anomaly.severity} />
                </div>
                <div className="min-w-0">
                  <p className="text-sm text-on-surface mb-1">{anomaly.description}</p>
                  {anomaly.location && (
                    <p className="font-mono text-xs text-on-surface-variant">{anomaly.location}</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
