import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Check, ChevronDown, ClipboardCheck, FileSearch, Loader2, Play, RefreshCw, Search, Send, X } from 'lucide-react'
import { fetchWithTimeout } from '../utils/fetchWithTimeout'

const inputClass = 'mt-1 min-h-10 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm'

export default function Candidates() {
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState(params.get('tab') === 'extract' ? 'extract' : 'queue')
  const [documents, setDocuments] = useState([])
  const [evidence, setEvidence] = useState([])
  const [candidates, setCandidates] = useState([])
  const [documentId, setDocumentId] = useState(params.get('document_id') || '')
  const [selectedEvidence, setSelectedEvidence] = useState(params.get('evidence_id') ? [params.get('evidence_id')] : [])
  const [extraction, setExtraction] = useState(null)
  const [selectedResults, setSelectedResults] = useState([])
  const [selectedCandidate, setSelectedCandidate] = useState(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [decision, setDecision] = useState('')
  const [review, setReview] = useState({ reviewer_id: '', reviewer_notes: '', reviewed_requirement_text: '', reviewed_actor: '', reviewed_action: '', reviewed_conditions: '', reviewed_exceptions: '', applicability_notes: '' })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [uploadFile, setUploadFile] = useState(null)
  const [uploadQuery, setUploadQuery] = useState('')
  const [useNvidia, setUseNvidia] = useState(false)
  const [phase8Result, setPhase8Result] = useState(null)
  const [ragQuery, setRagQuery] = useState('')
  const [ragFile, setRagFile] = useState(null)
  const [semanticOptIn, setSemanticOptIn] = useState(false)
  const [rerankOptIn, setRerankOptIn] = useState(false)
  const [ragResult, setRagResult] = useState(null)

  const load = useCallback(async () => {
    setBusy('load'); setError('')
    try {
      const responses = await Promise.all([fetch('/api/documents'), fetch('/api/evidence'), fetch('/api/candidates')])
      if (responses.some(response => !response.ok)) throw new Error('Could not load the local document, evidence, or candidate records.')
      const [docData, evidenceData, candidateData] = await Promise.all(responses.map(response => response.json()))
      setDocuments(docData.documents || []); setEvidence(evidenceData.evidence || []); setCandidates(candidateData.candidates || [])
      setSelectedCandidate(current => current ? (candidateData.candidates || []).find(item => item.candidate_id === current.candidate_id) || null : null)
    } catch (err) { setError(err.message) } finally { setBusy('') }
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const requestedDoc = params.get('document_id')
    const requestedEvidence = params.get('evidence_id')
    if (requestedDoc) setDocumentId(requestedDoc)
    if (requestedEvidence) setSelectedEvidence([requestedEvidence])
    if (params.get('tab') === 'extract') setTab('extract')
  }, [params])

  const evidenceForDocument = useMemo(() => documentId ? evidence.filter(row => row.document_id === documentId) : [], [evidence, documentId])
  const docsById = useMemo(() => Object.fromEntries(documents.map(row => [row.document_id, row])), [documents])
  const filteredCandidates = candidates.filter(row => {
    const term = query.toLowerCase()
    const matches = [row.candidate_id, row.candidate_text, row.document_id, row.evidence_id].some(value => String(value || '').toLowerCase().includes(term))
    return matches && (statusFilter === 'all' || row.review_decision === statusFilter)
  })
  const counts = { pending: candidates.filter(row => row.review_decision === 'pending').length, verified: candidates.filter(row => row.review_decision === 'verified').length, rejected: candidates.filter(row => row.review_decision === 'rejected').length }

  const toggleEvidence = id => setSelectedEvidence(current => current.includes(id) ? current.filter(item => item !== id) : current.length < 3 ? [...current, id] : current)
  const runExtraction = async () => {
    setBusy('extract'); setError(''); setNotice(''); setExtraction(null); setSelectedResults([])
    try {
      const response = await fetchWithTimeout('/api/candidates/extract', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: documentId, evidence_ids: selectedEvidence }) })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : payload.detail?.message || 'Extraction failed. Check source eligibility and local provider settings.')
      setExtraction(payload); setSelectedResults((payload.candidates || []).filter(item => item.candidate_obligation_grounded === true).map(item => item.candidate_id))
    } catch (err) { setError(err.message) } finally { setBusy('') }
  }
  const submitCandidates = async () => {
    if (!extraction || !selectedResults.length) return
    setBusy('submit'); setError(''); setNotice('')
    try {
      const response = await fetch('/api/candidates/extract/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run_id: extraction.run_id, candidate_ids: selectedResults }) })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'The candidates could not be submitted.')
      setNotice(payload.message); setTab('queue'); setParams({ tab: 'queue' }); await load()
    } catch (err) { setError(err.message) } finally { setBusy('') }
  }
  const submitReview = async () => {
    if (!selectedCandidate || !decision) return
    setBusy('review'); setError(''); setNotice('')
    try {
      const response = await fetch(`/api/candidates/${encodeURIComponent(selectedCandidate.candidate_id)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...review, decision }) })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Review could not be recorded.')
      setNotice(payload.message); setDecision(''); setReview({ reviewer_id: '', reviewer_notes: '', reviewed_requirement_text: '', reviewed_actor: '', reviewed_action: '', reviewed_conditions: '', reviewed_exceptions: '', applicability_notes: '' }); await load()
    } catch (err) { setError(err.message) } finally { setBusy('') }
  }

  const analyzeUpload = async () => {
    if (!uploadFile) return
    setBusy('phase8'); setError(''); setNotice(''); setPhase8Result(null)
    try {
      const form = new FormData()
      form.append('file', uploadFile)
      form.append('focus_query', uploadQuery)
      form.append('use_nvidia', String(useNvidia))
      const response = await fetchWithTimeout('/api/phase8/analyze', { method: 'POST', body: form }, 420000)
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'PDF analysis failed safely.')
      setPhase8Result(payload)
    } catch (err) { setError(err.message) } finally { setBusy('') }
  }

  const retrieveUpload = async () => {
    if (!ragFile) return
    setBusy('rag'); setError(''); setRagResult(null)
    try {
      const form = new FormData()
      form.append('file', ragFile)
      form.append('query', ragQuery)
      form.append('semantic_opt_in', String(semanticOptIn))
      form.append('rerank_opt_in', String(rerankOptIn))
      const response = await fetchWithTimeout('/api/phase9/retrieve', { method: 'POST', body: form }, 420000)
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Evidence retrieval failed safely.')
      setRagResult(payload)
    } catch (err) { setError(err.message) } finally { setBusy('') }
  }

  const tabButton = (key, label) => <button type="button" onClick={() => { setTab(key); setParams({ tab: key }) }} aria-pressed={tab === key} className={`min-h-10 rounded-lg px-4 text-sm font-semibold ${tab === key ? 'bg-blue-700 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>{label}</button>
  return <div className="mx-auto max-w-7xl space-y-6 p-5 sm:p-8">
    <header className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-blue-700">Grounded extraction and human review</p><h1 className="mt-1 text-3xl font-bold">Candidates & Extraction</h1><p className="mt-2 max-w-3xl text-slate-600">Extract provisional candidates from indexed source passages, then review them in the existing candidate queue.</p></div><button type="button" onClick={load} disabled={busy !== ''} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium"><RefreshCw size={16} className={busy === 'load' ? 'animate-spin' : ''} />Refresh records</button></header>
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm leading-5 text-amber-950"><strong>Human review required.</strong> Extraction results are provisional. Only source-grounded candidate text can be submitted; submitting does not verify an obligation or activate a rule. Reviewer IDs are not authenticated.</div>
    {error && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">{error}</p>}{notice && <p role="status" className="rounded-lg border border-green-300 bg-green-50 p-3 text-sm text-green-900">{notice}</p>}
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-2"><div className="flex gap-1">{tabButton('extract', 'Extract from evidence')}{tabButton('upload', 'Analyze uploaded PDF (Phase 8)')}{tabButton('rag', 'Evidence retrieval (Phase 9)')}{tabButton('queue', 'Candidate review queue')}</div><p className="px-2 text-xs text-slate-500">{counts.pending} pending · {counts.verified} verified by review · {counts.rejected} rejected</p></div>

    {tab === 'rag' && <section className="space-y-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div><h2 className="text-lg font-semibold">Grounded evidence retrieval</h2><p className="mt-1 text-sm text-slate-600">Search the selected PDF using local BM25 by default. Semantic embedding and reranking require separate explicit external-provider opt-ins. This retrieval-only workflow creates no obligations or regulatory records.</p></div>
      <div className="grid gap-4 md:grid-cols-2"><label className="block text-sm font-medium">PDF document<input type="file" accept="application/pdf,.pdf" onChange={event => { setRagFile(event.target.files?.[0] || null); setRagResult(null) }} className={`${inputClass} file:mr-3 file:border-0 file:bg-slate-100`} /><span className="mt-1 block text-xs text-slate-500">Maximum 15 MB. Text PDFs only; scanned pages are not OCR processed.</span></label><label className="block text-sm font-medium">Search query<input value={ragQuery} maxLength={500} onChange={event => setRagQuery(event.target.value)} placeholder="Describe the evidence you need" className={inputClass} /></label></div>
      <label className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"><input type="checkbox" checked={semanticOptIn} onChange={event => setSemanticOptIn(event.target.checked)} className="mt-1" /><span><strong>Send the query and PDF passages to NVIDIA NIM for semantic embeddings.</strong><span className="mt-1 block text-xs leading-5">Unchecked by default. Uses the configured embedding model. If unavailable, results fall back to BM25 and show the provider status.</span></span></label>
      <label className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"><input type="checkbox" checked={rerankOptIn} onChange={event => setRerankOptIn(event.target.checked)} className="mt-1" /><span><strong>Also send the query and retrieved passages to NVIDIA NIM for reranking.</strong><span className="mt-1 block text-xs leading-5">Separate opt-in. Reranking changes retrieval order only; it does not determine regulatory validity, compliance, or conflict.</span></span></label>
      <button type="button" onClick={retrieveUpload} disabled={!ragFile || !ragQuery.trim() || busy !== ''} aria-busy={busy === 'rag'} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-blue-700 px-4 font-semibold text-white disabled:opacity-50">{busy === 'rag' ? <Loader2 size={18} className="animate-spin" /> : <Search size={17} />}{busy === 'rag' ? 'Retrieving evidence…' : 'Retrieve evidence'}</button>
      {ragResult && <div className="space-y-4 border-t border-slate-200 pt-4"><div className="rounded-lg bg-slate-50 p-3 text-sm"><strong>{ragResult.status?.replaceAll('_', ' ')}</strong><p className="mt-1">Mode: {ragResult.retrieval_mode} · Semantic: {ragResult.semantic_status} · Reranking: {ragResult.rerank_status}</p><p className="mt-1 text-xs text-slate-600">{ragResult.score_notice}</p><p className="mt-1 text-xs text-slate-500">Unverified user upload · session-only index · no regulatory data written</p></div>
        {ragResult.results?.length ? ragResult.results.map(item => <article key={item.chunk_id} className="rounded-lg border border-slate-200 p-4"><div className="flex flex-wrap justify-between gap-2"><p className="text-xs font-semibold">{item.document_id} · {item.evidence_id} · page {item.page_number}</p><p className="text-xs text-slate-500">BM25 {item.lexical_score == null ? '—' : item.lexical_score.toFixed(4)} · cosine {item.semantic_score == null ? '—' : item.semantic_score.toFixed(4)} · fused {item.fused_score.toFixed(4)}{item.rerank_score == null ? '' : ` · rerank ${item.rerank_score.toFixed(4)}`}</p></div><p className="mt-1 text-xs text-slate-500">{item.source_reference} · chunk {item.chunk_id}</p><blockquote className="mt-3 whitespace-pre-wrap border-l-2 border-slate-300 pl-3 text-sm leading-5">{item.passage_text}</blockquote></article>) : <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-600">No passages matched this query. This is not evidence that the document contains no relevant requirements.</p>}
      </div>}
    </section>}

    {tab === 'upload' && <section className="space-y-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div><h2 className="text-lg font-semibold">Temporary PDF evidence analysis</h2><p className="mt-1 text-sm text-slate-600">Extracts page-linked passages and ranks them locally. The uploaded document and results stay in memory for this request and are not registered or added to the existing candidate queue.</p></div>
      <div className="grid gap-4 md:grid-cols-2"><label className="block text-sm font-medium">PDF document<input type="file" accept="application/pdf,.pdf" onChange={event => { setUploadFile(event.target.files?.[0] || null); setPhase8Result(null) }} className={`${inputClass} file:mr-3 file:border-0 file:bg-slate-100`} /><span className="mt-1 block text-xs text-slate-500">Maximum 15 MB. Text PDFs only; scanned pages are not OCR processed.</span></label><label className="block text-sm font-medium">Optional retrieval focus<input value={uploadQuery} maxLength={500} onChange={event => setUploadQuery(event.target.value)} placeholder="Terms or obligation topic to find" className={inputClass} /></label></div>
      <label className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"><input type="checkbox" checked={useNvidia} onChange={event => setUseNvidia(event.target.checked)} className="mt-1" /><span><strong>Explicitly send retrieved passages to configured NVIDIA NIM for suggestions.</strong><span className="mt-1 block text-xs leading-5">Unchecked by default. Checking sends selected source excerpts to the configured external provider. Suggestions remain unverified, inactive, and require human review.</span></span></label>
      <button type="button" onClick={analyzeUpload} disabled={!uploadFile || busy !== ''} aria-busy={busy === 'phase8'} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-blue-700 px-4 font-semibold text-white disabled:opacity-50">{busy === 'phase8' ? <Loader2 size={18} className="animate-spin" /> : <FileSearch size={17} />}{busy === 'phase8' ? 'Analyzing temporary PDF…' : 'Analyze temporary PDF'}</button>
      {phase8Result && <div className="space-y-4 border-t border-slate-200 pt-4"><div className="rounded-lg bg-slate-50 p-3 text-sm"><strong>{phase8Result.workflow_status?.replaceAll('_', ' ')}</strong><p className="mt-1">{phase8Result.message}</p><p className="mt-1 text-xs text-slate-500">Source status: unverified user upload · Persistence: session only · Activation: inactive</p></div>
        {phase8Result.validation_errors?.length > 0 && <ul className="rounded-lg bg-amber-50 p-3 text-sm text-amber-950">{phase8Result.validation_errors.map((item, index) => <li key={`${item.code}-${index}`}>{item.message}</li>)}</ul>}
        {!!phase8Result.retrieved_passages?.length && <div><h3 className="font-semibold">Retrieved source passages</h3><div className="mt-2 space-y-2">{phase8Result.retrieved_passages.map(item => <details key={item.evidence_id} className="rounded-lg border border-slate-200 p-3"><summary className="cursor-pointer text-sm font-medium">Page {item.page_number} · {item.evidence_id} · {item.source_reference}</summary><blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-slate-300 pl-3 text-xs leading-5">{item.passage_text}</blockquote></details>)}</div></div>}
        {!!phase8Result.candidates?.length && <div><h3 className="font-semibold">Model generated suggestions</h3><div className="mt-2 space-y-3">{phase8Result.candidates.map(item => <article key={item.candidate_id} className="rounded-lg border border-amber-300 p-4"><p className="text-xs font-semibold text-amber-900">Unverified candidate · pending human review · inactive</p><p className="mt-2 text-sm">{item.candidate_obligation}</p><p className="mt-2 text-xs text-slate-600">Source: {item.source_reference} · page {item.page_number} · evidence {item.evidence_id}</p><details className="mt-2"><summary className="cursor-pointer text-xs font-semibold">Compare exact source quotation</summary><blockquote className="mt-2 border-l-2 border-slate-300 pl-3 text-xs leading-5">{item.supporting_quotation}</blockquote></details><p className="mt-2 text-xs text-amber-900">Uncertainty: {item.uncertainty}</p></article>)}</div></div>}
      </div>}
    </section>}

    {tab === 'extract' && <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,0.9fr)]">
      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div><h2 className="text-lg font-semibold">Choose registered source evidence</h2><p className="mt-1 text-sm text-slate-600">Select up to three passages from one registered document. Historical/superseded source restrictions are enforced by the existing workflow.</p></div>
        <label className="block text-sm font-medium">Registered document<select value={documentId} onChange={event => { setDocumentId(event.target.value); setSelectedEvidence([]); setExtraction(null) }} className={inputClass}><option value="">Choose a document</option>{documents.map(doc => <option key={doc.document_id} value={doc.document_id}>{doc.document_id} · {doc.document_title}</option>)}</select></label>
        <div className="max-h-[28rem] space-y-2 overflow-auto pr-1">{evidenceForDocument.map(row => <label key={row.evidence_id} className={`flex cursor-pointer gap-3 rounded-lg border p-3 ${selectedEvidence.includes(row.evidence_id) ? 'border-blue-400 bg-blue-50' : 'border-slate-200 hover:bg-slate-50'}`}><input type="checkbox" checked={selectedEvidence.includes(row.evidence_id)} onChange={() => toggleEvidence(row.evidence_id)} disabled={!selectedEvidence.includes(row.evidence_id) && selectedEvidence.length >= 3} className="mt-1" /><span className="min-w-0"><span className="block break-all font-mono text-xs font-semibold">{row.evidence_id}</span><span className="mt-1 line-clamp-3 block text-sm text-slate-700">{row.passage_text}</span><span className="mt-1 block text-xs text-slate-500">Page {row.printed_page_number || row.page_number || row.pdf_page_index || 'Unknown'} · {row.paragraph_reference || 'Paragraph unknown'}</span></span></label>)}{documentId && !evidenceForDocument.length && <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-600">No indexed passages are available for this registered document.</p>}</div>
        <button type="button" onClick={runExtraction} disabled={!documentId || !selectedEvidence.length || busy !== ''} className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-blue-700 px-4 font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50">{busy === 'extract' ? <><Loader2 size={18} className="animate-spin" />Running existing extraction workflow…</> : <><Play size={17} />Run extraction on {selectedEvidence.length || 0} passage(s)</>}</button>
        <p className="text-xs leading-5 text-slate-500">The existing configured extraction provider runs only when you click this button. Its output is checked against source text before it can enter the review queue.</p>
      </section>
      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div><h2 className="text-lg font-semibold">Extraction result</h2><p className="mt-1 text-sm text-slate-600">Results stay in this browser session until you explicitly submit grounded candidates.</p></div>
        {!extraction ? <div className="rounded-lg bg-slate-50 p-8 text-center text-sm text-slate-500">Choose source passages and run extraction to view provisional results.</div> : <>
          <div className="flex flex-wrap gap-2 text-xs"><span className="rounded bg-slate-100 px-2 py-1">Run {extraction.run_id}</span><span className="rounded bg-slate-100 px-2 py-1">Source status: {extraction.document_status || 'Unknown'}</span><span className="rounded bg-amber-100 px-2 py-1 text-amber-900">{extraction.workflow_status?.replaceAll('_', ' ')}</span></div>
          {extraction.validation_errors?.length > 0 && <ul className="space-y-1 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{extraction.validation_errors.map((item, index) => <li key={`${item.code}-${index}`}>{item.message}</li>)}</ul>}
          {extraction.candidates?.length ? <div className="space-y-3">{extraction.candidates.map(item => {
            const canSubmit = item.candidate_obligation_grounded === true
            return <article key={item.candidate_id} className="rounded-lg border border-slate-200 p-4"><div className="flex items-start justify-between gap-3"><div><p className="font-mono text-xs font-semibold">{item.candidate_id}</p><p className="mt-1 text-xs text-slate-500">{item.document_id} · {item.evidence_id} · page {item.source_page || 'Unknown'} · {item.candidate_type?.replaceAll('_', ' ')}</p></div><span className={`rounded-full px-2 py-1 text-xs ${canSubmit ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-900'}`}>{canSubmit ? 'Source-grounded' : 'Ungrounded; review only'}</span></div><p className="mt-3 text-sm leading-5 text-slate-800">{item.proposed_obligation || 'No obligation candidate returned.'}</p><details className="mt-3"><summary className="cursor-pointer text-xs font-semibold text-blue-800">Source passage and uncertainty</summary><blockquote className="mt-2 border-l-2 border-slate-300 pl-3 text-xs leading-5 text-slate-700">{item.supporting_quotation || item.source_wording}</blockquote><p className="mt-2 text-xs text-amber-800">{item.uncertainty}</p></details>{canSubmit && <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={selectedResults.includes(item.candidate_id)} onChange={() => setSelectedResults(current => current.includes(item.candidate_id) ? current.filter(id => id !== item.candidate_id) : [...current, item.candidate_id])} />Submit this provisional candidate for human review</label>}</article>
          })}</div> : <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-600">No candidate was returned for these passages. This is not a conclusion that no legal obligation exists.</p>}
          {!!selectedResults.length && <button type="button" onClick={submitCandidates} disabled={busy !== ''} className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 font-semibold text-white disabled:opacity-50">{busy === 'submit' ? <Loader2 size={18} className="animate-spin" /> : <Send size={17} />}Add selected candidates to review queue</button>}
          <p className="text-xs text-slate-500">Provider: {extraction.audit?.model || 'not reported'} · Candidate status: unverified · Activation: inactive</p>
        </>}
      </section>
    </div>}

    {tab === 'queue' && <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
      <section className="space-y-4"><div className="grid gap-3 sm:grid-cols-[1fr_12rem]"><label className="relative"><span className="sr-only">Search candidates</span><Search size={17} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search candidate text, ID, or evidence" className="min-h-10 w-full rounded-lg border border-slate-300 bg-white pl-9 pr-3 text-sm" /></label><select value={statusFilter} onChange={event => setStatusFilter(event.target.value)} className="min-h-10 rounded-lg border border-slate-300 bg-white px-3 text-sm"><option value="all">All review states</option><option value="pending">Pending</option><option value="verified">Verified by review</option><option value="rejected">Rejected</option></select></div>
        {filteredCandidates.length ? filteredCandidates.map(item => <button type="button" key={item.candidate_id} onClick={() => { setSelectedCandidate(item); setDecision(''); setReview({ reviewer_id: '', reviewer_notes: '', reviewed_requirement_text: '', reviewed_actor: '', reviewed_action: '', reviewed_conditions: '', reviewed_exceptions: '', applicability_notes: '' }) }} className={`block w-full rounded-xl border bg-white p-4 text-left shadow-sm ${selectedCandidate?.candidate_id === item.candidate_id ? 'border-blue-500 ring-2 ring-blue-100' : 'border-slate-200 hover:border-slate-300'}`}><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono text-sm font-semibold">{item.candidate_id}</span><span className={`rounded-full px-2 py-1 text-xs ${item.review_decision === 'verified' ? 'bg-green-100 text-green-800' : item.review_decision === 'rejected' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-900'}`}>{item.review_decision === 'verified' ? 'Verified by review' : item.review_decision === 'rejected' ? 'Rejected' : 'Pending review'}</span></div><p className="mt-2 line-clamp-2 text-sm text-slate-700">{item.candidate_text}</p><p className="mt-2 text-xs text-slate-500">{item.document_id} · {item.evidence_id} · page {item.source_page || item.printed_page_number || 'Unknown'} · {item.candidate_type?.replaceAll('_', ' ') || 'Type unknown'}</p>{item.source_match_status !== 'matched' && <p className="mt-2 text-xs font-medium text-amber-800">Source text needs review before a decision can be recorded.</p>}</button>) : <div className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-600">No candidates match this filter.</div>}
      </section>
      <section className="h-fit rounded-xl border border-slate-200 bg-white p-5 shadow-sm xl:sticky xl:top-5">{selectedCandidate ? <>
        <div className="flex items-start justify-between gap-2"><div><h2 className="font-semibold">Review {selectedCandidate.candidate_id}</h2><p className="mt-1 text-xs text-slate-500">{docsById[selectedCandidate.document_id]?.document_title || selectedCandidate.document_id} · {selectedCandidate.evidence_id} · page {selectedCandidate.source_page || selectedCandidate.printed_page_number || 'Unknown'} · source status {docsById[selectedCandidate.document_id]?.document_status || 'unknown'}</p></div>{selectedCandidate.review_decision !== 'pending' && <span className="rounded bg-slate-100 px-2 py-1 text-xs">{selectedCandidate.review_decision}</span>}</div>
        <p className="mt-4 rounded-lg bg-slate-50 p-3 text-sm leading-5">{selectedCandidate.candidate_text}</p><details className="mt-3"><summary className="cursor-pointer text-sm font-medium text-blue-800">Open exact supporting source passage</summary><blockquote className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap border-l-2 border-slate-300 pl-3 text-xs leading-5 text-slate-700">{selectedCandidate.source_passage_text}</blockquote></details>
        {selectedCandidate.source_match_status !== 'matched' && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-900">{selectedCandidate.source_match_warning}</p>}
        {selectedCandidate.review_decision === 'pending' && selectedCandidate.source_match_status === 'matched' ? <div className="mt-4 space-y-3 border-t border-slate-100 pt-4"><p className="text-xs leading-5 text-amber-900">A verified decision writes a human-reviewed obligation record. It does not approve or activate an executable rule. This local prototype cannot authenticate the reviewer.</p><label className="block text-sm">Reviewer identifier<input value={review.reviewer_id} onChange={event => setReview(current => ({ ...current, reviewer_id: event.target.value }))} className={inputClass} /></label>
          <label className="block text-sm">Review notes<textarea value={review.reviewer_notes} onChange={event => setReview(current => ({ ...current, reviewer_notes: event.target.value }))} rows={2} className={inputClass} /></label>
          {decision === 'verified' && <div className="space-y-2 rounded-lg bg-slate-50 p-3"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Required human-reviewed fields</p>{[['reviewed_requirement_text','Reviewed requirement'],['reviewed_actor','Responsible actor'],['reviewed_action','Required action'],['reviewed_conditions','Conditions or “None identified after review”'],['reviewed_exceptions','Exceptions or “None identified after review”'],['applicability_notes','Applicability notes']].map(([key,label]) => <label key={key} className="block text-xs font-medium text-slate-700">{label}<textarea rows={2} value={review[key]} onChange={event => setReview(current => ({ ...current, [key]: event.target.value }))} className={inputClass} /></label>)}</div>}
          {!decision ? <div className="grid grid-cols-2 gap-2"><button type="button" onClick={() => setDecision('verified')} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-green-700 px-3 text-sm font-semibold text-white"><Check size={16} />Review as verified</button><button type="button" onClick={() => setDecision('rejected')} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-red-300 px-3 text-sm font-semibold text-red-800"><X size={16} />Reject candidate</button></div> : <div className="flex gap-2"><button type="button" onClick={() => setDecision('')} className="min-h-10 rounded-lg border border-slate-300 px-3 text-sm">Cancel</button><button type="button" onClick={submitReview} disabled={busy !== '' || !review.reviewer_id.trim() || (decision === 'rejected' && !review.reviewer_notes.trim())} className="inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-lg bg-blue-700 px-3 text-sm font-semibold text-white disabled:opacity-50">{busy === 'review' ? <Loader2 size={16} className="animate-spin" /> : <ClipboardCheck size={16} />}Record {decision} decision</button></div>}
        </div> : <div className="mt-4 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">This candidate has already been reviewed. Duplicate decisions are blocked by the existing review service.</div>}
        <Link to={`/evidence?document_id=${encodeURIComponent(selectedCandidate.document_id)}&evidence_id=${encodeURIComponent(selectedCandidate.evidence_id)}`} className="mt-4 inline-flex items-center gap-2 text-sm text-blue-700 underline"><FileSearch size={15} />Open evidence record</Link>
      </> : <div className="py-12 text-center text-sm text-slate-500"><ChevronDown className="mx-auto mb-3 rotate-[-90deg]" size={24} />Select a candidate to inspect source evidence and review fields.</div>}</section>
    </div>}
  </div>
}
