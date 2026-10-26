import { useEffect, useState } from 'react'
import { RefreshCw, Shield } from 'lucide-react'

export default function Governance() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const load = async () => {
    setError('')
    try { const response = await fetch('/api/governance/overview'); if (!response.ok) throw new Error('Could not load persisted governance records.'); setData(await response.json()) } catch (err) { setError(err.message) }
  }
  useEffect(() => { load() }, [])
  return <div className="mx-auto max-w-7xl space-y-6 p-5 sm:p-8">
    <header className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-blue-700">Lifecycle and verification</p><h1 className="mt-1 text-3xl font-bold">Governance</h1><p className="mt-2 text-slate-600">Persisted rule lifecycle records and their source verification state.</p></div><button type="button" onClick={load} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 text-sm"><RefreshCw size={16} />Refresh</button></header>
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm leading-5 text-amber-950">The app does not authenticate reviewer identity. This page reports stored lifecycle data only; it does not create approvals or activate rules.</div>
    {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {!data && !error && <p className="py-12 text-center text-sm text-slate-500">Loading governance records…</p>}
    {data && !data.storage_available && <div className="rounded-xl border border-slate-200 bg-white p-10 text-center"><Shield className="mx-auto text-slate-400" size={30} /><h2 className="mt-3 font-semibold">Rule lifecycle store unavailable</h2><p className="mt-2 text-sm text-slate-600">No persisted verified-rules store is present. Seed/demo rules are not displayed as bank rules.</p></div>}
    {data?.storage_available && data.rules.length === 0 && <div className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-600">The lifecycle store exists but contains no rules.</div>}
    {data?.rules?.length > 0 && <div className="space-y-3">{data.rules.map(rule => <article key={rule.rule_id} className="rounded-xl border border-slate-200 bg-white p-5"><div className="flex flex-wrap justify-between gap-3"><div><h2 className="font-semibold">{rule.rule_name || rule.rule_id}</h2><p className="mt-1 font-mono text-xs text-slate-500">{rule.rule_id} · version {rule.rule_version || 'unknown'}</p></div><span className="h-fit rounded-full bg-slate-100 px-2.5 py-1 text-xs">{rule.status || 'status unknown'}</span></div><p className="mt-3 text-sm text-slate-700">Verification: {rule.verification_status || rule.source_obligation?.verification_status || 'unknown'} · Source type: {rule.source_obligation?.dataset_kind || 'unknown'} · Evidence: {(rule.source_obligation?.evidence_ids || []).join(', ') || rule.source_obligation?.evidence_id || 'not recorded'}</p></article>)}</div>}
  </div>
}
