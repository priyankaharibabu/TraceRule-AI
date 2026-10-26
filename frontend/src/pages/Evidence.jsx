import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ChevronDown, ChevronRight, FileSearch, Search } from 'lucide-react'

export default function Evidence() {
  const [params] = useSearchParams()
  const [rows, setRows] = useState([])
  const [documents, setDocuments] = useState([])
  const [query, setQuery] = useState('')
  const [documentFilter, setDocumentFilter] = useState(params.get('document_id') || 'all')
  const [expanded, setExpanded] = useState(params.get('evidence_id') || '')
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    Promise.all([fetch('/api/evidence'), fetch('/api/documents')]).then(async ([evidenceResponse, documentsResponse]) => {
      if (!evidenceResponse.ok || !documentsResponse.ok) throw new Error('The local evidence index could not be loaded.')
      const [evidenceData, documentData] = await Promise.all([evidenceResponse.json(), documentsResponse.json()])
      if (active) { setRows(evidenceData.evidence || []); setDocuments(documentData.documents || []) }
    }).catch(err => active && setError(err.message))
    return () => { active = false }
  }, [])
  const documentNames = useMemo(() => Object.fromEntries(documents.map(item => [item.document_id, item.document_title || item.document_id])), [documents])
  const filtered = rows.filter(row => {
    const matchesText = [row.evidence_id, row.document_id, row.passage_text, row.section_heading, row.paragraph_reference].some(value => String(value || '').toLowerCase().includes(query.toLowerCase()))
    return matchesText && (documentFilter === 'all' || row.document_id === documentFilter)
  })

  return <div className="mx-auto max-w-7xl space-y-6 p-5 sm:p-8">
    <header><p className="text-xs font-semibold uppercase tracking-wider text-blue-700">Source evidence library</p><h1 className="mt-1 text-3xl font-bold">Evidence Passages</h1><p className="mt-2 max-w-3xl text-slate-600">Search and inspect exact indexed text with its source document, passage ID, and page references. A passage is evidence material, not a verified obligation.</p></header>
    {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    <div className="grid gap-3 sm:grid-cols-[1fr_15rem]"><label className="relative"><span className="sr-only">Search passages</span><Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search source text or evidence ID" className="min-h-11 w-full rounded-lg border border-slate-300 bg-white pl-10 pr-3 text-sm" /></label><label><span className="sr-only">Filter by source document</span><select value={documentFilter} onChange={event => setDocumentFilter(event.target.value)} className="min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm"><option value="all">All registered documents</option>{documents.map(doc => <option key={doc.document_id} value={doc.document_id}>{doc.document_id}</option>)}</select></label></div>
    <div className="flex items-center justify-between text-sm text-slate-500"><span>{rows.length} passages indexed</span><span>{filtered.length} shown</span></div>
    {error ? null : filtered.length ? <div className="space-y-3">{filtered.map(row => {
      const isOpen = expanded === row.evidence_id
      const page = row.printed_page_number || row.page_number || row.pdf_page_index || 'Unknown'
      return <article key={row.evidence_id} className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3 p-4"><button type="button" aria-expanded={isOpen} onClick={() => setExpanded(isOpen ? '' : row.evidence_id)} className="flex min-w-0 flex-1 items-start gap-3 text-left"><span className="mt-0.5 text-slate-400">{isOpen ? <ChevronDown size={18} /> : <ChevronRight size={18} />}</span><span className="min-w-0"><span className="block break-all font-mono text-sm font-semibold text-slate-900">{row.evidence_id}</span><span className="mt-1 block text-sm text-slate-600">{documentNames[row.document_id] || row.document_id}</span><span className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500"><span className="rounded bg-slate-100 px-2 py-1">Document {row.document_id}</span><span className="rounded bg-slate-100 px-2 py-1">Page {page}</span>{row.paragraph_reference && <span className="rounded bg-slate-100 px-2 py-1">Paragraph {row.paragraph_reference}</span>}<span className="rounded bg-amber-50 px-2 py-1 text-amber-800">{row.document_status || 'Document status unknown'}</span></span></span></button><Link to={`/candidates?tab=extract&document_id=${encodeURIComponent(row.document_id)}&evidence_id=${encodeURIComponent(row.evidence_id)}`} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-blue-200 px-3 text-sm font-medium text-blue-800 hover:bg-blue-50"><FileSearch size={16} />Extract candidate</Link></div>
        {isOpen && <div className="border-t border-slate-100 p-4"><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Exact indexed source text</p><blockquote className="whitespace-pre-wrap border-l-2 border-blue-300 pl-4 text-sm leading-6 text-slate-800">{row.passage_text || 'Passage text unavailable'}</blockquote>{row.source_url && <a href={row.source_url} target="_blank" rel="noreferrer" className="mt-3 inline-block text-sm text-blue-700 underline">Open source document reference</a>}</div>}
      </article>
    })}</div> : <p className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-600">{rows.length ? 'No passages match these filters.' : 'No passages are available in the local evidence index.'}</p>}
  </div>
}
