import { useQuery } from '@tanstack/react-query'
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

export default function Settings() {
  // We need a repo path — try to derive from URL or use a placeholder
  const repoParam = new URLSearchParams(window.location.search).get('repo') ?? ''

  const { data, error, isLoading } = useQuery<ConfigData>({
    queryKey: ['config', repoParam],
    queryFn: () => api.getConfig(repoParam),
    enabled: Boolean(repoParam),
    retry: false,
  })

  return (
    <div className="p-8 max-w-3xl">
      <h1 className="text-2xl font-headline font-bold text-on-surface mb-1">Settings</h1>
      <p className="text-sm text-on-surface-variant mb-8">
        Team configuration loaded from{' '}
        <code className="font-mono text-xs bg-surface-container-high px-1 py-0.5 rounded">
          .primpact.yml
        </code>{' '}
        in the repo root.
      </p>

      {isLoading && (
        <p className="text-sm text-on-surface-variant">Loading configuration...</p>
      )}

      {!repoParam && !isLoading && (
        <div className="mb-8 p-4 bg-surface-container rounded border border-outline-variant/20">
          <p className="text-sm text-on-surface-variant mb-1">
            No repo selected. Open a run to see its configuration, or pass{' '}
            <code className="font-mono text-xs bg-surface-container-high px-1 py-0.5 rounded">
              ?repo=/path/to/repo
            </code>{' '}
            in the URL.
          </p>
        </div>
      )}

      {(error || (data === undefined && repoParam && !isLoading)) && (
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
    </div>
  )
}
