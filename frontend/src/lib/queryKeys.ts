export const queryKeys = {
  runs: (repo: string) => ['runs', repo] as const,
  run: (id: string) => ['run', id] as const,
  report: (id: string) => ['report', id] as const,
  status: (id: string) => ['status', id] as const,
  snippet: (runId: string, file: string, line: number) =>
    ['snippet', runId, file, line] as const,
}
