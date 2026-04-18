import { Link, NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext'
import { api } from '../lib/api'

interface NavItem {
  label: string
  href: string
  icon: string
}

const globalItems: NavItem[] = [
  { label: 'DASHBOARD', href: '/', icon: 'home' },
  { label: 'HISTORY', href: '/history', icon: 'history' },
  { label: 'SETTINGS', href: '/settings', icon: 'settings' },
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
  end = false,
}: {
  to: string
  icon: string
  label: string
  end?: boolean
}) {
  return (
    <NavLink
      to={to}
      end={end}
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
  const { user, authEnabled } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await api.logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 flex flex-col bg-surface-container-low border-r border-outline-variant/10 h-screen sticky top-0 overflow-y-auto">
        {/* Brand — links home */}
        <div className="px-4 py-5 border-b border-outline-variant/10">
          <Link to="/" className="font-headline text-xl font-bold text-primary tracking-tight hover:opacity-80 transition-opacity">
            PrImpact
          </Link>
        </div>

        {/* Global nav */}
        <div className="py-2 flex-1">
          {globalItems.map((item) => (
            <SideNavLink
              key={item.label}
              to={item.href}
              icon={item.icon}
              label={item.label}
              end={item.href === '/'}
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

        {/* User profile (only shown when auth is enabled) */}
        {authEnabled && user && (
          <div className="px-4 py-3 border-t border-outline-variant/10 flex items-center gap-2">
            {user.avatar_url ? (
              <img
                src={user.avatar_url}
                alt={user.login}
                className="h-6 w-6 rounded-full shrink-0"
              />
            ) : (
              <span className="material-symbols-outlined text-[18px] text-on-surface-variant shrink-0">
                account_circle
              </span>
            )}
            <span className="text-[0.6875rem] font-mono text-on-surface-variant truncate flex-1">
              {user.login}
            </span>
            <button
              onClick={handleLogout}
              title="Sign out"
              className="text-on-surface-variant hover:text-on-surface transition-colors"
            >
              <span className="material-symbols-outlined text-[18px] leading-none">logout</span>
            </button>
          </div>
        )}
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 overflow-y-auto bg-surface">
        {children}
      </main>
    </div>
  )
}
