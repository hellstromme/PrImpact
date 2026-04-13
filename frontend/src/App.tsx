import { Routes, Route } from 'react-router-dom'
import { ActiveRunProvider } from './context/ActiveRunContext'
import Layout from './components/Layout'
import Dashboard from './screens/Dashboard'
import Report from './screens/Report'

export default function App() {
  return (
    <ActiveRunProvider>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs/:id" element={<Report />} />
          <Route path="/runs/:id/:tab" element={<Report />} />
        </Routes>
      </Layout>
    </ActiveRunProvider>
  )
}
