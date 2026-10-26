import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Documents from './pages/Documents'
import Evidence from './pages/Evidence'
import Candidates from './pages/Candidates'
import Governance from './pages/Governance'
import AuditTrail from './pages/AuditTrail'
import ProposedRuleAnalysis from './pages/ProposedRuleAnalysis'
import DocumentAnalyzer from './pages/DocumentAnalyzer'
import BankPolicies from './pages/BankPolicies'

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="documents" element={<Documents />} />
          <Route path="evidence" element={<Evidence />} />
          <Route path="phase5" element={<Navigate to="/candidates?tab=extract" replace />} />
          <Route path="candidates" element={<Candidates />} />
          <Route path="governance" element={<Governance />} />
          <Route path="audit" element={<AuditTrail />} />
          <Route path="proposed-rule" element={<ProposedRuleAnalysis />} />
          <Route path="document-analyzer" element={<DocumentAnalyzer />} />
          <Route path="bank-policies" element={<BankPolicies />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </Router>
  )
}

export default App
