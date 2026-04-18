import { lazy, Suspense } from 'react'
import { render, screen } from '@testing-library/react'
import { TabErrorBoundary } from './Report'

// ─── TabErrorBoundary — error catching ───────────────────────────────────────

function ThrowOnRender(): never {
  throw new Error('tab render error')
}

describe('TabErrorBoundary', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders children when no error occurs', () => {
    render(
      <TabErrorBoundary>
        <div data-testid="child">content</div>
      </TabErrorBoundary>
    )
    expect(screen.getByTestId('child')).toBeInTheDocument()
  })

  it('shows error fallback when a child throws during render', () => {
    render(
      <TabErrorBoundary>
        <ThrowOnRender />
      </TabErrorBoundary>
    )
    expect(screen.getByTestId('tab-error-fallback')).toBeInTheDocument()
    expect(screen.getByText(/failed to load this tab/i)).toBeInTheDocument()
  })

  it('does not render children when in error state', () => {
    render(
      <TabErrorBoundary>
        <ThrowOnRender />
      </TabErrorBoundary>
    )
    expect(screen.queryByRole('main')).not.toBeInTheDocument()
  })
})

// ─── Suspense fallback ────────────────────────────────────────────────────────

describe('Suspense fallback spinner', () => {
  it('shows spinner while a lazy component is loading', async () => {
    let resolveImport!: (m: { default: () => JSX.Element }) => void
    const lazyPromise = new Promise<{ default: () => JSX.Element }>((r) => {
      resolveImport = r
    })
    const LazyComp = lazy(() => lazyPromise)

    render(
      <Suspense
        fallback={
          <div data-testid="tab-suspense-fallback">
            <span>progress_activity</span>
          </div>
        }
      >
        <LazyComp />
      </Suspense>
    )

    expect(screen.getByTestId('tab-suspense-fallback')).toBeInTheDocument()

    resolveImport({ default: () => <div data-testid="loaded-content" /> })
    await screen.findByTestId('loaded-content')
    expect(screen.queryByTestId('tab-suspense-fallback')).not.toBeInTheDocument()
  })
})
