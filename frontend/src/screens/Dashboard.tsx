import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { queryKeys } from '../lib/queryKeys'
import { relativeTime, shortPath } from '../lib/formatters'
import { VerdictChip } from '../components/StatusChip'
import SparkLine from '../components/SparkLine'
import type { RunSummary } from '../lib/types'
import { useAuth } from '../lib/AuthContext'

const repoKey = (login?: string | null) => (login ? `primpact_repo_${login}` : 'primpact_repo')

// ---------------------------------------------------------------------------
// HeroForm
// ---------------------------------------------------------------------------

function HeroForm({ repo, onRepoChange }: { repo: string; onRepoChange: (r: string) => void }) {
  const navigate = useNavigate()
  const [prNumber, setPrNumber] = useState('')
  const [baseSha, setBaseSha] = useState('')
  const [headSha, setHeadSha] = useState('')
  const [mode, setMode] = useState<'pr' | 'sha'>('pr')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pollingId, setPollingId] = useState<string | null>(null)

  // Poll status when analysis is running
  const { data: statusData } = useQuery({
    queryKey: queryKeys.status(pollingId ?? ''),
    queryFn: () => api.getStatus(pollingId!),
    enabled: pollingId !== null,
    refetchInterval: 2000,
  })

  useEffect(() => {
    if (!statusData) return
    if (statusData.status === 'complete') {
      navigate(`/runs/${statusData.run_id}`)
    } else if (statusData.status === 'failed') {
      setError(statusData.error ?? 'Analysis failed')
      setSubmitting(false)
      setPollingId(null)
    }
  }, [statusData, navigate])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)

    try {
      const body =
        mode === 'pr'
          ? { repo, pr_number: parseInt(prNumber, 10) }
          : { repo, base_sha: baseSha, head_sha: headSha }

      const { run_id } = await api.postAnalyse(body)
      setPollingId(run_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-3xl mx-auto">
      <div className="bg-surface-container border border-outline-variant/20 p-1 rounded-lg shadow-2xl">
        <div className="bg-surface p-6 rounded-[5px]">
          {/* Mode toggle */}
          <div className="flex gap-2 mb-5">
            <button
              type="button"
              onClick={() => setMode('pr')}
              className={`text-[10px] font-mono uppercase tracking-widest px-3 py-1 rounded transition-colors ${
                mode === 'pr'
                  ? 'bg-surface-container-highest text-on-surface'
                  : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              PR Number
            </button>
            <button
              type="button"
              onClick={() => setMode('sha')}
              className={`text-[10px] font-mono uppercase tracking-widest px-3 py-1 rounded transition-colors ${
                mode === 'sha'
                  ? 'bg-surface-container-highest text-on-surface'
                  : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              SHA Range
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-[10px] font-mono text-outline uppercase tracking-widest mb-1">
                Repository Path
              </label>
              <input
                type="text"
                value={repo}
                onChange={(e) => onRepoChange(e.target.value)}
                placeholder="/path/to/repo"
                required
                className="w-full bg-surface-container-low border-none focus:ring-1 focus:ring-primary rounded font-mono text-sm px-4 py-3 text-on-surface placeholder:text-on-surface-variant/50 outline-none"
              />
            </div>

            {mode === 'pr' ? (
              <div>
                <label className="block text-[10px] font-mono text-outline uppercase tracking-widest mb-1">
                  PR Number
                </label>
                <input
                  type="number"
                  value={prNumber}
                  onChange={(e) => setPrNumber(e.target.value)}
                  placeholder="e.g. 247"
                  required
                  min={1}
                  className="w-full bg-surface-container-low border-none focus:ring-1 focus:ring-primary rounded font-mono text-sm px-4 py-3 text-on-surface placeholder:text-on-surface-variant/50 outline-none"
                />
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] font-mono text-outline uppercase tracking-widest mb-1">
                    Base SHA
                  </label>
                  <input
                    type="text"
                    value={baseSha}
                    onChange={(e) => setBaseSha(e.target.value)}
                    placeholder="abc1234"
                    required
                    className="w-full bg-surface-container-low border-none focus:ring-1 focus:ring-primary rounded font-mono text-sm px-4 py-3 text-on-surface placeholder:text-on-surface-variant/50 outline-none"
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-mono text-outline uppercase tracking-widest mb-1">
                    Head SHA
                  </label>
                  <input
                    type="text"
                    value={headSha}
                    onChange={(e) => setHeadSha(e.target.value)}
                    placeholder="def5678"
                    required
                    className="w-full bg-surface-container-low border-none focus:ring-1 focus:ring-primary rounded font-mono text-sm px-4 py-3 text-on-surface placeholder:text-on-surface-variant/50 outline-none"
                  />
                </div>
              </div>
            )}
          </div>

          {error && (
            <p className="text-tertiary text-xs font-mono mb-3 px-1">{error}</p>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={submitting}
              className="machined-gradient text-on-primary-fixed px-8 py-3 rounded font-bold flex items-center gap-2 hover:opacity-90 active:scale-95 transition-all disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {submitting ? (
                <>
                  <span className="material-symbols-outlined text-[18px] animate-spin">
                    progress_activity
                  </span>
                  {pollingId ? 'Analysing…' : 'Starting…'}
                </>
              ) : (
                <>
                  Run Analysis
                  <span className="material-symbols-outlined text-[18px]">bolt</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </form>
  )
}

// ---------------------------------------------------------------------------
// StatsRow
// ---------------------------------------------------------------------------

function StatsRow({ runs }: { runs: RunSummary[] }) {
  const activeAnalyses = runs.filter((r) => !r.merged).length

  const avgBlast =
    runs.length > 0
      ? Math.round(runs.reduce((s, r) => s + r.blast_radius_count, 0) / runs.length)
      : 0

  const resolved = runs.filter((r) => r.verdict === 'clean').length

  const stats = [
    { label: 'Active Analyses', value: activeAnalyses.toString().padStart(2, '0'), icon: 'sync' },
    { label: 'Avg Blast Radius', value: avgBlast.toString(), icon: 'explosion' },
    { label: 'Blockers Resolved', value: resolved.toString(), icon: 'gavel' },
  ]

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
      {stats.map((s) => (
        <div
          key={s.label}
          className="bg-surface-container-low p-6 rounded-lg border border-outline-variant/10"
        >
          <div className="flex justify-between items-start mb-4">
            <span className="text-outline text-[10px] font-mono uppercase tracking-widest">
              {s.label}
            </span>
            <span className="material-symbols-outlined text-primary text-[20px]">{s.icon}</span>
          </div>
          <div className="font-headline text-4xl font-bold">{s.value}</div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// RecentRunsList
// ---------------------------------------------------------------------------

function RecentRunsList({ runs }: { runs: RunSummary[] }) {
  const navigate = useNavigate()
  const maxBlastRadius = runs.reduce((m, r) => Math.max(m, r.blast_radius_count), 1)

  if (runs.length === 0) {
    return (
      <div className="text-center py-16 text-on-surface-variant">
        <span className="material-symbols-outlined text-[48px] mb-4 block">history</span>
        <p className="font-mono text-sm">No analyses in the last 7 days for this repository.</p>
        <p className="text-xs mt-1">Run an analysis above to get started.</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {runs.map((run) => (
        <button
          key={run.id}
          onClick={() => navigate(`/runs/${run.id}`)}
          className="group w-full flex items-center bg-surface-container-low hover:bg-surface-container-high transition-all p-4 rounded-lg border-l-4 border-primary-container/40 hover:border-primary text-left"
        >
          {/* PR number */}
          <div className="w-14 h-12 flex items-center justify-center bg-surface-container rounded mr-4 font-mono text-primary text-xs shrink-0">
            {run.pr_number != null ? `#${run.pr_number}` : '—'}
          </div>

          {/* Title + meta */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="font-bold text-on-surface truncate">
                {run.pr_title ?? shortPath(run.head_sha, 1)}
              </span>
              <VerdictChip verdict={run.verdict} />
            </div>
            <div className="flex items-center gap-4 text-xs text-on-surface-variant font-mono">
              <span className="flex items-center gap-1">
                <span className="material-symbols-outlined text-[14px]">schedule</span>
                {relativeTime(run.created_at)}
              </span>
              <span className="flex items-center gap-1">
                <span className="material-symbols-outlined text-[14px]">hub</span>
                {run.blast_radius_count} files
              </span>
              {run.signal_count > 0 && (
                <span className="flex items-center gap-1 text-secondary">
                  <span className="material-symbols-outlined text-[14px]">security</span>
                  {run.signal_count} signals
                </span>
              )}
            </div>
          </div>

          {/* Blast radius bar */}
          <div className="hidden lg:flex items-center gap-2 ml-6 shrink-0">
            <span className="font-mono text-xs text-on-surface-variant w-8 text-right">
              {run.blast_radius_count}
            </span>
            <SparkLine values={[run.blast_radius_count]} max={maxBlastRadius} />
          </div>

          <span className="material-symbols-outlined text-on-surface-variant group-hover:text-primary transition-colors ml-4 shrink-0">
            chevron_right
          </span>
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

export default function Dashboard() {
  const { user } = useAuth()
  const key = repoKey(user?.login)
  const [repo, setRepo] = useState<string>(() => localStorage.getItem(key) ?? '')
  const [debouncedRepo, setDebouncedRepo] = useState(repo)

  useEffect(() => {
    const id = setTimeout(() => setDebouncedRepo(repo), 300)
    return () => clearTimeout(id)
  }, [repo])

  function handleRepoChange(r: string) {
    setRepo(r)
    localStorage.setItem(key, r)
  }

  const { data: runs = [], isLoading } = useQuery({
    queryKey: queryKeys.runs(debouncedRepo),
    queryFn: () => api.getRuns(debouncedRepo),
    enabled: debouncedRepo.trim().length > 0,
  })

  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000
  const recentRuns = runs.filter(
    (r) => new Date(r.created_at).getTime() >= sevenDaysAgo
  )

  return (
    <div className="px-8 py-12">
      {/* Hero */}
      <div className="text-center mb-10">
        <h1 className="font-headline text-5xl font-bold tracking-tighter mb-4 text-on-surface">
          Analyse the impact of your{' '}
          <span className="text-primary">code changes</span>
        </h1>
        <p className="text-on-surface-variant max-w-2xl mx-auto text-lg">
          Instantly visualise blast radius, security regressions, and dependency shifts
          across your entire repository.
        </p>
      </div>

      <div className="mb-16">
        <HeroForm repo={repo} onRepoChange={handleRepoChange} />
      </div>

      {/* Stats */}
      <StatsRow runs={recentRuns} />

      {/* Recent runs */}
      <section>
        <div className="flex items-center justify-between mb-6">
          <h2 className="font-headline text-2xl font-bold tracking-tight">Recent Reports</h2>
          <span className="text-xs font-mono text-on-surface-variant">Last 7 days</span>
        </div>
        {isLoading ? (
          <div className="text-center py-12 text-on-surface-variant">
            <span className="material-symbols-outlined text-[32px] animate-spin block mb-2">
              progress_activity
            </span>
            <p className="text-sm font-mono">Loading runs…</p>
          </div>
        ) : (
          <RecentRunsList runs={recentRuns} />
        )}
      </section>
    </div>
  )
}
