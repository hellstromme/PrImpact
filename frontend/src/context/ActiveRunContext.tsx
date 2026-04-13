import { createContext, useContext, useState } from 'react'

interface ActiveRunContextValue {
  runId: string | null
  setRunId: (id: string | null) => void
}

const ActiveRunContext = createContext<ActiveRunContextValue>({
  runId: null,
  setRunId: () => {},
})

export function ActiveRunProvider({ children }: { children: React.ReactNode }) {
  const [runId, setRunId] = useState<string | null>(null)
  return (
    <ActiveRunContext.Provider value={{ runId, setRunId }}>
      {children}
    </ActiveRunContext.Provider>
  )
}

export function useActiveRun() {
  return useContext(ActiveRunContext)
}
