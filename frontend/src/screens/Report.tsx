import { useState, lazy, Suspense } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { queryKeys } from '../lib/queryKeys'
import { VerdictChip } from '../components/StatusChip'
import TabBar, { type Tab } from '../components/TabBar'

const SummaryTab = lazy(() => import('./tabs/SummaryTab'))
const AnomaliesTab = lazy(() => import('./tabs/AnomaliesTab'))
const BlastRadiusTab = lazy(() => import('./tabs/BlastRadiusTab'))
const SecurityTab = lazy(() => import('./tabs/SecurityTab'))
const DependenciesTab = lazy(() => import('./tabs/DependenciesTab'))
const TestGapsTab = lazy(() => import('./tabs/TestGapsTab'))

const TABS: Tab[] = [
  { id: 'summary', label: 'Summary', icon: 'summarize' },
  { id: 'blast-radius', label: 'Blast Radius', icon: 'hub' },
  { id: 'anomalies', label: 'Anomalies', icon: 'warning' },
  { id: 'security', label: 'Security', icon: 'security' },
  { id: 'dependencies', label: 'Dependencies', icon: 'account_tree' },
  { id: 'test-gaps', label: 'Test Gaps', icon: 'bug_report' },
]

export default function Report() {
  const { id: runId, tab: tabParam } = useParams<{ id: string; tab?: string }>()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState(tabParam ?? 'summary')

  // Sync tab to URL
  function handleTabChange(tab: string) {
    setActiveTab(tab)
    navigate(`/runs/${runId}/${tab}`, { replace: true })
  }

  const {
    data: report,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: queryKeys.report(runId ?? ''),
    queryFn: () => api.getReport(runId!),
    enabled: runId != null,
    staleTime: Infinity, // report data never changes for a given run
  })

  if (!runId) return null

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-on-surface-variant">
        <div className="text-center">
          <span className="material-symbols-outlined text-[48px] animate-spin block mb-3 text-primary">
            progress_activity
          </span>
          <p className="font-mono text-sm">Loading report…</p>
        </div>
      </div>
    )
  }

  if (isError || !report) {
    return (
      <div className="p-8 text-center text-tertiary">
        <span className="material-symbols-outlined text-[48px] block mb-3">error</span>
        <p className="font-mono text-sm">
          {error instanceof Error ? error.message : 'Failed to load report.'}
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Report header */}
      <div className="px-8 py-5 bg-surface-container-low border-b border-outline-variant/10">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="font-headline text-2xl font-bold tracking-tight text-on-surface mb-1">
              {report.pr_title || 'Analysis Report'}
            </h1>
            <div className="flex items-center gap-3 font-mono text-xs text-on-surface-variant">
              <span>{report.base_sha.slice(0, 8)}</span>
              <span>→</span>
              <span>{report.head_sha.slice(0, 8)}</span>
              <span>·</span>
              <span>{report.changed_files.length} files changed</span>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {report.verdict && <VerdictChip verdict={report.verdict.status} />}
            {!report.verdict && report.ai_analysis.anomalies.length === 0 && (
              <VerdictChip verdict="clean" />
            )}
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <TabBar tabs={TABS} activeTab={activeTab} onSelect={handleTabChange} />

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        <Suspense fallback={
          <div className="flex items-center justify-center h-32 text-on-surface-variant">
            <span className="material-symbols-outlined animate-spin text-[32px] text-primary">progress_activity</span>
          </div>
        }>
          {activeTab === 'summary' && <SummaryTab report={report} onNavigate={handleTabChange} />}
          {activeTab === 'blast-radius' && <BlastRadiusTab report={report} />}
          {activeTab === 'anomalies' && <AnomaliesTab report={report} />}
          {activeTab === 'security' && <SecurityTab report={report} runId={runId} />}
          {activeTab === 'dependencies' && <DependenciesTab report={report} />}
          {activeTab === 'test-gaps' && <TestGapsTab report={report} />}
        </Suspense>
      </div>
    </div>
  )
}
