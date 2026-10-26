import { useState } from 'react'

export default function DocumentAnalyzer() {
  const [ruleId, setRuleId] = useState('SYN-RULE-001')
  const [caseId, setCaseId] = useState('SYN-CASE-ANCHOR-001')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  async function verifySynthetic(event) {
    event.preventDefault()
    setBusy(true); setError(''); setResult(null)
    try {
      const response = await fetch('/api/phase10/synthetic/verify', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rule_id: ruleId, case_id: caseId }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload.detail || 'Synthetic verification is unavailable.')
      setResult(payload)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  return <section className="space-y-3 p-4 sm:p-6" aria-label="Analyzer and synthetic demonstrations">
    <header><p className="text-xs font-semibold uppercase tracking-wider text-blue-700">Secondary tool</p><h1 className="mt-1 text-2xl font-bold text-slate-900">Analyzer & synthetic demonstrations</h1><p className="mt-1 max-w-4xl text-sm leading-5 text-slate-600">This retained analyzer supports its existing document search and clearly labeled fictional scenarios. Uploaded files are temporary analysis inputs, not registered documents. Synthetic examples are separate from regulatory records, obligations, and proposed-rule review.</p></header>
    <section className="rounded-xl border border-amber-300 bg-white p-4" aria-label="Synthetic deterministic rule verification">
      <div><h2 className="font-semibold text-slate-900">Deterministic rule test (synthetic fixtures only)</h2><p className="mt-1 text-sm text-slate-600">OPA checks synthetic test eligibility, Z3 checks logical satisfiability, and the existing Python engine evaluates a fixed fixture case. These outputs do not establish legal validity, compliance, or a conflict. No records are saved and no rule is approved or activated.</p></div>
      <form onSubmit={verifySynthetic} className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <label className="text-xs font-medium text-slate-700">Synthetic rule ID<input required pattern="SYN-RULE-[0-9]{3}" maxLength={64} value={ruleId} onChange={event => setRuleId(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-300 px-3 text-sm" /></label>
        <label className="text-xs font-medium text-slate-700">Synthetic test case ID<input required pattern="SYN-CASE-[A-Z0-9-]+" maxLength={64} value={caseId} onChange={event => setCaseId(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-300 px-3 text-sm" /></label>
        <button disabled={busy} className="min-h-10 rounded-lg bg-blue-700 px-4 text-sm font-semibold text-white disabled:opacity-50">{busy ? 'Running test…' : 'Run synthetic test'}</button>
      </form>
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {result && <div role="status" className="mt-3 space-y-2 rounded-lg bg-slate-50 p-3 text-sm">
        <p className="font-semibold text-amber-900">{result.label}</p>
        <p>Rule {result.rule_id} · version {result.rule_version} · case {result.test_provenance.case_id}</p>
        <p>Schema: {result.validation.valid ? 'valid' : 'invalid'} · OPA/Rego synthetic gate: {result.opa_eligibility.allowed ? 'allowed' : 'blocked'} · Z3 satisfiability: {result.satisfiability.result}</p>
        <p>Python simulated outcome: {result.evaluation.matched ? 'condition matched' : 'condition did not match'} · action: {result.evaluation.action?.action_type || 'none'}</p>
        <p className="text-xs text-slate-600">{result.satisfiability.meaning} · {result.persistence} persisted · {result.approval_or_activation}</p>
      </div>}
    </section>
    <iframe title="TraceRule document analyzer and isolated synthetic demonstrations" src="/analyzer.html" className="h-[calc(100vh-10rem)] min-h-[640px] w-full rounded-xl border border-slate-200 bg-white" />
  </section>
}
