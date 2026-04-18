import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext'

export default function Login() {
  const { user, authEnabled, loading } = useAuth()
  const navigate = useNavigate()

  // If auth is disabled or user is already authenticated, go to dashboard
  useEffect(() => {
    if (!loading && (!authEnabled || user)) {
      navigate('/', { replace: true })
    }
  }, [loading, authEnabled, user, navigate])

  const params = new URLSearchParams(window.location.search)
  const forbidden = params.get('error') === 'forbidden'

  if (loading) return null

  return (
    <div className="flex h-screen items-center justify-center bg-surface">
      <div className="w-full max-w-sm rounded-2xl border border-outline-variant/20 bg-surface-container-low p-8 shadow-sm">
        <h1 className="font-headline text-2xl font-bold text-primary mb-1 tracking-tight">
          PrImpact
        </h1>
        <p className="text-sm text-on-surface-variant mb-8">
          AI-powered pull request impact analysis
        </p>

        {forbidden && (
          <div className="mb-6 rounded-lg border border-error/30 bg-error/10 px-4 py-3 text-sm text-error">
            Your GitHub account is not authorised to access this instance. Contact
            the server administrator.
          </div>
        )}

        <a
          href="/auth/login"
          className="flex w-full items-center justify-center gap-3 rounded-lg bg-primary px-4 py-3 text-sm font-medium text-on-primary transition-opacity hover:opacity-90"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current" aria-hidden="true">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
          </svg>
          Sign in with GitHub
        </a>
      </div>
    </div>
  )
}
