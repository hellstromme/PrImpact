import { useParams, NavLink } from 'react-router-dom'
import { useActiveRun } from '../context/ActiveRunContext'

interface NavItem {
  label: string
  href: string
  icon: string
}

const perAnalysisItems: NavItem[] = [
  { label: 'SUMMARY', href: '', icon: 'summarize' },
  { label: 'BLAST RADIUS', href: '/blast-radius', icon: 'hub' },
  { label: 'SECURITY', href: '/security', icon: 'security' },
  { label: 'DEPENDENCIES', href: '/dependencies', icon: 'account_tree' },
  { label: 'TEST GAPS', href: '/test-gaps', icon: 'bug_report' },
]

const globalItems: NavItem[] = [
  { label: 'HISTORY', href: '/history', icon: 'history' },
]

const bottomItems: NavItem[] = [
  { label: 'DOCUMENTATION', href: '/docs', icon: 'description' },
  { label: 'SUPPORT', href: '/support', icon: 'help' },
]

function Icon({ name }: { name: string }) {
  return (
    <span className="material-symbols-outlined text-[18px] leading-none">
      {name}
    </span>
  )
}

function SideNavLink({
  to,
  icon,
  label,
}: {
  to: string
  icon: string
  label: string
}) {
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        [
          'flex items-center gap-3 px-4 py-2 text-[0.6875rem] tracking-widest font-mono transition-colors',
          isActive
            ? 'border-l-2 border-primary bg-surface-container-high text-primary'
            : 'border-l-2 border-transparent text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface',
        ].join(' ')
      }
    >
      <Icon name={icon} />
      {label}
    </NavLink>
  )
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const { id: runId } = useParams<{ id?: string }>()
  const { runId: contextRunId } = useActiveRun()

  // Determine active run from URL param or context
  const activeRunId = runId ?? contextRunId

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 flex flex-col bg-surface-container-low border-r border-outline-variant/10 h-screen sticky top-0 overflow-y-auto">
        {/* Brand */}
        <div className="px-4 py-5 border-b border-outline-variant/10">
          <span className="font-headline text-xl font-bold text-primary tracking-tight">
            PrImpact
          </span>
        </div>

        {/* + New Analysis */}
        <div className="px-3 py-4 border-b border-outline-variant/10">
          <NavLink
            to="/"
            className="flex items-center justify-center gap-2 w-full machined-gradient text-on-primary-fixed text-[0.75rem] font-bold px-4 py-2.5 rounded hover:opacity-90 active:scale-95 transition-all"
          >
            <Icon name="add" />
            NEW ANALYSIS
          </NavLink>
        </div>

        {/* Per-analysis nav */}
        {activeRunId && (
          <div className="py-2 border-b border-outline-variant/10">
            {perAnalysisItems.map((item) => (
              <SideNavLink
                key={item.label}
                to={`/runs/${activeRunId}${item.href}`}
                icon={item.icon}
                label={item.label}
              />
            ))}
          </div>
        )}

        {/* Global items */}
        <div className="py-2 flex-1">
          {globalItems.map((item) => (
            <SideNavLink
              key={item.label}
              to={item.href}
              icon={item.icon}
              label={item.label}
            />
          ))}
        </div>

        {/* Bottom items */}
        <div className="py-2 border-t border-outline-variant/10">
          {bottomItems.map((item) => (
            <SideNavLink
              key={item.label}
              to={item.href}
              icon={item.icon}
              label={item.label}
            />
          ))}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 overflow-y-auto bg-surface">
        {children}
      </main>
    </div>
  )
}
