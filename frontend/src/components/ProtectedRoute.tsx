import { Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext'

function Spinner() {
  return (
    <div className="flex h-screen items-center justify-center bg-surface">
      <span className="material-symbols-outlined animate-spin text-4xl text-primary">
        progress_activity
      </span>
    </div>
  )
}

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, authEnabled, loading } = useAuth()
  if (loading) return <Spinner />
  if (authEnabled && !user) return <Navigate to="/login" replace />
  return <>{children}</>
}
