import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, FileText, RefreshCw, Search } from 'lucide-react'

export default function Documents() {
  const [documents, setDocuments] = useState([])
  const [evidence, setEvidence] = useState([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [docResponse, evidenceResponse] = await Promise.all([fetch('/api/documents'), fetch('/api/evidence')])
      if (!docResponse.ok || !evidenceResponse.ok) throw new Error('The local registry or evidence index could not be loaded.')
      const [docData, evidenceData] = await Promise.all([docResponse.json(), evidenceResponse.json()])
      setDocuments(docData.documents || []); setEvidence(evidenceData.evidence || [])
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])
  const filtered = documents.filter(doc => [doc.document_id, doc.document_title, doc.issuer, doc.role].some(value => String(value || '').toLowerCase().includes(query.toLowerCase())))

  return <div className="mx-auto max-w-7xl space-y-6 p-5 sm:p-8">
    <header className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-blue-700">Source registry</p><h1 className="mt-1 text-3xl font-bold">Documents</h1><p className="mt-2 max-w-3xl text-slate-600">Registered regulatory source documents and their indexed passage counts. Temporary review uploads do not become registered documents.</p></div><button type="button" onClick={load} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium hover:bg-slate-50 disabled:opacity-50"><RefreshCw size={16} className={loading ? 'animate-spin' : ''} />Refresh registry</button></header>
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-950">This page reads the local document registry. The current application does not persistently register user-uploaded documents.</div>
    {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    <div className="relative max-w-xl"><Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search document ID, title, issuer, or role" className="min-h-11 w-full rounded-lg border border-slate-300 bg-white pl-10 pr-3 text-sm" /></div>
    {loading ? <p className="py-12 text-center text-sm text-slate-500">Loading registered sources…</p> : filtered.length ? <div className="grid gap-4 lg:grid-cols-2">{filtered.map(doc => {
      const count = evidence.filter(row => row.document_id === doc.document_id).length
      const status = doc.document_status || 'Status unknown'
      return <article key={doc.document_id} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3"><div className="flex gap-3"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-blue-50 text-blue-700"><FileText size={20} /></span><div><h2 className="font-semibold text-slate-900">{doc.document_title || 'Title unknown'}</h2><p className="mt-1 font-mono text-xs text-slate-500">{doc.document_id}</p></div></div><span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700">{status}</span></div>
        <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 text-sm"><div><dt className="text-xs text-slate-500">Issuer</dt><dd className="mt-0.5">{doc.issuer || 'Unknown'}</dd></div><div><dt className="text-xs text-slate-500">Published / issue date</dt><dd className="mt-0.5">{doc.issue_date || 'Unknown'}</dd></div><div><dt className="text-xs text-slate-500">Updated date</dt><dd className="mt-0.5">{doc.updated_date || 'Unknown'}</dd></div><div><dt className="text-xs text-slate-500">Registry role</dt><dd className="mt-0.5">{doc.role || 'Unknown'}</dd></div><div><dt className="text-xs text-slate-500">Pages</dt><dd className="mt-0.5">{doc.page_count || 'Unknown'}</dd></div><div><dt className="text-xs text-slate-500">Indexed passages</dt><dd className="mt-0.5">{count}</dd></div></dl>
        {doc.verification_notes && <p className="mt-4 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-950">{doc.verification_notes}</p>}
        <div className="mt-4 flex flex-wrap gap-4 border-t border-slate-100 pt-4">{doc.source_url && <a className="inline-flex items-center gap-1 text-sm font-medium text-blue-700 underline" href={doc.source_url} target="_blank" rel="noreferrer">Open registered source <ExternalLink size={14} /></a>}<Link className="text-sm font-medium text-blue-700 underline" to={`/evidence?document_id=${encodeURIComponent(doc.document_id)}`}>Browse passages</Link></div>
      </article>
    })}</div> : <p className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-600">{documents.length ? 'No registered documents match this search.' : 'No documents are registered in the local registry.'}</p>}
  </div>
}
