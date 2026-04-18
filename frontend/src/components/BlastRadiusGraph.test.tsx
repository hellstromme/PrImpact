import { render, screen } from '@testing-library/react'
import BlastRadiusGraph from './BlastRadiusGraph'
import type { BlastGraph, GraphNode } from '../lib/types'

// ─── Hoisted mocks ────────────────────────────────────────────────────────────

const mockLayout   = vi.hoisted(() => vi.fn())
const mockDagreNode = vi.hoisted(() => vi.fn(() => ({ x: 100, y: 100 })))
const mockSetGraph  = vi.hoisted(() => vi.fn())
const mockFitView   = vi.hoisted(() => vi.fn())

vi.mock('@dagrejs/dagre', () => ({
  default: {
    graphlib: {
      Graph: class {
        setDefaultEdgeLabel = vi.fn()
        setGraph = mockSetGraph
        setNode = vi.fn()
        setEdge = vi.fn()
        node = mockDagreNode
      },
    },
    layout: mockLayout,
  },
}))

vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, nodeTypes, onInit }: any) => {
    onInit?.({ fitView: mockFitView })
    return (
      <div data-testid="reactflow">
        {nodes?.map((n: any) => {
          const Comp = nodeTypes?.[n.type]
          return Comp ? (
            <div key={n.id} data-testid={`node-wrapper-${n.id}`}>
              <Comp
                id={n.id}
                data={n.data}
                type={n.type}
                isConnectable
                selected={false}
                zIndex={0}
                dragging={false}
                xPos={0}
                yPos={0}
              />
            </div>
          ) : null
        })}
      </div>
    )
  },
  Background: () => null,
  Controls:   () => null,
  Handle: ({ type }: any) => <div data-testid={`handle-${type}`} />,
  Position:   { Left: 'left', Right: 'right' },
  useNodesState: (initial: any) => [initial, vi.fn(), vi.fn()],
  useEdgesState:  (initial: any) => [initial, vi.fn(), vi.fn()],
  MarkerType: { ArrowClosed: 'arrowclosed' },
}))

// ─── Helpers ─────────────────────────────────────────────────────────────────

function node(
  id: string,
  type: 'changed' | 'affected' = 'affected',
  distance = 1,
  churn_score: number | null = null,
): GraphNode {
  return { id, path: id, type, distance, language: 'python', churn_score }
}

