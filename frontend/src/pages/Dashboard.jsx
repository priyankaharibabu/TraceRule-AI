import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ArrowRight, ClipboardList, FileSearch, FileText, ShieldCheck } from 'lucide-react'

async function get(path) {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`Request failed: ${path}`)
  return response.json()
}

function Metric({ href, label, value, detail, icon: Icon, unavailable = false }) {
  const content = <div className="h-full rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-blue-300 hover:shadow-md">
    <div className="flex items-center justify-between"><span className="grid h-10 w-10 place-items-center rounded-lg bg-blue-50 text-blue-700"><Icon size={20} /></span><ArrowRight size={16} className="text-slate-400" /></div>
    <p className={`mt-4 text-3xl font-bold ${unavailable ? 'text-slate-400' : 'text-slate-900'}`}>{value}</p>
    <p className="mt-1 text-sm font-medium text-slate-700">{label}</p>
    <p className="mt-1 text-xs text-slate-500">{detail}</p>
  </div>
  return <Link to={href} className="block h-full rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2">{content}</Link>
}

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.allSettled([get('/api/documents'), get('/api/evidence'), get('/api/candidates'), get('/api/audit-trail'), get('/api/governance/overview')]).then(results => {
      if (!active) return
      const values = results.map(item => item.status === 'fulfilled' ? item.value : null)
      setData({ documents: values[0], evidence: values[1], candidates: values[2], audit: values[3], governance: values[4] })
      if (results.some(item => item.status === 'rejected')) setError('Some local data sources could not be loaded. Affected figures are shown as unavailable.')
    })
    return () => { active = false }
  }, [])

  const docs = data?.documents?.documents || []
  const passages = data?.evidence?.evidence || []
  const candidates = data?.candidates?.candidates || []
  const pending = candidates.filter(row => row.review_decision === 'pending').length
  const verified = candidates.filter(row => row.review_decision === 'verified').length
  const rejected = candidates.filter(row => row.review_decision === 'rejected').length
  const events = data?.audit?.events || []

  const metrics = [
    { href: '/documents', label: 'Registered source documents', value: data?.documents ? docs.length : 'Unavailable', detail: data?.documents ? 'From the local document registry' : 'Registry not loaded', icon: FileText, unavailable: !data?.documents },
    { href: '/evidence', label: 'Indexed evidence passages', value: data?.evidence ? passages.length : 'Unavailable', detail: 'Source passages; not verified obligations', icon: Activity, unavailable: !data?.evidence },
    { href: '/candidates', label: 'Obligation candidates', value: data?.candidates ? candidates.length : 'Unavailable', detail: data?.candidates ? `${pending} pending · ${verified} reviewed verified · ${rejected} rejected` : 'Candidate store not loaded', icon: ClipboardList, unavailable: !data?.candidates },
    { href: '/governance', label: 'Verified obligations', value: data?.candidates ? data.candidates.verified_obligation_count : 'Unavailable', detail: 'Current human-reviewed obligation records', icon: ShieldCheck, unavailable: !data?.candidates },
  ]

  return <div className="mx-auto max-w-7xl space-y-7 p-5 sm:p-8">
    <header><p className="text-xs font-semibold uppercase tracking-wider text-blue-700">Regulatory intelligence workspace</p><h1 className="mt-1 text-3xl font-bold tracking-tight text-slate-900">Dashboard</h1><p className="mt-2 text-slate-600">Current counts come from the local source registry, evidence index, candidate queue, and review records.</p></header>
    {error && <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">{error}</p>}
    <section aria-label="Data-backed workspace metrics" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(item => <Metric key={item.label} {...item} />)}</section>
    <div className="grid gap-5 lg:grid-cols-2">
      <section className="rounded-xl border border-slate-200 bg-white shadow-sm"><div className="border-b border-slate-100 px-5 py-4"><h2 className="font-semibold">Recent recorded activity</h2><p className="mt-1 text-xs text-slate-500">Only events present in the local audit files are shown.</p></div>
        {events.length ? <ul className="divide-y divide-slate-100">{events.slice(0, 6).map(event => <li key={event.event_id} className="flex gap-3 px-5 py-3"><span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-blue-600" /><div className="min-w-0"><p className="text-sm font-medium text-slate-800">{event.event_type.replaceAll('_', ' ')} · {event.rule_id}</p><p className="mt-0.5 text-xs text-slate-500">{event.details?.decision || event.details?.reason || 'Recorded event'} · {event.timestamp || 'Timestamp unavailable'}</p></div></li>)}</ul> : <p className="p-5 text-sm text-slate-600">{data ? 'No governance or candidate-review events are recorded.' : 'Loading local audit records…'}</p>}
      </section>
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-semibold">Data status</h2><dl className="mt-4 space-y-3 text-sm">
        <div className="flex justify-between gap-3"><dt className="text-slate-600">Candidate review queue</dt><dd className="font-medium">{data?.candidates ? `${pending} pending · ${verified} verified · ${rejected} rejected` : 'Unavailable'}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-slate-600">Persisted rule lifecycle records</dt><dd className="font-medium">{data?.governance?.storage_available ? data.governance.rule_count : 'Unavailable'}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-slate-600">Verified source obligations</dt><dd className="font-medium">{data?.candidates ? data.candidates.verified_obligation_count : 'Unavailable'}</dd></div>
      </dl><p className="mt-4 border-t border-slate-100 pt-4 text-xs leading-5 text-slate-500">Synthetic scenarios are isolated from these metrics and never count as regulatory records or real transaction impact.</p></section>
    </div>
    <section className="flex flex-col gap-4 rounded-xl bg-slate-900 p-5 text-white sm:flex-row sm:items-center sm:justify-between"><div><h2 className="font-semibold">Continue a review workflow</h2><p className="mt-1 text-sm text-slate-300">Browse indexed sources, extract grounded candidates, or assess a proposed rule.</p></div><div className="flex flex-wrap gap-2"><Link to="/candidates?tab=extract" className="inline-flex items-center gap-2 rounded-lg bg-white px-4 py-2.5 text-sm font-semibold text-slate-900"><FileSearch size={17} />Candidates & Extraction</Link><Link to="/proposed-rule" className="rounded-lg border border-slate-500 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800">Proposed Rule Review</Link></div></section>
  </div>
}
