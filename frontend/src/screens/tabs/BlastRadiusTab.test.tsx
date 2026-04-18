import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import BlastRadiusTab from './BlastRadiusTab'
import type { ImpactReport } from '../../lib/types'

vi.mock('../../components/BlastRadiusGraph', () => ({
  default: () => <div data-testid="blast-radius-graph" />,
}))
vi.mock('../../components/SparkLine', () => ({
  default: () => null,
}))

const VIEW_KEY = 'primpact.blastRadiusView'

function makeReport(overrides?: Partial<ImpactReport>): ImpactReport {
  return {
    pr_title: 'test pr',
    base_sha: 'abc',
    head_sha: 'def',
    changed_files: [],
    blast_radius: [
      { path: 'src/a.py', distance: 1, imported_symbols: ['foo'], churn_score: 2.5 },
    ],
    interface_changes: [],
    ai_analysis: {
      summary: '',
      decisions: [],
      assumptions: [],
      anomalies: [],
      test_gaps: [],
      security_signals: [],
      semantic_verdicts: [],
    },
    dependency_issues: [],
    historical_hotspots: [],
    blast_graph: {
      nodes: [{ id: 'src/a.py', path: 'src/a.py', type: 'changed', distance: 0, language: null, churn_score: null }],
      edges: [],
    },
    ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

// ─── View switching ───────────────────────────────────────────────────────────

describe('BlastRadiusTab — view switching', () => {
  it('renders table view by default', () => {
    render(<BlastRadiusTab report={makeReport()} />)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByTestId('blast-radius-graph')).not.toBeInTheDocument()
  })

  it('switches to graph view when the graph button is clicked', async () => {
    const user = userEvent.setup()
    render(<BlastRadiusTab report={makeReport()} />)
    await user.click(screen.getByRole('button', { name: /graph/i }))
    expect(screen.getByTestId('blast-radius-graph')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('switches back to table view from graph view', async () => {
    const user = userEvent.setup()
    render(<BlastRadiusTab report={makeReport()} />)
    await user.click(screen.getByRole('button', { name: /graph/i }))
    await user.click(screen.getByRole('button', { name: /table/i }))
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByTestId('blast-radius-graph')).not.toBeInTheDocument()
  })
})

// ─── localStorage persistence ─────────────────────────────────────────────────

describe('BlastRadiusTab — localStorage persistence', () => {
  it('reads stored view preference on mount', () => {
    localStorage.setItem(VIEW_KEY, 'graph')
    render(<BlastRadiusTab report={makeReport()} />)
    expect(screen.getByTestId('blast-radius-graph')).toBeInTheDocument()
  })

  it('writes the selected view to localStorage when switching', async () => {
    const user = userEvent.setup()
    render(<BlastRadiusTab report={makeReport()} />)
    await user.click(screen.getByRole('button', { name: /graph/i }))
    expect(localStorage.getItem(VIEW_KEY)).toBe('graph')
  })

  it('defaults to table view when stored value is unrecognised', () => {
    localStorage.setItem(VIEW_KEY, 'unknown')
    render(<BlastRadiusTab report={makeReport()} />)
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('defaults to table view when localStorage.getItem throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })
    render(<BlastRadiusTab report={makeReport()} />)
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('does not throw when localStorage.setItem throws during view switch', async () => {
    const user = userEvent.setup()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })
    render(<BlastRadiusTab report={makeReport()} />)
    await expect(user.click(screen.getByRole('button', { name: /graph/i }))).resolves.not.toThrow()
  })
})

// ─── View toggle visibility ───────────────────────────────────────────────────

describe('BlastRadiusTab — view toggle visibility', () => {
  it('does not show view toggle buttons when blast_graph is null', () => {
    render(<BlastRadiusTab report={makeReport({ blast_graph: null })} />)
    expect(screen.queryByRole('button', { name: /graph/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /table/i })).not.toBeInTheDocument()
  })

  it('does not show view toggle buttons when blast_graph has no nodes', () => {
    render(<BlastRadiusTab report={makeReport({ blast_graph: { nodes: [], edges: [] } })} />)
    expect(screen.queryByRole('button', { name: /graph/i })).not.toBeInTheDocument()
  })

  it('shows view toggle buttons when blast_graph has nodes', () => {
    render(<BlastRadiusTab report={makeReport()} />)
    expect(screen.getByRole('button', { name: /graph/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /table/i })).toBeInTheDocument()
  })
})

// ─── Graph rendering ──────────────────────────────────────────────────────────

describe('BlastRadiusTab — graph rendering', () => {
  it('renders BlastRadiusGraph when view is graph and blast_graph is not null', async () => {
    const user = userEvent.setup()
    render(<BlastRadiusTab report={makeReport()} />)
    await user.click(screen.getByRole('button', { name: /graph/i }))
    expect(screen.getByTestId('blast-radius-graph')).toBeInTheDocument()
  })

  it('does not render BlastRadiusGraph when blast_graph is null even if view is graph in storage', () => {
    localStorage.setItem(VIEW_KEY, 'graph')
    render(<BlastRadiusTab report={makeReport({ blast_graph: null })} />)
    expect(screen.queryByTestId('blast-radius-graph')).not.toBeInTheDocument()
  })
})
