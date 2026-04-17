import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { PrImpactConfig } from '../lib/types'

const STARTER_TEMPLATE = `# .primpact.yml
high_sensitivity_modules:
  - src/auth/
  - src/payments/

suppressed_signals:
  - signal_type: shell_invoke
    path_prefix: tools/
    reason: "Build tools intentionally use subprocess"

blast_radius_depth:
  src/utils/: 2

fail_on_severity: high
`

function CopyButton({ text }: { text: string }) {
  const handleCopy = () => {
    navigator.clipboard.writeText(text).catch(() => {})
  }
  return (
    <button
      onClick={handleCopy}
      className="px-3 py-1 text-xs font-mono bg-surface-container-high text-on-surface-variant rounded hover:bg-surface-container hover:text-on-surface transition-colors"
    >
      Copy
    </button>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-8">
      <h2 className="text-sm font-semibold text-on-surface-variant uppercase tracking-widest mb-3 font-mono">
        {title}
      </h2>
      {children}
    </div>
  )
}

function EmptyValue() {
  return <span className="text-on-surface-variant italic text-sm">none configured</span>
}

interface ConfigData extends PrImpactConfig {
  path: string
}

function ClearDatabaseModal({
  onConfirm,
  onCancel,
  isPending,
}: {
  onConfirm: () => void
  onCancel: () => void
  isPending: boolean
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-surface rounded-lg border border-outline-variant/30 shadow-xl max-w-md w-full mx-4 p-6">
        <h3 className="text-base font-semibold text-on-surface mb-2">Clear Run History?</h3>
        <p className="text-sm text-on-surface-variant mb-6">
          This will permanently delete all recorded analysis runs for this repository. Historical
          hotspots and anomaly patterns will be lost. <strong className="text-on-surface">This
          cannot be undone.</strong>
        </p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={isPending}
            className="px-4 py-2 text-sm font-medium text-on-surface-variant bg-surface-container rounded hover:bg-surface-container-high transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={isPending}
            className="px-4 py-2 text-sm font-medium text-on-error bg-error rounded hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {isPending ? 'Clearing…' : 'Clear History'}
          </button>
        </div>
      </div>
    </div>
  )
}

const REPO_KEY = 'primpact_repo'

