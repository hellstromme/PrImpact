import { useCallback, useEffect, useMemo } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react'
import dagre from '@dagrejs/dagre'
import '@xyflow/react/dist/style.css'
import type { BlastGraph } from '../lib/types'

// ─── Node dimensions ────────────────────────────────────────────────────────

const NODE_W = 220
const NODE_H = 56

// ─── Dagre layout ───────────────────────────────────────────────────────────

function applyDagreLayout(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', ranksep: 90, nodesep: 48, marginx: 32, marginy: 32 })

  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }))
  edges.forEach((e) => g.setEdge(e.source, e.target))

  dagre.layout(g)

  return nodes.map((n) => {
    const pos = g.node(n.id)
    return { ...n, position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 } }
  })
}

// ─── Custom node ────────────────────────────────────────────────────────────

type NodeData = {
  label: string
  fullPath: string
  nodeType: 'changed' | 'affected'
  distance: number
  churnScore: number | null
  symbols: string[]
}

function BlastNode({ data }: NodeProps<Node<NodeData>>) {
  const { nodeType, distance, label, churnScore, fullPath } = data

  const borderCls =
    nodeType === 'changed'
      ? 'border-[#fe554d] bg-[#fe554d]/10'
      : distance === 1
        ? 'border-[#34a547] bg-[#34a547]/10'
        : distance === 2
          ? 'border-[#bd8708] bg-[#bd8708]/10'
          : 'border-[#3e4a3d] bg-[#1c2026]'

  const labelCls =
    nodeType === 'changed'
      ? 'text-[#ffb4ac]'
      : distance === 1
        ? 'text-[#6fdd78]'
        : distance === 2
          ? 'text-[#fabc45]'
          : 'text-[#becab9]'

  const badge =
    nodeType === 'changed' ? 'CHANGED' : `D${distance}`

  const badgeCls =
    nodeType === 'changed'
      ? 'bg-[#fe554d]/20 text-[#ffb4ac] border border-[#fe554d]/30'
      : distance === 1
        ? 'bg-[#34a547]/20 text-[#6fdd78]'
        : distance === 2
          ? 'bg-[#bd8708]/20 text-[#fabc45]'
          : 'bg-[#3e4a3d]/30 text-[#becab9]'

  return (
    <div
      className={`flex flex-col justify-center px-3 border rounded-sm ${borderCls} group relative`}
      style={{ width: NODE_W, height: NODE_H }}
      title={fullPath}
    >
      <Handle type="target" position={Position.Left} className="!border-0 !bg-transparent !w-0 !h-0" />
      <div className="flex items-center justify-between gap-2 min-w-0">
        <span
          className={`font-mono text-[11px] truncate leading-tight ${labelCls}`}
          style={{ maxWidth: NODE_W - 72 }}
        >
          {label}
        </span>
        <span
          className={`shrink-0 text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-[2px] ${badgeCls}`}
        >
          {badge}
        </span>
      </div>
      {churnScore != null && (
        <div className="text-[9px] text-[#889484] mt-0.5 font-mono">
          churn {Math.round(churnScore)}
        </div>
      )}
      {/* Tooltip */}
      <div className="absolute left-full top-1/2 -translate-y-1/2 ml-2 z-50 hidden group-hover:block pointer-events-none">
        <div className="bg-[#0a0e14] border border-[#3e4a3d] rounded-sm p-2 text-[10px] font-mono text-[#dfe2eb] whitespace-nowrap shadow-xl max-w-xs">
          <div className="text-[#becab9] mb-1 break-all">{fullPath}</div>
          {data.symbols.length > 0 && (
            <div className="text-[#6fdd78]">
              imports: {data.symbols.slice(0, 5).join(', ')}
              {data.symbols.length > 5 && ` +${data.symbols.length - 5}`}
            </div>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!border-0 !bg-transparent !w-0 !h-0" />
    </div>
  )
}

const nodeTypes = { blast: BlastNode }

// ─── Edge style by distance ─────────────────────────────────────────────────

function edgeColor(targetDistance: number): string {
  if (targetDistance === 1) return '#34a547'
  if (targetDistance === 2) return '#bd8708'
  return '#3e4a3d'
}

// ─── Inner graph (needs ReactFlowProvider context) ───────────────────────────

function GraphInner({ graph }: { graph: BlastGraph }) {
  const distanceMap = useMemo(() => {
    const m: Record<string, number> = {}
    graph.nodes.forEach((n) => { m[n.id] = n.distance })
    return m
  }, [graph])

  // Build RF nodes
  const initialNodes: Node<NodeData>[] = useMemo(
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
          symbols: [],
        },
      })),
    [graph],
  )

  // Build RF edges
  const initialEdges: Edge[] = useMemo(
    () =>
      graph.edges.map((e, i) => {
        const dist = distanceMap[e.target] ?? 1
        const color = edgeColor(dist)
        return {
          id: `e-${i}`,
          source: e.source,
          target: e.target,
          animated: false,
          style: { stroke: color, strokeWidth: 1.5 },
          markerEnd: {
            type: 'arrowclosed' as const,
            color,
            width: 12,
            height: 12,
          },
        }
      }),
    [graph, distanceMap],
  )

  // Attach symbols to node data from edges
  const nodesWithSymbols = useMemo(() => {
    const symMap: Record<string, string[]> = {}
    graph.edges.forEach((e) => {
      if (e.symbols.length > 0) {
        symMap[e.target] = (symMap[e.target] ?? []).concat(e.symbols)
      }
    })
    return initialNodes.map((n) => ({
      ...n,
      data: { ...n.data, symbols: symMap[n.id] ?? [] },
    }))
  }, [initialNodes, graph])

  const layoutNodes = useMemo(
    () => applyDagreLayout(nodesWithSymbols, initialEdges),
    [nodesWithSymbols, initialEdges],
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes)
  const [edges, , onEdgesChange] = useEdgesState(initialEdges)
  const { fitView } = useReactFlow()

  useEffect(() => {
    setNodes(layoutNodes)
  }, [layoutNodes, setNodes])

  const onInit = useCallback(() => {
    setTimeout(() => fitView({ padding: 0.15, duration: 300 }), 50)
  }, [fitView])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={nodeTypes}
      onInit={onInit}
      fitView
      minZoom={0.2}
      maxZoom={2}
      style={{ background: '#10141a' }}
      proOptions={{ hideAttribution: true }}
    >
      <Background color="#3e4a3d" gap={20} size={0.5} />
      <Controls
        style={{ background: '#181c22', border: '1px solid #3e4a3d' }}
        showInteractive={false}
      />
    </ReactFlow>
  )
}

