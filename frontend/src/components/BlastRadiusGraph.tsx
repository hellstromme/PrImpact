import { useEffect, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  MarkerType,
  type Node,
  type Edge,
  type NodeProps,
  type OnInit,
} from '@xyflow/react'
import dagre from '@dagrejs/dagre'
import type { BlastGraph } from '../lib/types'

// ─── Dimensions ─────────────────────────────────────────────────────────────

const NODE_W = 220
const NODE_H = 56

// ─── Dagre layout ────────────────────────────────────────────────────────────

function applyDagreLayout(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', ranksep: 90, nodesep: 48, marginx: 32, marginy: 32 })
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }))
  edges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return nodes.map((n) => {
    const p = g.node(n.id)
    return { ...n, position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 } }
  })
}

// ─── Custom node ─────────────────────────────────────────────────────────────

type NodeData = {
  label: string
  fullPath: string
  nodeType: 'changed' | 'affected'
  distance: number
  churnScore: number | null
  symbols: string[]
}

const colorMap = {
  changed: { border: '#fe554d', bg: 'rgba(254,85,77,0.1)', text: '#ffb4ac', badge: 'rgba(254,85,77,0.2)' },
  d1:      { border: '#34a547', bg: 'rgba(52,165,71,0.1)',  text: '#6fdd78', badge: 'rgba(52,165,71,0.2)' },
  d2:      { border: '#bd8708', bg: 'rgba(189,135,8,0.1)',  text: '#fabc45', badge: 'rgba(189,135,8,0.2)' },
  d3:      { border: '#3e4a3d', bg: 'rgba(30,36,38,0.8)',   text: '#becab9', badge: 'rgba(62,74,61,0.3)' },
}

function getColors(nodeType: 'changed' | 'affected', distance: number) {
  if (nodeType === 'changed') return colorMap.changed
  if (distance === 1) return colorMap.d1
  if (distance === 2) return colorMap.d2
  return colorMap.d3
}

function BlastNode({ data }: NodeProps<Node<NodeData>>) {
  const { nodeType, distance, label, churnScore, fullPath, symbols } = data
  const c = getColors(nodeType, distance)
  const badge = nodeType === 'changed' ? 'CHANGED' : `D${distance}`

  return (
    <div
      style={{
        width: NODE_W,
        height: NODE_H,
        border: `1px solid ${c.border}`,
        background: c.bg,
        borderRadius: 2,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        padding: '0 12px',
        position: 'relative',
        boxSizing: 'border-box',
      }}
      title={`${fullPath}${symbols.length ? '\nimports: ' + symbols.slice(0, 5).join(', ') : ''}`}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0, width: 1, height: 1 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <span
          style={{
            fontFamily: 'monospace',
            fontSize: 11,
            color: c.text,
            flex: 1,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {label}
        </span>
        <span
          style={{
            flexShrink: 0,
            fontSize: 9,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            padding: '2px 6px',
            borderRadius: 2,
            background: c.badge,
            color: c.text,
            border: `1px solid ${c.border}40`,
          }}
        >
          {badge}
        </span>
      </div>
      {churnScore != null && (
        <div style={{ fontFamily: 'monospace', fontSize: 9, color: '#889484', marginTop: 2 }}>
          churn {Math.round(churnScore)}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ opacity: 0, width: 1, height: 1 }} />
    </div>
  )
}

const NODE_TYPES = { blast: BlastNode }

// ─── Legend ──────────────────────────────────────────────────────────────────

function Legend() {
  const items = [
    { color: colorMap.changed.border, label: 'Changed' },
    { color: colorMap.d1.border,      label: 'D1 — direct' },
    { color: colorMap.d2.border,      label: 'D2 — transitive' },
    { color: colorMap.d3.border,      label: 'D3 — deep' },
  ]
  return (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 8 }}>
      {items.map((item) => (
        <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 10, height: 10, borderRadius: 2,
            background: `${item.color}20`, border: `1px solid ${item.color}`,
          }} />
          <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#becab9' }}>{item.label}</span>
        </div>
      ))}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function BlastRadiusGraph({ graph }: { graph: BlastGraph }) {
  // All hooks must be called unconditionally before any early return (Rules of Hooks).
  const containerH = Math.min(Math.max(graph.nodes.length * 80, 320), 600)

  const symMap = useMemo(() => {
    const m: Record<string, string[]> = {}
    graph.edges.forEach((e) => {
      if (e.symbols.length > 0) m[e.target] = (m[e.target] ?? []).concat(e.symbols)
    })
    return m
  }, [graph])

  const rawNodes: Node<NodeData>[] = useMemo(
    () =>
      graph.nodes.map((n) => ({
        id: n.id,
        type: 'blast',
        position: { x: 0, y: 0 },
        data: {
          label: n.path.split('/').pop() ?? n.path,
          fullPath: n.path,
          nodeType: n.type,
          distance: n.distance,
          churnScore: n.churn_score,
          symbols: symMap[n.id] ?? [],
        },
      })),
    [graph, symMap],
  )

  const rawEdges: Edge[] = useMemo(
    () =>
      graph.edges.map((e, i) => {
        const targetNode = graph.nodes.find((n) => n.id === e.target)
        const dist = targetNode?.distance ?? 1
        const c = getColors('affected', dist)
        return {
          id: `e${i}`,
          source: e.source,
          target: e.target,
          style: { stroke: c.border, strokeWidth: 1.5 },
          markerEnd: { type: MarkerType.ArrowClosed, color: c.border, width: 14, height: 14 },
        }
      }),
    [graph],
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(applyDagreLayout(rawNodes, rawEdges))
  const [edges, setEdges, onEdgesChange] = useEdgesState(rawEdges)

  // Sync React Flow state when the graph prop changes (e.g. navigating between runs).
  useEffect(() => {
    setNodes(applyDagreLayout(rawNodes, rawEdges))
    setEdges(rawEdges)
  }, [rawNodes, rawEdges, setNodes, setEdges])

  const onInit: OnInit = (instance) => {
    instance.fitView({ padding: 0.12, duration: 300 })
  }

  if (graph.nodes.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 240, fontFamily: 'monospace', fontSize: 13, color: '#becab9' }}>
        No dependency graph data available.
      </div>
    )
  }

  return (
    <div>
      <Legend />
      <div style={{ width: '100%', height: containerH, borderRadius: 4, border: '1px solid #3e4a3d', overflow: 'hidden' }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={NODE_TYPES}
          onInit={onInit}
          fitView
          minZoom={0.15}
          maxZoom={2}
          style={{ width: '100%', height: '100%', background: '#10141a' }}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#3e4a3d" gap={24} size={0.5} />
          <Controls style={{ background: '#181c22', border: '1px solid #3e4a3d' }} showInteractive={false} />
        </ReactFlow>
      </div>
      <p style={{ fontFamily: 'monospace', fontSize: 10, color: '#889484', marginTop: 6 }}>
        Arrows point from changed files outward to affected dependents. Drag to pan · scroll to zoom · hover nodes for details.
      </p>
    </div>
  )
}