export default function Settings() {
  const [repo, setRepo] = useState<string>(() => {
    const urlParam = new URLSearchParams(window.location.search).get('repo')
    return urlParam ?? localStorage.getItem(REPO_KEY) ?? ''
  })
  const queryClient = useQueryClient()
  const [showClearModal, setShowClearModal] = useState(false)
  const [clearPending, setClearPending] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)
  const [clearSuccess, setClearSuccess] = useState(false)

  function handleRepoChange(r: string) {
    setRepo(r)
    localStorage.setItem(REPO_KEY, r)
  }

  async function handleClearConfirm() {
    setClearPending(true)
    setClearError(null)
    try {
      await api.clearHistory(repo)
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      setClearSuccess(true)
      setShowClearModal(false)
    } catch (err) {
      setClearError(err instanceof Error ? err.message : 'Unknown error')
      setShowClearModal(false)
    } finally {
      setClearPending(false)
    }
  }

  const { data, error, isLoading } = useQuery<ConfigData>({
    queryKey: ['config', repo],
    queryFn: () => api.getConfig(repo),
    enabled: Boolean(repo.trim()),
    retry: false,
  })

  return (
    <div className="p-8 max-w-3xl">
      {showClearModal && (
        <ClearDatabaseModal
          onConfirm={handleClearConfirm}
          onCancel={() => setShowClearModal(false)}
          isPending={clearPending}
        />
      )}

      <div className="flex items-start justify-between gap-6 mb-6">
        <div>
          <h1 className="text-2xl font-headline font-bold text-on-surface mb-1">Settings</h1>
          <p className="text-sm text-on-surface-variant">
            Team configuration loaded from{' '}
            <code className="font-mono text-xs bg-surface-container-high px-1 py-0.5 rounded">
              .primpact.yml
            </code>{' '}
            in the repo root.
          </p>
        </div>
        <div className="flex items-center gap-2 bg-surface-container rounded px-3 py-2 shrink-0">
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

      {isLoading && (
        <p className="text-sm text-on-surface-variant">Loading configuration...</p>
      )}

      {!repo.trim() && !isLoading && (
        <div className="mb-8 p-4 bg-surface-container rounded border border-outline-variant/20">
          <p className="text-sm text-on-surface-variant">
            Enter a repository path above to load its configuration.
          </p>
        </div>
      )}

      {(error || (data === undefined && repo.trim() && !isLoading)) && (
        <div className="mb-8">
          <div className="p-4 bg-surface-container rounded border border-outline-variant/20 mb-6">
            <p className="text-sm text-on-surface-variant">
              No <code className="font-mono text-xs">.primpact.yml</code> found in the repository
              root. Copy the starter template below to enable team configuration.
            </p>
          </div>

          <Section title="Starter Template">
            <div className="relative">
              <div className="flex justify-between items-center mb-2">
                <span className="text-xs text-on-surface-variant font-mono">.primpact.yml</span>
                <CopyButton text={STARTER_TEMPLATE} />
              </div>
              <pre className="bg-surface-container p-4 rounded text-xs font-mono text-on-surface whitespace-pre overflow-x-auto border border-outline-variant/20">
                {STARTER_TEMPLATE}
              </pre>
            </div>
          </Section>
        </div>
      )}

      {data && (
        <>
          <div className="mb-6 text-xs font-mono text-on-surface-variant">
            Loaded from:{' '}
            <code className="bg-surface-container-high px-1 py-0.5 rounded">{data.path}</code>
          </div>

          <Section title="High-Sensitivity Modules">
            {data.high_sensitivity_modules && data.high_sensitivity_modules.length > 0 ? (
              <ul className="list-disc list-inside space-y-1">
                {data.high_sensitivity_modules.map((m) => (
                  <li key={m} className="text-sm font-mono text-on-surface">
                    {m}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyValue />
            )}
          </Section>

          <Section title="Suppressed Signals">
            {data.suppressed_signals && data.suppressed_signals.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-outline-variant/20">
                      <th className="pb-2 pr-4 text-xs uppercase tracking-wider text-on-surface-variant font-mono font-normal">
                        Signal Type
                      </th>
                      <th className="pb-2 pr-4 text-xs uppercase tracking-wider text-on-surface-variant font-mono font-normal">
                        Path Prefix
                      </th>
                      <th className="pb-2 text-xs uppercase tracking-wider text-on-surface-variant font-mono font-normal">
                        Reason
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.suppressed_signals.map((s, i) => (
                      <tr key={i} className="border-b border-outline-variant/10">
                        <td className="py-2 pr-4 font-mono text-xs text-on-surface">
                          {s.signal_type}
                        </td>
                        <td className="py-2 pr-4 font-mono text-xs text-on-surface">
                          {s.path_prefix}
                        </td>
                        <td className="py-2 text-xs text-on-surface-variant">{s.reason || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyValue />
            )}
          </Section>

          <Section title="Blast Radius Depth Overrides">
            {data.blast_radius_depth && Object.keys(data.blast_radius_depth).length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-outline-variant/20">
                      <th className="pb-2 pr-4 text-xs uppercase tracking-wider text-on-surface-variant font-mono font-normal">
                        Path
                      </th>
                      <th className="pb-2 text-xs uppercase tracking-wider text-on-surface-variant font-mono font-normal">
                        Depth
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.blast_radius_depth).map(([path, depth]) => (
                      <tr key={path} className="border-b border-outline-variant/10">
                        <td className="py-2 pr-4 font-mono text-xs text-on-surface">{path}</td>
                        <td className="py-2 font-mono text-xs text-on-surface">{depth}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyValue />
            )}
          </Section>

          <Section title="CI Severity Threshold">
            {data.fail_on_severity ? (
              <span className="font-mono text-sm text-on-surface">{data.fail_on_severity}</span>
            ) : (
              <span className="text-sm text-on-surface-variant italic">using CLI default</span>
            )}
          </Section>

          <Section title="Anomaly Thresholds">
            {data.anomaly_thresholds && Object.keys(data.anomaly_thresholds).length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-outline-variant/20">
                      <th className="pb-2 pr-4 text-xs uppercase tracking-wider text-on-surface-variant font-mono font-normal">
                        Signal Type
                      </th>
                      <th className="pb-2 text-xs uppercase tracking-wider text-on-surface-variant font-mono font-normal">
                        Severity
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.anomaly_thresholds).map(([k, v]) => (
                      <tr key={k} className="border-b border-outline-variant/10">
                        <td className="py-2 pr-4 font-mono text-xs text-on-surface">{k}</td>
                        <td className="py-2 font-mono text-xs text-on-surface">{v}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyValue />
            )}
          </Section>
        </>
      )}

      <div className="mt-12 pt-8 border-t border-outline-variant/20">
        <h2 className="text-sm font-semibold text-error uppercase tracking-widest mb-3 font-mono">
          Danger Zone
        </h2>
        <div className="p-4 bg-surface-container rounded border border-error/20">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-on-surface mb-1">Clear Run History</p>
              <p className="text-xs text-on-surface-variant">
                Permanently delete all recorded analysis runs for this repository. Historical
                hotspot and anomaly data will be lost.
              </p>
              {clearSuccess && (
                <p className="text-xs text-primary mt-2">History cleared successfully.</p>
              )}
              {clearError && (
                <p className="text-xs text-error mt-2">Error: {clearError}</p>
              )}
            </div>
            <button
              onClick={() => { setClearSuccess(false); setClearError(null); setShowClearModal(true) }}
              disabled={!repo.trim()}
              className="shrink-0 px-4 py-2 text-sm font-medium text-on-error bg-error rounded hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Clear History
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