// ─── Legend ──────────────────────────────────────────────────────────────────

function Legend() {
  const items = [
    { color: '#fe554d', label: 'Changed' },
    { color: '#34a547', label: 'D1 — direct import' },
    { color: '#bd8708', label: 'D2 — transitive' },
    { color: '#3e4a3d', label: 'D3 — deep' },
  ]
  return (
    <div className="flex gap-4 flex-wrap">
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-1.5">
          <div
            className="w-2.5 h-2.5 rounded-[2px] border"
            style={{ background: `${item.color}20`, borderColor: item.color }}
          />
          <span className="text-[10px] font-mono text-on-surface-variant">{item.label}</span>
        </div>
      ))}
    </div>
  )
}

// ─── Public component ────────────────────────────────────────────────────────

export default function BlastRadiusGraph({ graph }: { graph: BlastGraph }) {
  if (graph.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-on-surface-variant text-sm font-mono">
        No dependency graph data available.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <Legend />
      <div
        className="rounded-lg border border-outline-variant/10 overflow-hidden"
        style={{ height: Math.min(Math.max(graph.nodes.length * 72, 320), 600) }}
      >
        <ReactFlowProvider>
          <GraphInner graph={graph} />
        </ReactFlowProvider>
      </div>
      <p className="text-[10px] text-on-surface-variant font-mono">
        Arrows point from changed files outward to affected dependents. Drag to pan, scroll to zoom.
      </p>
    </div>
  )
}
