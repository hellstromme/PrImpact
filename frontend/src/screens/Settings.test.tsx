import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Settings from './Settings'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    getConfig: vi.fn(),
    clearHistory: vi.fn(),
  },
}))

const REPO_KEY = 'primpact_repo'

const EMPTY_CONFIG = {
  path: '/.primpact.yml',
  high_sensitivity_modules: [],
  suppressed_signals: [],
  blast_radius_depth: {},
  fail_on_severity: null,
  anomaly_thresholds: {},
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
}

function renderSettings() {
  render(
    <QueryClientProvider client={makeClient()}>
      <Settings />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  localStorage.clear()
  vi.resetAllMocks()
  window.history.pushState({}, '', '/')
  // Default: getConfig never settles (keeps query in loading state by default)
  vi.mocked(api.getConfig).mockReturnValue(new Promise(() => {}))
  vi.mocked(api.clearHistory).mockResolvedValue({ deleted: true })
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// Repo initialisation
// ---------------------------------------------------------------------------

describe('repo state initialisation', () => {
  it('initialises from localStorage when URL parameter is absent', () => {
    localStorage.setItem(REPO_KEY, '/stored/repo')
    renderSettings()
    expect(screen.getByPlaceholderText('/path/to/repo')).toHaveValue('/stored/repo')
  })

  it('falls back to empty string when both URL param and localStorage are absent', () => {
    renderSettings()
    expect(screen.getByPlaceholderText('/path/to/repo')).toHaveValue('')
  })

  it('prefers URL parameter over localStorage when both are present', () => {
    localStorage.setItem(REPO_KEY, '/stored/repo')
    window.history.pushState({}, '', '?repo=/url/repo')
    renderSettings()
    expect(screen.getByPlaceholderText('/path/to/repo')).toHaveValue('/url/repo')
  })
})

// ---------------------------------------------------------------------------
// localStorage persistence
// ---------------------------------------------------------------------------

describe('localStorage persistence', () => {
  it('persists repo to localStorage with key primpact_repo on every change', async () => {
    const user = userEvent.setup()
    const setSpy = vi.spyOn(Storage.prototype, 'setItem')
    renderSettings()

    await user.type(screen.getByPlaceholderText('/path/to/repo'), '/a')

    // Two keystrokes ('/','a') → two setItem calls
    expect(setSpy).toHaveBeenCalledTimes(2)
    expect(setSpy).toHaveBeenLastCalledWith(REPO_KEY, '/a')
  })

  it('writes the current value to localStorage on every input change', async () => {
    const user = userEvent.setup()
    const setSpy = vi.spyOn(Storage.prototype, 'setItem')
    renderSettings()

    await user.type(screen.getByPlaceholderText('/path/to/repo'), '/my/repo')

    expect(setSpy).toHaveBeenLastCalledWith(REPO_KEY, '/my/repo')
    // Called once per keystroke
    expect(setSpy).toHaveBeenCalledTimes('/my/repo'.length)
  })
})

// ---------------------------------------------------------------------------
// Config query behaviour
// ---------------------------------------------------------------------------

describe('config query', () => {
  it('does not call getConfig when repo is whitespace-only', async () => {
    const user = userEvent.setup()
    renderSettings()

    await user.type(screen.getByPlaceholderText('/path/to/repo'), '   ')

    expect(api.getConfig).not.toHaveBeenCalled()
  })

  it('calls getConfig with current repo value when it is non-empty', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getConfig).mockResolvedValue(EMPTY_CONFIG)
    renderSettings()

    await user.type(screen.getByPlaceholderText('/path/to/repo'), '/repo')

    await waitFor(() => expect(api.getConfig).toHaveBeenCalledWith('/repo'))
  })

  it('refetches config when repo value changes in state', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getConfig).mockResolvedValue(EMPTY_CONFIG)
    renderSettings()
    const input = screen.getByPlaceholderText('/path/to/repo')

    await user.type(input, '/first')
    await waitFor(() => expect(api.getConfig).toHaveBeenCalledWith('/first'))

    await user.clear(input)
    await user.type(input, '/second')
    await waitFor(() => expect(api.getConfig).toHaveBeenCalledWith('/second'))
  })
})

// ---------------------------------------------------------------------------
// Conditional rendering
// ---------------------------------------------------------------------------

describe('conditional rendering', () => {
  it('shows empty-repo prompt when repo trims to empty string', async () => {
    const user = userEvent.setup()
    renderSettings()

    await user.type(screen.getByPlaceholderText('/path/to/repo'), '   ')

    expect(screen.getByText(/Enter a repository path above/)).toBeInTheDocument()
  })

  it('shows error state when repo is non-empty but config fetch fails', async () => {
    // Pre-set via localStorage so the query fires on mount without typing delays
    localStorage.setItem(REPO_KEY, '/some/repo')
    vi.mocked(api.getConfig).mockRejectedValue(new Error('HTTP 404'))
    renderSettings()

    // The Starter Template section only renders in the error/no-config state
    await screen.findByRole('heading', { name: 'Starter Template' }, { timeout: 3000 })
  })
})

// ---------------------------------------------------------------------------
// Clear History button
// ---------------------------------------------------------------------------

describe('Clear History button', () => {
  it('is disabled when repo trims to empty string', async () => {
    const user = userEvent.setup()
    renderSettings()

    await user.type(screen.getByPlaceholderText('/path/to/repo'), '   ')

    expect(screen.getByRole('button', { name: 'Clear History' })).toBeDisabled()
  })

  it('is enabled when repo is non-empty', async () => {
    const user = userEvent.setup()
    renderSettings()

    await user.type(screen.getByPlaceholderText('/path/to/repo'), '/repo')

    expect(screen.getByRole('button', { name: 'Clear History' })).not.toBeDisabled()
  })

  it('calls clearHistory with current React state value, not the initial URL param', async () => {
    window.history.pushState({}, '', '?repo=/url/repo')
    const user = userEvent.setup()
    renderSettings()
    const input = screen.getByPlaceholderText('/path/to/repo')

    // Change the repo away from the URL-param value
    await user.clear(input)
    await user.type(input, '/changed/repo')

    // Open modal
    await user.click(screen.getByRole('button', { name: 'Clear History' }))
    // Modal renders before the danger-zone button in the DOM, so [0] is the modal confirm
    const confirmButtons = screen.getAllByRole('button', { name: 'Clear History' })
    await user.click(confirmButtons[0])

    await waitFor(() =>
      expect(api.clearHistory).toHaveBeenCalledWith('/changed/repo')
    )
    expect(api.clearHistory).not.toHaveBeenCalledWith('/url/repo')
  })
})
