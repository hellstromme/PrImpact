import { createContext, useContext, useEffect, useState } from 'react'
import type { AuthUser } from './types'

interface AuthContextValue {
  user: AuthUser | null
  authEnabled: boolean
  loading: boolean
  setUser: (user: AuthUser | null) => void
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  authEnabled: false,
  loading: true,
  setUser: () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [authEnabled, setAuthEnabled] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/auth/status')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) {
          setAuthEnabled(data.auth_enabled)
          setUser(data.user ?? null)
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <AuthContext.Provider value={{ user, authEnabled, loading, setUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
