import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './screens/Dashboard'
import History from './screens/History'
import Report from './screens/Report'
import Settings from './screens/Settings'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/history" element={<History />} />
        <Route path="/runs/:id" element={<Navigate to="summary" replace />} />
        <Route path="/runs/:id/:tab" element={<Report />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Layout>
  )
}
