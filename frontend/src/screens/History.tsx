import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { relativeTime, shortPath } from '../lib/formatters'
import { VerdictChip } from '../components/StatusChip'
import type { RunSummary } from '../lib/types'
import { useAuth } from '../lib/AuthContext'

const repoKey = (login?: string | null) => (login ? `primpact_repo_${login}` : 'primpact_repo')
type Filter = 'all' | 'clean' | 'has_blockers'

function groupByDate(runs: RunSummary[]): { label: string; runs: RunSummary[] }[] {
  const now = new Date()
  const today = new Date(now); today.setHours(0, 0, 0, 0)
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1)
  const thisWeek = new Date(today); thisWeek.setDate(today.getDate() - 7)

  const buckets: [string, RunSummary[]][] = [
    ['Today', []],
    ['Yesterday', []],
    ['This week', []],
    ['Older', []],
  ]

  for (const run of runs) {
    const d = new Date(run.created_at)
    if (d >= today) buckets[0][1].push(run)
    else if (d >= yesterday) buckets[1][1].push(run)
    else if (d >= thisWeek) buckets[2][1].push(run)
    else buckets[3][1].push(run)
  }

  return buckets
    .filter(([, r]) => r.length > 0)
    .map(([label, runs]) => ({ label, runs }))
}

function EmptyState({ icon, title, message }: { icon: string; title: string; message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-on-surface-variant">
      <span className="material-symbols-outlined text-[48px] mb-4 text-outline">{icon}</span>
      <p className="font-headline font-bold text-on-surface mb-1">{title}</p>
      <p className="text-sm text-center max-w-xs">{message}</p>
    </div>
  )
}