function makeGraph(overrides?: Partial<BlastGraph>): BlastGraph {
  return {
    nodes: [
      node('src/a.py', 'changed', 0),
      node('src/b.py', 'affected', 1, 5.0),
    ],
    edges: [{ source: 'src/a.py', target: 'src/b.py', symbols: ['myFunc'] }],
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('BlastRadiusGraph — empty state', () => {
  it('renders empty message when graph has no nodes', () => {
    render(<BlastRadiusGraph graph={{ nodes: [], edges: [] }} />)
    expect(screen.getByText('No dependency graph data available.')).toBeInTheDocument()
  })

  it('does not render ReactFlow when graph has no nodes', () => {
    render(<BlastRadiusGraph graph={{ nodes: [], edges: [] }} />)
    expect(screen.queryByTestId('reactflow')).not.toBeInTheDocument()
  })
})

describe('BlastRadiusGraph — rendering with valid data', () => {
  it('renders ReactFlow when graph has nodes', () => {
    render(<BlastRadiusGraph graph={makeGraph()} />)
    expect(screen.getByTestId('reactflow')).toBeInTheDocument()
  })

  it('renders a node wrapper for each graph node', () => {
    render(<BlastRadiusGraph graph={makeGraph()} />)
    expect(screen.getByTestId('node-wrapper-src/a.py')).toBeInTheDocument()
    expect(screen.getByTestId('node-wrapper-src/b.py')).toBeInTheDocument()
  })
})

describe('BlastRadiusGraph — container height', () => {
  it('applies minimum height of 320 for a single node', () => {
    render(<BlastRadiusGraph graph={makeGraph({ nodes: [node('a.py', 'changed', 0)], edges: [] })} />)
    const container = screen.getByTestId('reactflow').parentElement!
    expect(container).toHaveStyle({ height: '320px' })
  })

  it('scales height proportionally for a mid-range node count', () => {
    const nodes = Array.from({ length: 5 }, (_, i) => node(`f${i}.py`))
    render(<BlastRadiusGraph graph={{ nodes, edges: [] }} />)
    // 5 * 80 = 400, within [320, 600]
    const container = screen.getByTestId('reactflow').parentElement!
    expect(container).toHaveStyle({ height: '400px' })
  })

  it('caps height at 600 for a large node count', () => {
    const nodes = Array.from({ length: 10 }, (_, i) => node(`f${i}.py`))
    render(<BlastRadiusGraph graph={{ nodes, edges: [] }} />)
    // 10 * 80 = 800 → capped at 600
    const container = screen.getByTestId('reactflow').parentElement!
    expect(container).toHaveStyle({ height: '600px' })
  })
})

describe('BlastNode — node type and distance badges', () => {
  it('renders CHANGED badge for a changed node', () => {
    render(<BlastRadiusGraph graph={makeGraph({ nodes: [node('a.py', 'changed', 0)], edges: [] })} />)
    expect(screen.getByText('CHANGED')).toBeInTheDocument()
  })

  it('renders D1 badge for distance-1 affected node', () => {
    render(<BlastRadiusGraph graph={makeGraph({ nodes: [node('b.py', 'affected', 1)], edges: [] })} />)
    expect(screen.getByText('D1')).toBeInTheDocument()
  })

  it('renders D2 badge for distance-2 affected node', () => {
    render(<BlastRadiusGraph graph={makeGraph({ nodes: [node('c.py', 'affected', 2)], edges: [] })} />)
    expect(screen.getByText('D2')).toBeInTheDocument()
  })

  it('renders D3 badge for distance-3 affected node', () => {
    render(<BlastRadiusGraph graph={makeGraph({ nodes: [node('d.py', 'affected', 3)], edges: [] })} />)
    expect(screen.getByText('D3')).toBeInTheDocument()
  })

  it('displays the filename (last path segment) as node label', () => {
    render(<BlastRadiusGraph graph={makeGraph()} />)
    expect(screen.getByText('a.py')).toBeInTheDocument()
    expect(screen.getByText('b.py')).toBeInTheDocument()
  })

  it('shows churn score when churn_score is non-null', () => {
    render(<BlastRadiusGraph graph={makeGraph()} />)
    // b.py has churn_score 5.0 → "churn 5"
    expect(screen.getByText(/churn 5/)).toBeInTheDocument()
  })

  it('does not render churn line when churn_score is null', () => {
    render(<BlastRadiusGraph graph={makeGraph({ nodes: [node('a.py', 'changed', 0, null)], edges: [] })} />)
    expect(screen.queryByText(/churn/)).not.toBeInTheDocument()
  })
})

describe('BlastNode — symbol tooltip', () => {
  it('includes full path and imports in title when symbols are present', () => {
    const { container } = render(<BlastRadiusGraph graph={makeGraph()} />)
    // b.py gets symbols ['myFunc'] from the edge
    const el = container.querySelector('[title*="src/b.py"]')
    expect(el).not.toBeNull()
    expect(el!.getAttribute('title')).toContain('imports: myFunc')
  })

  it('omits "imports:" from title when node has no incoming symbols', () => {
    const graph: BlastGraph = {
      nodes: [node('src/a.py', 'changed', 0)],
      edges: [],
    }
    const { container } = render(<BlastRadiusGraph graph={graph} />)
    const el = container.querySelector('[title*="imports:"]')
    expect(el).toBeNull()
  })
})

describe('BlastRadiusGraph — symbol map construction', () => {
  it('aggregates symbols from edges onto the target node title', () => {
    const graph: BlastGraph = {
      nodes: [
        node('src/util.py', 'changed', 0),
        node('src/consumer.py', 'affected', 1),
      ],
      edges: [{ source: 'src/util.py', target: 'src/consumer.py', symbols: ['helper', 'Config'] }],
    }
    const { container } = render(<BlastRadiusGraph graph={graph} />)
    const el = container.querySelector('[title*="imports: helper, Config"]')
    expect(el).not.toBeNull()
  })
})

describe('BlastRadiusGraph — dagre layout', () => {
  it('calls dagre.layout when the graph has nodes', () => {
    render(<BlastRadiusGraph graph={makeGraph()} />)
    expect(mockLayout).toHaveBeenCalled()
  })

  it('configures the dagre graph with left-to-right direction', () => {
    render(<BlastRadiusGraph graph={makeGraph()} />)
    expect(mockSetGraph).toHaveBeenCalledWith(
      expect.objectContaining({ rankdir: 'LR' })
    )
  })
})

describe('BlastRadiusGraph — ReactFlow initialisation', () => {
  it('calls fitView on ReactFlow init with correct options', () => {
    render(<BlastRadiusGraph graph={makeGraph()} />)
    expect(mockFitView).toHaveBeenCalledWith({ padding: 0.12, duration: 300 })
  })
})
