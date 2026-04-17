import type {
  AnalyseRequest,
  AnalysisStatusResponse,
  ImpactReport,
  PrImpactConfig,
  RunSummary,
  SignalAnnotation,
  SignalAnnotationMap,
  SnippetResponse,
} from './types'

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  getRuns: (repo: string, limit = 50, offset = 0): Promise<RunSummary[]> =>
    fetch(`/api/runs?repo=${encodeURIComponent(repo)}&limit=${limit}&offset=${offset}`).then(
      (r) => _json<RunSummary[]>(r)
    ),

  getRun: (id: string): Promise<RunSummary> =>
    fetch(`/api/runs/${id}`).then((r) => _json<RunSummary>(r)),

  getReport: (id: string): Promise<ImpactReport> =>
    fetch(`/api/runs/${id}/report`).then((r) => _json<ImpactReport>(r)),

  postAnalyse: (body: AnalyseRequest): Promise<{ run_id: string; status: string }> =>
    fetch('/api/analyse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => _json<{ run_id: string; status: string }>(r)),

  getStatus: (runId: string): Promise<AnalysisStatusResponse> =>
    fetch(`/api/analyse/${runId}/status`).then((r) => _json<AnalysisStatusResponse>(r)),

  getSnippet: (runId: string, file: string, line: number, context = 5): Promise<SnippetResponse> =>
    fetch(
      `/api/runs/${runId}/snippet?file=${encodeURIComponent(file)}&line=${line}&context=${context}`
    ).then((r) => _json<SnippetResponse>(r)),

  getConfig: (repo: string): Promise<PrImpactConfig & { path: string }> =>
    fetch(`/api/config?repo=${encodeURIComponent(repo)}`).then((r) =>
      _json<PrImpactConfig & { path: string }>(r)
    ),

  clearHistory: (repo: string): Promise<{ deleted: boolean }> =>
    fetch(`/api/history?repo=${encodeURIComponent(repo)}`, { method: 'DELETE' }).then((r) =>
      _json<{ deleted: boolean }>(r)
    ),

  getAnnotations: (runId: string): Promise<SignalAnnotationMap> =>
    fetch(`/api/runs/${runId}/annotations`).then((r) => _json<SignalAnnotationMap>(r)),

  saveAnnotation: (
    runId: string,
    signalKey: string,
    body: { muted?: boolean; mute_reason?: string | null; assigned_to?: string | null },
  ): Promise<SignalAnnotation> =>
    fetch(`/api/runs/${runId}/annotations/${signalKey}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => _json<SignalAnnotation>(r)),
}
