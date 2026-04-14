import type { ImpactReport } from '../../lib/types'
import { SeverityChip } from '../../components/StatusChip'

const chipBase =
  'inline-flex items-center px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-[2px]'

function IssueTypeChip({ type }: { type: string }) {
  if (type === 'typosquat') {
    return (
      <span className={`${chipBase} bg-tertiary-container/20 text-tertiary border border-tertiary/30`}>
        TYPOSQUAT
      </span>
    )
  }
  if (type === 'vulnerability') {
    return (
      <span className={`${chipBase} bg-tertiary-container/20 text-tertiary border border-tertiary/30`}>
        VULN
      </span>
    )
  }
  return (
    <span className={`${chipBase} bg-secondary-container/20 text-secondary`}>
      UPDATED
    </span>
  )
}

export default function DependenciesTab({ report }: { report: ImpactReport }) {
  const deps = report.dependency_issues

  const added = deps.filter((d) => d.issue_type === 'typosquat').length
  const vulns = deps.filter((d) => d.issue_type === 'vulnerability').length
  const updated = deps.filter((d) => d.issue_type === 'version_change').length

  if (deps.length === 0) {
    return (
      <div className="p-8 text-center text-on-surface-variant">
        <span className="material-symbols-outlined text-[48px] mb-4 block text-primary">
          account_tree
        </span>
        <p className="font-mono text-sm">No dependency changes detected.</p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-5xl space-y-8">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Flagged / New', value: added, color: 'text-tertiary' },
          { label: 'Vulnerabilities', value: vulns, color: 'text-tertiary' },
          { label: 'Version Changes', value: updated, color: 'text-secondary' },
        ].map((s) => (
          <div
            key={s.label}
            className="bg-surface-container-low p-4 rounded-lg border border-outline-variant/10"
          >
            <div className="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant mb-2">
              {s.label}
            </div>
            <div className={`font-headline text-3xl font-bold ${s.color}`}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Table */}
      <section>
        <h2 className="font-headline text-lg font-semibold mb-4 border-b border-outline-variant/10 pb-2">
          Dependency Details
        </h2>
        <div className="overflow-x-auto rounded-lg border border-outline-variant/10">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-surface-container-low border-b border-outline-variant/10">
                {['Package', 'Type', 'Severity', 'Description', 'License'].map((h) => (
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
              {deps.map((dep, i) => (
                <tr
                  key={i}
                  className="border-b border-outline-variant/10 hover:bg-surface-container-high/50 transition-colors"
                >
                  <td className="px-4 py-3 font-mono text-xs text-primary">{dep.package_name}</td>
                  <td className="px-4 py-3">
                    <IssueTypeChip type={dep.issue_type} />
                  </td>
                  <td className="px-4 py-3">
                    <SeverityChip severity={dep.severity} />
                  </td>
                  <td className="px-4 py-3 text-xs text-on-surface-variant max-w-xs">
                    {dep.description}
                  </td>
                  <td className="px-4 py-3 text-xs font-mono text-on-surface-variant">
                    {dep.license ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
