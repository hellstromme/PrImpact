import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { ImpactReport, SecuritySignal, DependencyIssue, Severity } from '../../lib/types'
import { SeverityChip } from '../../components/StatusChip'
import CodeBlock from '../../components/CodeBlock'
import { api } from '../../lib/api'
import { queryKeys } from '../../lib/queryKeys'

type SignalItem =
  | { kind: 'signal'; data: SecuritySignal }
  | { kind: 'dep'; data: DependencyIssue }

function allSignals(report: ImpactReport): SignalItem[] {
  return [
    ...report.ai_analysis.security_signals.map((s) => ({ kind: 'signal' as const, data: s })),
    ...report.dependency_issues.map((d) => ({ kind: 'dep' as const, data: d })),
  ]
}

function getLabel(item: SignalItem): string {
  return item.kind === 'signal' ? item.data.description : item.data.package_name
}

function getSeverity(item: SignalItem): Severity {
  return item.data.severity
}

function SignalListItem({
  item,
  selected,
  onClick,
}: {
  item: SignalItem
  selected: boolean
  onClick: () => void
}) {
  const label = getLabel(item)
  const severity = getSeverity(item)
  const sub =
    item.kind === 'signal'
      ? `${item.data.location.file}${item.data.location.line ? ':' + item.data.location.line : ''}`
      : item.data.issue_type

  return (
    <button
      onClick={onClick}
      className={[
        'w-full text-left px-4 py-3 border-b border-outline-variant/10 transition-colors',
        selected ? 'bg-surface-container-high' : 'hover:bg-surface-container',
      ].join(' ')}
    >
      <div className="flex items-start gap-3">
        <SeverityChip severity={severity} />
        <div className="min-w-0">
          <p className="text-sm text-on-surface truncate">{label}</p>
          <p className="text-xs font-mono text-on-surface-variant truncate mt-0.5">{sub}</p>
        </div>
      </div>
    </button>
  )
}

function SignalDetail({
  item,
  runId,
}: {
  item: SignalItem
  runId: string
}) {
  const file =
    item.kind === 'signal' ? item.data.location.file : undefined
  const line =
    item.kind === 'signal' ? (item.data.location.line ?? undefined) : undefined

  const { data: snippet } = useQuery({
    queryKey: queryKeys.snippet(runId, file ?? '', line ?? 0),
    queryFn: () => api.getSnippet(runId, file!, line!),
    enabled: file !== undefined && line !== undefined,
  })

  if (item.kind === 'dep') {
    const dep = item.data
    return (
      <div className="p-6 space-y-4">
        <div className="flex items-center gap-3">
          <SeverityChip severity={dep.severity} />
          <span className="font-mono text-sm text-on-surface">{dep.package_name}</span>
        </div>
        <p className="text-on-surface text-sm">{dep.description}</p>
        {dep.license && (
          <p className="text-xs font-mono text-on-surface-variant">License: {dep.license}</p>
        )}
      </div>
    )
  }

  const sig = item.data
  return (
    <div className="p-6 space-y-5">
      <div>
        <div className="flex items-center gap-3 mb-2">
          <SeverityChip severity={sig.severity} />
          <span className="font-mono text-[10px] text-on-surface-variant uppercase tracking-widest">
            {sig.signal_type}
          </span>
        </div>
        <p className="text-on-surface text-sm">{sig.description}</p>
      </div>

      {sig.location.file && (
        <p className="font-mono text-xs text-on-surface-variant">
          {sig.location.file}
          {sig.location.line ? `:${sig.location.line}` : ''}
        </p>
      )}

      {snippet && (
        <div>
          <p className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant mb-2">
            Code Evidence
          </p>
          <CodeBlock
            lines={snippet.lines}
            startLine={snippet.start_line}
            highlightLine={snippet.highlight_line}
          />
        </div>
      )}

      {sig.why_unusual && (
        <div>
          <p className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant mb-1">
            Analysis
          </p>
          <p className="text-sm text-on-surface">{sig.why_unusual}</p>
        </div>
      )}

      {sig.suggested_action && (
        <div>
          <p className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant mb-1">
            Suggested Action
          </p>
          <p className="text-sm text-on-surface">{sig.suggested_action}</p>
        </div>
      )}

      <div className="flex gap-2 pt-2">
        <button
          disabled
          title="Coming in a future release"
          className="text-xs font-mono px-3 py-1.5 rounded border border-outline-variant/20 text-on-surface-variant cursor-not-allowed opacity-50"
        >
          Mute Signal
        </button>
        <button
          disabled
          title="Coming in a future release"
          className="text-xs font-mono px-3 py-1.5 rounded border border-outline-variant/20 text-on-surface-variant cursor-not-allowed opacity-50"
        >
          Assign Reviewer
        </button>
      </div>
    </div>
  )
}

