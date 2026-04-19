import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import SecurityTab from './SecurityTab'
import { api } from '../../lib/api'
import type { ImpactReport, SignalAnnotation } from '../../lib/types'

vi.mock('../../lib/api', () => ({
  api: {
    getAnnotations: vi.fn(),
    getSnippet: vi.fn(),
    saveAnnotation: vi.fn(),
  },
}))

vi.mock('../../components/CodeBlock', () => ({
  default: () => null,
}))

const SIGNAL_KEY = 'abc123def456abc1'
const RUN_ID = 'run-test-001'

function makeReport(overrides?: Partial<ImpactReport>): ImpactReport {
  return {
    pr_title: 'test pr',
    base_sha: 'abc',
    head_sha: 'def',
    changed_files: [],
    blast_radius: [],
    interface_changes: [],
    ai_analysis: {
      summary: '',
      decisions: [],
      assumptions: [],
      anomalies: [],
      test_gaps: [],
      security_signals: [
        {
          description: 'Shell invoke detected',
          location: { file: 'src/run.py', line: 5, symbol: null },
          signal_type: 'shell_invoke',
          severity: 'high',
          why_unusual: 'No prior calls',
          suggested_action: 'Review',
          signal_key: SIGNAL_KEY,
        },
      ],
      semantic_verdicts: [],
    },
    dependency_issues: [],
    historical_hotspots: [],
    blast_graph: null,
    ...overrides,
  }
}

function makeAnnotation(overrides?: Partial<SignalAnnotation>): SignalAnnotation {
  return {
    signal_key: SIGNAL_KEY,
    muted: false,
    mute_reason: null,
    assigned_to: null,
    updated_at: '2024-01-01T00:00:00Z',
    muted_by: null,
    assigned_by: null,
    ...overrides,
  }
}

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.mocked(api.getAnnotations).mockResolvedValue({})
  vi.mocked(api.getSnippet).mockRejectedValue(new Error('no snippet'))
})

afterEach(() => {
  vi.resetAllMocks()
})

// ─── Mutation error handling ──────────────────────────────────────────────────

describe('SecurityTab — mutation error handling', () => {
  it('does not crash when saveAnnotation fails', async () => {
    const user = userEvent.setup()
    vi.mocked(api.saveAnnotation).mockRejectedValue(new Error('network error'))
    render(<SecurityTab report={makeReport()} runId={RUN_ID} />, { wrapper: makeWrapper() })

    await user.click(screen.getByText('Shell invoke detected'))
    await user.click(await screen.findByRole('button', { name: /mute signal/i }))
    await user.click(screen.getByRole('button', { name: /^mute$/i }))

    await waitFor(() => {
      // Text appears in both list and detail panel — component is still mounted
      expect(screen.getAllByText('Shell invoke detected').length).toBeGreaterThan(0)
    })
  })
})

// ─── Query invalidation on success ───────────────────────────────────────────

describe('SecurityTab — query invalidation on success', () => {
  it('refetches annotations after a successful save', async () => {
    const user = userEvent.setup()
    vi.mocked(api.saveAnnotation).mockResolvedValue(makeAnnotation({ muted: true }))
    render(<SecurityTab report={makeReport()} runId={RUN_ID} />, { wrapper: makeWrapper() })

    await user.click(screen.getByText('Shell invoke detected'))
    await user.click(await screen.findByRole('button', { name: /mute signal/i }))
    await user.click(screen.getByRole('button', { name: /^mute$/i }))

    await waitFor(() => {
      expect(api.getAnnotations).toHaveBeenCalledTimes(2)
    })
  })
})

// ─── signal_key visibility guard ─────────────────────────────────────────────

describe('SecurityTab — signal_key visibility', () => {
  it('hides mute and assign buttons when signal_key is empty string', async () => {
    const user = userEvent.setup()
    const report = makeReport({
      ai_analysis: {
        summary: '',
        decisions: [],
        assumptions: [],
        anomalies: [],
        test_gaps: [],
        security_signals: [
          {
            description: 'No key signal',
            location: { file: 'src/a.py', line: null, symbol: null },
            signal_type: 'shell_invoke',
            severity: 'high',
            why_unusual: '',
            suggested_action: '',
            signal_key: '',
          },
        ],
        semantic_verdicts: [],
      },
    })
    render(<SecurityTab report={report} runId={RUN_ID} />, { wrapper: makeWrapper() })

    await user.click(screen.getByText('No key signal'))

    expect(screen.queryByRole('button', { name: /mute signal/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /assign reviewer/i })).not.toBeInTheDocument()
  })
})

// ─── AssignForm — disabled for whitespace ─────────────────────────────────────

describe('SecurityTab — AssignForm disabled for whitespace', () => {
  it('Assign button is disabled when name contains only whitespace', async () => {
    const user = userEvent.setup()
    render(<SecurityTab report={makeReport()} runId={RUN_ID} />, { wrapper: makeWrapper() })

    await user.click(screen.getByText('Shell invoke detected'))
    await user.click(await screen.findByRole('button', { name: /assign reviewer/i }))

    const input = screen.getByPlaceholderText(/reviewer name/i)
    await user.type(input, '   ')

    expect(screen.getByRole('button', { name: /^assign$/i })).toBeDisabled()
  })
})

// ─── Clear sends empty string ─────────────────────────────────────────────────

describe('SecurityTab — clear assignee', () => {
  it('calls saveAnnotation with assigned_to="" when clear is clicked', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getAnnotations).mockResolvedValue({
      [SIGNAL_KEY]: makeAnnotation({ assigned_to: 'bob' }),
    })
    vi.mocked(api.saveAnnotation).mockResolvedValue(makeAnnotation({ assigned_to: null }))
    render(<SecurityTab report={makeReport()} runId={RUN_ID} />, { wrapper: makeWrapper() })

    await user.click(screen.getByText('Shell invoke detected'))
    await user.click(await screen.findByRole('button', { name: /clear/i }))

    await waitFor(() => {
      expect(api.saveAnnotation).toHaveBeenCalledWith(
        RUN_ID,
        SIGNAL_KEY,
        expect.objectContaining({ assigned_to: '' }),
      )
    })
  })
})

// ─── Escape key closes forms ──────────────────────────────────────────────────

describe('SecurityTab — escape key closes forms', () => {
  it('pressing Escape in MuteForm closes the form', async () => {
    const user = userEvent.setup()
    render(<SecurityTab report={makeReport()} runId={RUN_ID} />, { wrapper: makeWrapper() })

    await user.click(screen.getByText('Shell invoke detected'))
    await user.click(await screen.findByRole('button', { name: /mute signal/i }))

    expect(screen.getByPlaceholderText(/reason \(optional\)/i)).toBeInTheDocument()

    await user.keyboard('{Escape}')

    expect(screen.queryByPlaceholderText(/reason \(optional\)/i)).not.toBeInTheDocument()
  })

  it('pressing Escape in AssignForm closes the form', async () => {
    const user = userEvent.setup()
    render(<SecurityTab report={makeReport()} runId={RUN_ID} />, { wrapper: makeWrapper() })

    await user.click(screen.getByText('Shell invoke detected'))
    await user.click(await screen.findByRole('button', { name: /assign reviewer/i }))

    expect(screen.getByPlaceholderText(/reviewer name/i)).toBeInTheDocument()

    await user.keyboard('{Escape}')

    expect(screen.queryByPlaceholderText(/reviewer name/i)).not.toBeInTheDocument()
  })
})
