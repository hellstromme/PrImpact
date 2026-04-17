import type { ImpactReport } from '../../lib/types'
import { SeverityChip, DistanceChip } from '../../components/StatusChip'
import { shortPath } from '../../lib/formatters'
import SparkLine from '../../components/SparkLine'

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="font-headline text-lg font-semibold tracking-tight mb-4 text-on-surface border-b border-outline-variant/10 pb-2">
      {children}
    </h2>
  )
}

function SignalCountBadge({
  count,
  severity,
}: {
  count: number
  severity: 'high' | 'medium' | 'low'
}) {
  if (count === 0) return null
  return <SeverityChip severity={severity} label={`${count} ${severity}`} />
}

export default function SummaryTab({
  report,
  onNavigate,
}: {
  report: ImpactReport
  onNavigate?: (tab: string) => void
}) {
  const { ai_analysis: ai, blast_radius, interface_changes, dependency_issues, verdict } = report

  // Signal counts by severity
  const allSignals = [...ai.security_signals, ...dependency_issues]
  const highCount = allSignals.filter((s) => s.severity === 'high').length
  const medCount = allSignals.filter((s) => s.severity === 'medium').length
  const lowCount = allSignals.filter((s) => s.severity === 'low').length
  const anomalyCount = ai.anomalies.length

  return (
    <div className="p-8 max-w-5xl space-y-10">
      {/* Executive Summary */}
      <section>
        <SectionHeading>Executive Summary</SectionHeading>
        <p className="text-on-surface leading-relaxed">{ai.summary || '—'}</p>
      </section>

      {/* Agent Verdict */}
      {verdict && (
        <section>
          <SectionHeading>Agent Verdict</SectionHeading>
          <div
            className={`rounded-lg border p-4 mb-4 ${
              verdict.status === 'clean'
                ? 'border-green-500/30 bg-green-500/5'
                : 'border-red-500/30 bg-red-500/5'
            }`}
          >
            <div className="flex items-center gap-3 mb-2">
              <span
                className={`text-xs font-mono font-bold uppercase tracking-widest px-2 py-0.5 rounded ${
                  verdict.status === 'clean'
                    ? 'bg-green-500/20 text-green-400'
                    : 'bg-red-500/20 text-red-400'
                }`}
              >
                {verdict.status === 'clean' ? 'Clean' : 'Blockers found'}
              </span>
            </div>
            {verdict.rationale && (
              <p className="text-on-surface-variant text-sm leading-relaxed">{verdict.rationale}</p>
            )}
          </div>
          {verdict.blockers.length > 0 && (
            <div className="space-y-2">
              {verdict.blockers.map((b, i) => {
                const targetTab =
                  b.category === 'anomaly' ? 'anomalies'
                  : b.category === 'security_signal' ? 'security'
                  : b.category === 'dependency_issue' ? 'dependencies'
                  : b.category === 'test_gap' ? 'test-gaps'
                  : null
                const canNavigate = targetTab && onNavigate
                return (
                  <div
                    key={i}
                    onClick={canNavigate ? () => onNavigate!(targetTab!) : undefined}
                    className={`rounded border border-red-500/20 bg-surface-container-low px-4 py-3 ${canNavigate ? 'cursor-pointer hover:border-red-500/40 hover:bg-surface-container transition-colors' : ''}`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] font-mono uppercase tracking-widest text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">
                        {b.category.replace(/_/g, ' ')}
                      </span>
                      <span className="text-xs text-on-surface">{b.description}</span>
                      {canNavigate && (
                        <span className="ml-auto text-[10px] font-mono text-on-surface-variant shrink-0">
                          View →
                        </span>
                      )}
                    </div>
                    {b.location && (
                      <p className="font-mono text-xs text-on-surface-variant">{b.location}</p>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </section>
      )}

      {/* Anomaly count — links to Anomalies tab */}
      {anomalyCount > 0 && (
        <section>
          <SectionHeading>Anomalies</SectionHeading>
          <button
            onClick={() => onNavigate?.('anomalies')}
            className="flex items-center gap-3 rounded-lg border border-outline-variant/10 bg-surface-container-low px-5 py-3 hover:border-primary/30 hover:bg-surface-container transition-colors"
          >
            <span className="font-headline text-3xl font-bold text-primary">{anomalyCount}</span>
            <span className="text-on-surface-variant text-sm">
              {anomalyCount === 1 ? 'anomaly detected' : 'anomalies detected'}
            </span>
            <span className="ml-auto text-xs font-mono text-on-surface-variant">View all →</span>
          </button>
        </section>
      )}

      {/* Signal pills row */}
      {(highCount + medCount + lowCount) > 0 && (
        <section>
          <SectionHeading>Security Signals</SectionHeading>
          <div className="flex flex-wrap gap-2">
            <SignalCountBadge count={highCount} severity="high" />
            <SignalCountBadge count={medCount} severity="medium" />
            <SignalCountBadge count={lowCount} severity="low" />
          </div>
        </section>
      )}

      {/* Blast Radius summary */}
      {blast_radius.length > 0 && (
        <section>
          <SectionHeading>Blast Radius</SectionHeading>
          <div className="flex gap-6 mb-4">
            <div>
              <span className="font-headline text-3xl font-bold text-primary">
                {blast_radius.length}
              </span>
              <span className="text-on-surface-variant text-sm ml-2">files impacted</span>
            </div>
          </div>
          {(() => {
            const maxChurn = blast_radius.reduce((m, e) => Math.max(m, e.churn_score ?? 0), 1)
            return (
              <div className="overflow-x-auto rounded-lg border border-outline-variant/10">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-surface-container-low border-b border-outline-variant/10">
                      <th className="text-left px-4 py-2 text-[10px] font-mono uppercase tracking-widest text-on-surface-variant">
                        File
                      </th>
                      <th className="text-left px-4 py-2 text-[10px] font-mono uppercase tracking-widest text-on-surface-variant">
                        Distance
                      </th>
                      <th className="text-left px-4 py-2 text-[10px] font-mono uppercase tracking-widest text-on-surface-variant">
                        Symbols
                      </th>
                      <th className="text-left px-4 py-2 text-[10px] font-mono uppercase tracking-widest text-on-surface-variant">
                        Churn
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {blast_radius.slice(0, 5).map((entry) => (
                      <tr
                        key={entry.path}
                        className="border-b border-outline-variant/10 hover:bg-surface-container-high/50"
                      >
                        <td className="px-4 py-2 font-mono text-xs text-on-surface">
                          {shortPath(entry.path)}
                        </td>
                        <td className="px-4 py-2">
                          <DistanceChip distance={entry.distance} />
                        </td>
                        <td className="px-4 py-2 text-xs text-on-surface-variant">
                          {entry.imported_symbols.length}
                        </td>
                        <td className="px-4 py-2">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs text-on-surface-variant w-6 text-right">
                              {entry.churn_score != null ? Math.round(entry.churn_score) : '—'}
                            </span>
                            <SparkLine values={[entry.churn_score ?? 0]} max={maxChurn} width={48} height={14} />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          })()}
          {blast_radius.length > 5 && (
            <p className="text-xs text-on-surface-variant mt-2 font-mono">
              +{blast_radius.length - 5} more files — see Blast Radius tab
            </p>
          )}
        </section>
      )}

      {/* Interface Changes */}
      {interface_changes.length > 0 && (
        <section>
          <SectionHeading>Interface Changes</SectionHeading>
          <div className="space-y-4">
            {interface_changes.slice(0, 3).map((ic, i) => (
              <div
                key={i}
                className="rounded-lg border border-outline-variant/10 overflow-hidden"
              >
                <div className="flex items-center gap-3 px-4 py-2 bg-surface-container-low border-b border-outline-variant/10">
                  <span className="font-mono text-xs text-primary">{ic.symbol}</span>
                  <span className="text-on-surface-variant text-xs font-mono">in {shortPath(ic.file)}</span>
                  {ic.callers.length > 0 && (
                    <span className="ml-auto text-[10px] font-mono text-tertiary">
                      {ic.callers.length} callers
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-2 divide-x divide-outline-variant/10">
                  <div className="p-3">
                    <div className="text-[10px] font-mono text-on-surface-variant uppercase mb-2">Before</div>
                    <pre className="font-mono text-xs text-tertiary/80 whitespace-pre-wrap">{ic.before}</pre>
                  </div>
                  <div className="p-3">
                    <div className="text-[10px] font-mono text-on-surface-variant uppercase mb-2">After</div>
                    <pre className="font-mono text-xs text-primary whitespace-pre-wrap">{ic.after}</pre>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Decisions */}
      {ai.decisions.length > 0 && (
        <section>
          <SectionHeading>Decisions</SectionHeading>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {ai.decisions.map((d, i) => (
              <div
                key={i}
                className="bg-surface-container-low p-4 rounded-lg border border-outline-variant/10"
              >
                <p className="text-on-surface text-sm font-medium mb-2">{d.description}</p>
                <p className="text-on-surface-variant text-xs mb-2">{d.rationale}</p>
                {d.risk && (
                  <p className="text-secondary text-xs font-mono">⚠ {d.risk}</p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Assumptions */}
      {ai.assumptions.length > 0 && (
        <section>
          <SectionHeading>Assumptions</SectionHeading>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {ai.assumptions.map((a, i) => (
              <div
                key={i}
                className="bg-surface-container-low p-4 rounded-lg border border-outline-variant/10"
              >
                <p className="text-on-surface text-sm font-medium mb-1">{a.description}</p>
                <p className="font-mono text-xs text-on-surface-variant mb-2">{a.location}</p>
                {a.risk && (
                  <p className="text-tertiary text-xs">Risk: {a.risk}</p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
