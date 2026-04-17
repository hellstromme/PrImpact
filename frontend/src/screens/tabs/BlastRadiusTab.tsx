import type { ImpactReport } from '../../lib/types'
import { DistanceChip } from '../../components/StatusChip'
import SparkLine from '../../components/SparkLine'
import { shortPath } from '../../lib/formatters'

export default function BlastRadiusTab({ report }: { report: ImpactReport }) {
  const { blast_radius, interface_changes } = report

  const maxPropagation = blast_radius.reduce((m, e) => Math.max(m, e.distance), 0)
  const maxChurn = blast_radius.reduce((m, e) => Math.max(m, e.churn_score ?? 0), 1)

  return (
    <div className="p-8 max-w-5xl space-y-8">
      {/* Metrics row */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Impacted Files', value: blast_radius.length },
          { label: 'Max Propagation', value: `${maxPropagation} hops` },
          { label: 'Interface Changes', value: interface_changes.length },
        ].map((m) => (
          <div
            key={m.label}
            className="bg-surface-container-low p-4 rounded-lg border border-outline-variant/10"
          >
            <div className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant mb-2">
              {m.label}
            </div>
            <div className="font-headline text-3xl font-bold text-on-surface">{m.value}</div>
          </div>
        ))}
      </div>

      {/* Interface breaking change alert */}
      {interface_changes.length > 0 && (
        <div className="flex gap-3 p-4 bg-tertiary-container/10 border border-tertiary/20 rounded-lg">
          <span className="material-symbols-outlined text-tertiary text-[20px] shrink-0 mt-0.5">
            warning
          </span>
          <div>
            <p className="text-tertiary text-sm font-bold mb-1">Interface Breaking Change</p>
            <p className="text-on-surface-variant text-xs">
              {interface_changes.length} public symbol
              {interface_changes.length !== 1 ? 's' : ''} changed signature.
              {interface_changes.map((ic) => ` ${ic.symbol}`).join(',')}
            </p>
          </div>
        </div>
      )}

      {/* File Impact Profile table */}
      <section>
        <h2 className="font-headline text-lg font-semibold mb-4 border-b border-outline-variant/10 pb-2">
          File Impact Profile
        </h2>
        {blast_radius.length === 0 ? (
          <p className="text-on-surface-variant text-sm font-mono">
            No files in blast radius.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-outline-variant/10">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-surface-container-low border-b border-outline-variant/10">
                  {['File', 'Distance', 'Symbols Used', 'Churn'].map((h) => (
                    <th
                      key={h}
                      className="text-left px-4 py-2 text-[10px] font-mono uppercase tracking-widest text-on-surface-variant"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {blast_radius.map((entry) => (
                  <tr
                    key={entry.path}
                    className="border-b border-outline-variant/10 hover:bg-surface-container-high/50 transition-colors"
                  >
                    <td className="px-4 py-2.5 font-mono text-xs text-on-surface max-w-[280px] truncate">
                      {shortPath(entry.path)}
                    </td>
                    <td className="px-4 py-2.5">
                      <DistanceChip distance={entry.distance} />
                    </td>
                    <td className="px-4 py-2.5 text-xs text-on-surface-variant">
                      {entry.imported_symbols.length > 0
                        ? entry.imported_symbols.slice(0, 3).join(', ') +
                          (entry.imported_symbols.length > 3
                            ? ` +${entry.imported_symbols.length - 3}`
                            : '')
                        : '—'}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-on-surface-variant w-6 text-right">
                          {entry.churn_score != null ? Math.round(entry.churn_score) : '—'}
                        </span>
                        <SparkLine
                          values={[entry.churn_score ?? 0]}
                          max={maxChurn}
                          width={48}
                          height={16}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