function RunRow({ run, onClick }: { run: RunSummary; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="group w-full flex items-center bg-surface-container-low hover:bg-surface-container-high transition-all px-4 py-3 rounded-lg border-l-4 border-primary-container/40 hover:border-primary text-left"
    >
      {/* PR badge */}
      <div className="w-12 h-10 flex items-center justify-center bg-surface-container rounded mr-4 font-mono text-primary text-xs shrink-0">
        {run.pr_number != null ? `#${run.pr_number}` : '—'}
      </div>

      {/* Title + meta */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="font-medium text-on-surface truncate text-sm">
            {run.pr_title ?? shortPath(run.head_sha, 1)}
          </span>
          <VerdictChip verdict={run.verdict} />
        </div>
        <div className="flex items-center gap-4 text-xs text-on-surface-variant font-mono">
          <span className="flex items-center gap-1">
            <span className="material-symbols-outlined text-[13px]">schedule</span>
            {relativeTime(run.created_at)}
          </span>
          <span className="flex items-center gap-1">
            <span className="material-symbols-outlined text-[13px]">hub</span>
            {run.blast_radius_count} in radius
          </span>
          {run.anomaly_count > 0 && (
            <span className="flex items-center gap-1 text-secondary">
              <span className="material-symbols-outlined text-[13px]">warning</span>
              {run.anomaly_count} anomal{run.anomaly_count === 1 ? 'y' : 'ies'}
            </span>
          )}
          {run.signal_count > 0 && (
            <span className="flex items-center gap-1 text-tertiary">
              <span className="material-symbols-outlined text-[13px]">security</span>
              {run.signal_count} signal{run.signal_count !== 1 ? 's' : ''}
            </span>
          )}
        </div>
      </div>

      {/* SHA range — visible at xl+ */}
      <div className="hidden xl:flex items-center gap-1 font-mono text-xs text-on-surface-variant mx-6 shrink-0">
        <span>{run.base_sha.slice(0, 7)}</span>
        <span className="text-outline">→</span>
        <span>{run.head_sha.slice(0, 7)}</span>
      </div>

      <span className="material-symbols-outlined text-on-surface-variant group-hover:text-primary transition-colors shrink-0">
        chevron_right
      </span>
    </button>
  )
}

export default function History() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const key = repoKey(user?.login)
  const [repo, setRepo] = useState<string>(() => localStorage.getItem(key) ?? '')
  const [filter, setFilter] = useState<Filter>('all')

  function handleRepoChange(r: string) {
    setRepo(r)
    localStorage.setItem(key, r)
  }

  const { data: runs = [], isLoading } = useQuery({
    queryKey: ['history-runs', repo],
    queryFn: () => api.getRuns(repo, 200),
    enabled: repo.trim().length > 0,
  })

  const filtered = runs.filter((run) => {
    if (filter === 'clean') return run.verdict === 'clean'
    if (filter === 'has_blockers') return run.verdict === 'has_blockers'
    return true
  })

  const counts = {
    all: runs.length,
    clean: runs.filter((r) => r.verdict === 'clean').length,
    has_blockers: runs.filter((r) => r.verdict === 'has_blockers').length,
  }

  const groups = groupByDate(filtered)

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="px-8 py-6 border-b border-outline-variant/10 bg-surface-container-low">
        <div className="flex items-center justify-between gap-6 mb-4">
          <div>
            <h1 className="font-headline text-2xl font-bold tracking-tight text-on-surface mb-0.5">
              Analysis History
            </h1>
            <p className="text-xs text-on-surface-variant font-mono">
              {repo.trim() ? `${runs.length} run${runs.length !== 1 ? 's' : ''} recorded` : 'Select a repository'}
            </p>
          </div>

          {/* Repo selector */}
          <div className="flex items-center gap-2 bg-surface-container rounded px-3 py-2">
            <span className="material-symbols-outlined text-[16px] text-on-surface-variant shrink-0">
              folder
            </span>
            <input
              type="text"
              value={repo}
              onChange={(e) => handleRepoChange(e.target.value)}
              placeholder="/path/to/repo"
              className="bg-transparent border-none outline-none font-mono text-xs text-on-surface placeholder:text-on-surface-variant/50 w-56"
            />
          </div>
        </div>

        {/* Verdict filter */}
        <div className="flex gap-1">
          {([
            ['all', 'All'],
            ['clean', 'Clean'],
            ['has_blockers', 'Blockers'],
          ] as [Filter, string][]).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={[
                'flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest px-3 py-1.5 rounded transition-colors',
                filter === value
                  ? 'bg-surface-container-highest text-on-surface'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container',
              ].join(' ')}
            >
              {label}
              <span
                className={[
                  'text-[9px] px-1.5 py-0.5 rounded-full font-bold',
                  filter === value
                    ? 'bg-primary text-on-primary-fixed'
                    : 'bg-surface-container text-on-surface-variant',
                ].join(' ')}
              >
                {counts[value]}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Run list */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        {!repo.trim() ? (
          <EmptyState
            icon="folder_open"
            title="No repository selected"
            message="Enter a repository path above to browse its analysis history."
          />
        ) : isLoading ? (
          <div className="flex items-center justify-center py-24 text-on-surface-variant">
            <span className="material-symbols-outlined text-[32px] animate-spin mr-3">
              progress_activity
            </span>
            <span className="font-mono text-sm">Loading history…</span>
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon="history"
            title={filter === 'all' ? 'No analyses yet' : `No ${filter === 'clean' ? 'clean' : 'blocker'} runs`}
            message={
              filter === 'all'
                ? 'Run an analysis from the Dashboard to get started.'
                : 'Try changing the filter above.'
            }
          />
        ) : (
          <div className="space-y-8 max-w-4xl">
            {groups.map((group) => (
              <section key={group.label}>
                <h2 className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant mb-3 flex items-center gap-3">
                  {group.label}
                  <span className="text-outline">{group.runs.length}</span>
                </h2>
                <div className="space-y-2">
                  {group.runs.map((run) => (
                    <RunRow
                      key={run.id}
                      run={run}
                      onClick={() => navigate(`/runs/${run.id}`)}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
