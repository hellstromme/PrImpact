// TypeScript types mirroring pr_impact/models.py dataclasses.

export type Severity = 'high' | 'medium' | 'low'

export interface RunSummary {
  id: string
  repo_path: string
  pr_number: number | null
  pr_title: string | null
  base_sha: string
  head_sha: string
  created_at: string        // ISO 8601
  verdict: 'clean' | 'has_blockers' | null
  blast_radius_count: number
  anomaly_count: number
  signal_count: number
}

export interface SourceLocation {
  file: string
  line: number | null
  symbol: string | null
}

export interface ChangedSymbol {
  name: string
  kind: 'file' | 'function' | 'class' | 'import'
  change_type: string
  signature_before: string | null
  signature_after: string | null
  params: string[]
  decorators: string[]
  return_type: string | null
}

export interface ChangedFile {
  path: string
  language: string
  diff: string
  content_before: string
  content_after: string
  changed_symbols: ChangedSymbol[]
}

export interface BlastRadiusEntry {
  path: string
  distance: number
  imported_symbols: string[]
  churn_score: number | null
}

export interface InterfaceChange {
  file: string
  symbol: string
  before: string
  after: string
  callers: string[]
}

export interface Decision {
  description: string
  rationale: string
  risk: string
}

export interface Assumption {
  description: string
  location: string
  risk: string
}

export interface Anomaly {
  description: string
  location: string
  severity: Severity
}

export interface TestGap {
  behaviour: string
  location: string
  severity: Severity
  gap_type: string
}

export interface SecuritySignal {
  description: string
  location: SourceLocation
  signal_type: string
  severity: Severity
  why_unusual: string
  suggested_action: string
}

export interface DependencyIssue {
  package_name: string
  issue_type: 'typosquat' | 'version_change' | 'vulnerability'
  description: string
  severity: Severity
  license: string | null
}

export interface SemanticVerdict {
  file: string
  symbol: string
  verdict: 'equivalent' | 'risky' | 'normal'
  reason: string
}

export interface AIAnalysis {
  summary: string
  decisions: Decision[]
  assumptions: Assumption[]
  anomalies: Anomaly[]
  test_gaps: TestGap[]
  security_signals: SecuritySignal[]
  semantic_verdicts: SemanticVerdict[]
}

export interface HistoricalHotspot {
  file: string
  appearances: number
}

export interface VerdictBlocker {
  category: 'test_gap' | 'security_signal' | 'dependency_issue' | 'anomaly'
  description: string
  location: string
}

export interface Verdict {
  status: 'clean' | 'has_blockers'
  agent_should_continue: boolean
  rationale: string
  blockers: VerdictBlocker[]
}

export interface ImpactReport {
  pr_title: string
  base_sha: string
  head_sha: string
  changed_files: ChangedFile[]
  blast_radius: BlastRadiusEntry[]
  interface_changes: InterfaceChange[]
  ai_analysis: AIAnalysis
  dependency_issues: DependencyIssue[]
  historical_hotspots: HistoricalHotspot[]
  // verdict may be present when --verdict was used at analysis time
  verdict?: Verdict
}

// API request/response types

export interface AnalyseRequest {
  repo: string
  pr_number?: number
  base_sha?: string
  head_sha?: string
}

export type AnalysisStatus = 'pending' | 'complete' | 'failed'

export interface AnalysisStatusResponse {
  run_id: string
  status: AnalysisStatus
  error: string | null
}

export interface SnippetResponse {
  lines: string[]
  start_line: number
  highlight_line: number
  total_lines: number
}

// Team configuration types mirroring pr_impact/models.py PrImpactConfig

export interface SuppressedSignal {
  signal_type: string
  path_prefix: string
  reason: string
}

export interface PrImpactConfig {
  high_sensitivity_modules: string[]
  suppressed_signals: SuppressedSignal[]
  blast_radius_depth: Record<string, number>
  fail_on_severity: string | null
  anomaly_thresholds: Record<string, string>
}