export default function SecurityTab({
  report,
  runId,
}: {
  report: ImpactReport
  runId: string
}) {
  const [severityFilter, setSeverityFilter] = useState<'all' | Severity>('all')
  const [typeFilter, setTypeFilter] = useState<'all' | 'security' | 'dependency'>('all')
  const [search, setSearch] = useState('')
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)

  const items = allSignals(report)

  const filtered = items.filter((item) => {
    if (severityFilter !== 'all' && item.data.severity !== severityFilter) return false
    if (typeFilter === 'security' && item.kind !== 'signal') return false
    if (typeFilter === 'dependency' && item.kind !== 'dep') return false
    if (search && !getLabel(item).toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  const selected = selectedIdx !== null ? filtered[selectedIdx] ?? null : null

  if (items.length === 0) {
    return (
      <div className="p-8 text-center text-on-surface-variant">
        <span className="material-symbols-outlined text-[48px] mb-4 block text-primary">
          shield
        </span>
        <p className="font-mono text-sm">No security signals detected.</p>
      </div>
    )
  }

  return (
    <div className="flex h-full" style={{ minHeight: '600px' }}>
      {/* Signal list */}
      <div className="w-80 shrink-0 border-r border-outline-variant/10 flex flex-col">
        {/* Filter bar */}
        <div className="p-3 border-b border-outline-variant/10 space-y-2">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search signals…"
            className="w-full bg-surface-container-low text-xs font-mono px-3 py-2 rounded outline-none focus:ring-1 focus:ring-primary text-on-surface placeholder:text-on-surface-variant/50"
          />
          <div className="flex gap-1 flex-wrap">
            {(['all', 'high', 'medium', 'low'] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSeverityFilter(s)}
                className={[
                  'text-[10px] font-mono px-2 py-0.5 rounded uppercase tracking-widest',
                  severityFilter === s
                    ? 'bg-surface-container-highest text-on-surface'
                    : 'text-on-surface-variant hover:text-on-surface',
                ].join(' ')}
              >
                {s}
              </button>
            ))}
            <span className="text-outline-variant mx-1">|</span>
            {(['all', 'security', 'dependency'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTypeFilter(t)}
                className={[
                  'text-[10px] font-mono px-2 py-0.5 rounded uppercase tracking-widest',
                  typeFilter === t
                    ? 'bg-surface-container-highest text-on-surface'
                    : 'text-on-surface-variant hover:text-on-surface',
                ].join(' ')}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <p className="text-center text-on-surface-variant text-xs py-8 font-mono">
              No matching signals
            </p>
          ) : (
            filtered.map((item, i) => (
              <SignalListItem
                key={i}
                item={item}
                selected={selectedIdx === i}
                onClick={() => setSelectedIdx(i)}
              />
            ))
          )}
        </div>
      </div>

      {/* Detail panel */}
      <div className="flex-1 overflow-y-auto">
        {selected ? (
          <SignalDetail item={selected} runId={runId} />
        ) : (
          <div className="flex items-center justify-center h-full text-on-surface-variant">
            <div className="text-center">
              <span className="material-symbols-outlined text-[40px] mb-3 block">
                chevron_left
              </span>
              <p className="text-sm font-mono">Select a signal to view details</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
