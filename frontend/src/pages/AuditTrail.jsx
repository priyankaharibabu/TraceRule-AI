import { useEffect, useMemo, useState } from 'react'
import { Download, RefreshCw, Search } from 'lucide-react'

function csvCell(value) { return `"${String(value ?? '').replaceAll('"', '""')}"` }

export default function AuditTrail() {
  const [events, setEvents] = useState([])
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [eventFilter, setEventFilter] = useState('all')
  const load = async () => {
    setError('')
    try { const response = await fetch('/api/audit-trail'); if (!response.ok) throw new Error('Could not load local audit records.'); const data = await response.json(); setEvents(data.events || []) } catch (err) { setError(err.message) }
  }
  useEffect(() => { load() }, [])
  const types = useMemo(() => [...new Set(events.map(event => event.event_type).filter(Boolean))].sort(), [events])
  const filtered = events.filter(event => eventFilter === 'all' || event.event_type === eventFilter).filter(event => JSON.stringify(event).toLowerCase().includes(query.toLowerCase()))
  const exportCsv = () => {
    const columns = ['event_id', 'timestamp', 'rule_id', 'event_type', 'user_id', 'details']
    const lines = [columns.map(csvCell).join(','), ...filtered.map(event => columns.map(key => csvCell(key === 'details' ? JSON.stringify(event.details || {}) : event[key])).join(','))]
    const url = URL.createObjectURL(new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'tracerule-local-audit.csv'; anchor.click(); URL.revokeObjectURL(url)
  }
  return <div className="mx-auto max-w-7xl space-y-6 p-5 sm:p-8">
    <header className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-blue-700">Recorded events</p><h1 className="mt-1 text-3xl font-bold">Audit Trail</h1><p className="mt-2 text-slate-600">Governance events and candidate review records found in the local audit files.</p></div><div className="flex gap-2"><button type="button" onClick={load} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 text-sm"><RefreshCw size={16} />Refresh</button><button type="button" onClick={exportCsv} disabled={!filtered.length} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-slate-900 px-3 text-sm font-medium text-white disabled:opacity-50"><Download size={16} />Export visible events</button></div></header>
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm leading-5 text-amber-950">Local prototype audit records are not tamper-proof and do not authenticate the recorded user identity.</div>
    {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    <div className="grid gap-3 sm:grid-cols-[1fr_16rem]"><label className="relative"><span className="sr-only">Search audit records</span><Search size={17} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search event, record, actor, or details" className="min-h-10 w-full rounded-lg border border-slate-300 bg-white pl-9 pr-3 text-sm" /></label><select aria-label="Filter event type" value={eventFilter} onChange={event => setEventFilter(event.target.value)} className="min-h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm"><option value="all">All recorded event types</option>{types.map(type => <option value={type} key={type}>{type.replaceAll('_', ' ')}</option>)}</select></div>
    <p className="text-sm text-slate-500">{filtered.length} recorded event(s)</p>
    {filtered.length ? <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">{filtered.map(event => <article key={`${event.event_id}-${event.timestamp}`} className="border-b border-slate-100 p-4 last:border-0"><div className="flex flex-wrap justify-between gap-2"><div><h2 className="text-sm font-semibold capitalize text-slate-900">{String(event.event_type || 'event').replaceAll('_', ' ')}</h2><p className="mt-1 font-mono text-xs text-slate-500">{event.event_id} · {event.rule_id || 'Record ID unknown'}</p></div><time className="text-xs text-slate-500">{event.timestamp || 'Timestamp unknown'}</time></div><p className="mt-2 text-xs text-slate-600">Actor: {event.user_id || 'Not recorded / not authenticated'}</p>{event.details && <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-3 text-xs text-slate-700">{JSON.stringify(event.details, null, 2)}</pre>}</article>)}</div> : <div className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-600">{events.length ? 'No recorded events match the filters.' : 'No audit events are recorded in the local audit files.'}</div>}
  </div>
}
