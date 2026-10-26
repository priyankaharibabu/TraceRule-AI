import { useMemo, useState } from 'react'
import { FileUp, Loader2, ShieldAlert } from 'lucide-react'
import { Link } from 'react-router-dom'

const TRANSACTION_FIELDS = [
  ['customer_type', 'Customer type'], ['loan_product', 'Loan product'], ['loan_amount', 'Loan amount'],
  ['loan_tenor_months', 'Loan term in months'], ['apr_percent', 'Annual interest rate (%)'],
  ['channel', 'Channel'], ['has_lsp', 'Uses a loan service provider'], ['lsp_count', 'Number of service providers'],
  ['credit_limit_increase_requested', 'Credit limit increase requested'], ['disbursement_route', 'Disbursement route'],
  ['repayment_route', 'Repayment route'], ['data_storage_region', 'Data storage region'],
  ['dlg_cap_percent', 'Digital lending cap (%)'], ['borrower_age', 'Borrower age'],
  ['income_verified', 'Income verified'], ['jurisdiction', 'Jurisdiction'],
]
const OPERATORS = [['eq', 'is'], ['ne', 'is not'], ['lt', 'is less than'], ['lte', 'is at most'], ['gt', 'is greater than'], ['gte', 'is at least'], ['in', 'is one of'], ['not_in', 'is not one of']]
const ACTIONS = [['allow', 'Allow'], ['deny', 'Deny'], ['require_review', 'Send for review'], ['flag', 'Flag'], ['set_limit', 'Set a limit'], ['require_disclosure', 'Require a disclosure']]

async function readResponse(response) {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || 'The request could not be completed. Please check the fields and try again.')
  return payload
}

export default function ProposedRuleAnalysis() {
  const [file, setFile] = useState(null)
  const [extraction, setExtraction] = useState(null)
  const [selectedStatement, setSelectedStatement] = useState('')
  const [purpose, setPurpose] = useState('')
  const [ruleName, setRuleName] = useState('')
  const [conditionField, setConditionField] = useState('loan_amount')
  const [conditionOperator, setConditionOperator] = useState('gt')
  const [conditionValue, setConditionValue] = useState('50000')
  const [actionType, setActionType] = useState('require_review')
  const [actionMessage, setActionMessage] = useState('')
  const [maxAmount, setMaxAmount] = useState('')
  const [affectedNotes, setAffectedNotes] = useState('')
  const [limitNotes, setLimitNotes] = useState('')
  const [exceptionNotes, setExceptionNotes] = useState('')
  const [result, setResult] = useState(null)
  const [decision, setDecision] = useState('')
  const [authorityNotes, setAuthorityNotes] = useState('')
  const [decisionResult, setDecisionResult] = useState(null)
  const [resultTab, setResultTab] = useState('source')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const selected = useMemo(() => extraction?.statements?.find((item) => item.statement_id === selectedStatement), [extraction, selectedStatement])

  const chooseFile = (nextFile) => {
    setFile(nextFile)
    setExtraction(null)
    setResult(null)
    setDecisionResult(null)
    setError('')
    if (nextFile && !ruleName) setRuleName(`Proposed rule from ${nextFile.name.replace(/\.pdf$/i, '')}`.slice(0, 256))
  }

  const extractText = async (event) => {
    event.preventDefault()
    if (!file) return
    setBusy('extract')
    setError('')
    try {
      const form = new FormData()
      form.append('file', file)
      const response = await fetch('/api/proposed-rules/extract', { method: 'POST', body: form })
      const data = await readResponse(response)
      setExtraction(data)
      setSelectedStatement(data.statements?.[0]?.statement_id || '')
      setResult(null)
      setDecisionResult(null)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy('')
    }
  }

  const compare = async (event) => {
    event.preventDefault()
    if (!extraction || !selected) return
    setBusy('compare')
    setError('')
    setResult(null)
    setDecisionResult(null)
    try {
      const response = await fetch('/api/proposed-rules/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          analysis_id: extraction.analysis_id,
          statement_id: selected.statement_id,
          rule_name: ruleName,
          purpose,
          condition_field: conditionField,
          condition_operator: conditionOperator,
          condition_value: conditionValue,
          action_type: actionType,
          action_message: actionMessage,
          action_max_amount: actionType === 'set_limit' && maxAmount !== '' ? Number(maxAmount) : null,
          affected_transaction_notes: affectedNotes,
          limit_notes: limitNotes,
          exception_notes: exceptionNotes,
        }),
      })
      setResult(await readResponse(response))
      setResultTab('source')
      setDecision('')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy('')
    }
  }

  const saveDecision = async () => {
    if (!decision || !result) return
    setBusy('decision')
    setError('')
    try {
      const response = await fetch('/api/proposed-rules/decisions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ analysis_id: result.analysis_id, decision, authority_notes: authorityNotes }),
      })
      setDecisionResult(await readResponse(response))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5 p-5 sm:p-7">
      <header>
        <p className="text-sm font-semibold uppercase tracking-wide text-blue-700">Proposed Rule Review</p>
        <h1 className="mt-1 text-3xl font-bold text-slate-900">Check a proposed rule</h1>
        <p className="mt-2 max-w-4xl text-slate-600">Upload a proposal, inspect indexed source passages, and record a local review outcome.</p>
      </header>

      <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950" role="note">
        <div className="flex gap-3"><ShieldAlert className="mt-0.5 shrink-0" size={20} /><p><strong>Review only.</strong> This page does not establish legal applicability or safety. Wording matches are screening signals; current verified executable rules and genuine transaction records are not available for impact testing.</p></div>
      </div>

      {error && <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800" role="alert">{error}</div>}

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="mb-5 flex items-start gap-3"><span className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 font-bold text-blue-800">1</span><div><h2 className="text-xl font-semibold text-slate-900">Upload proposed rule</h2><p className="mt-1 text-sm text-slate-600">Text is extracted on this local backend. No AI provider is called by this workflow.</p></div></div>
        <form onSubmit={extractText} className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <label className="flex min-h-12 flex-1 cursor-pointer items-center gap-3 rounded-lg border border-dashed border-slate-400 px-4 py-3 hover:bg-slate-50">
            <FileUp size={20} className="text-blue-700" />
            <span className="min-w-0 truncate text-sm text-slate-700">{file?.name || 'Choose a proposed-rule PDF (maximum 15 MB)'}</span>
            <input className="sr-only" type="file" accept="application/pdf,.pdf" onChange={(event) => chooseFile(event.target.files?.[0] || null)} />
          </label>
          <button disabled={!file || busy !== ''} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-blue-700 px-5 font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50">
            {busy === 'extract' && <Loader2 className="animate-spin" size={18} />}{busy === 'extract' ? 'Reading PDF…' : 'Extract source text'}
          </button>
        </form>
        {extraction && <div className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-700"><p><strong>{extraction.file_name}</strong> · {extraction.pages_processed} readable pages · proposal reference {extraction.proposal_id}</p>{extraction.extraction_warning && <p className="mt-2 text-amber-800" role="status">{extraction.extraction_warning}</p>}{!extraction.statements?.length && <p className="mt-2">No rule can be compared until a readable source statement is available.</p>}</div>}
      </section>

      {extraction?.statements?.length > 0 && <form onSubmit={compare} className="space-y-4">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <div className="mb-5 flex items-start gap-3"><span className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 font-bold text-blue-800">2</span><div><h2 className="text-xl font-semibold text-slate-900">Review and edit the proposed rule</h2><p className="mt-1 text-sm text-slate-600">The source passages below are copied from the PDF. Text signals are suggestions only; confirm every field against the source.</p></div></div>
          <div className="grid gap-5 lg:grid-cols-2">
            <div className="space-y-4">
              <label className="block text-sm font-medium text-slate-700">Source statement
                <select required value={selectedStatement} onChange={(event) => setSelectedStatement(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-3">
                  {extraction.statements.map((item) => <option value={item.statement_id} key={item.statement_id}>Page {item.page}{item.section_reference ? ` · Section ${item.section_reference}` : ''} · {item.quote.replace(/\s+/g, ' ').slice(0, 90)}</option>)}
                </select>
              </label>
              {selected && <details className="rounded-lg border border-blue-200 bg-blue-50 p-4"><summary className="cursor-pointer text-sm font-semibold text-blue-900">Inspect extracted source text and preliminary clues</summary><p className="mt-3 text-xs font-semibold uppercase text-blue-800">Page {selected.page}{selected.section_reference ? ` · section ${selected.section_reference}` : ''}</p><blockquote className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-800">{selected.quote}</blockquote><div className="mt-3 border-t border-blue-200 pt-3 text-xs text-blue-950"><p><strong>Possible text clues (check against source):</strong> Action: {selected.text_signals.possible_action || 'not identified'} · Transaction fields: {selected.text_signals.possible_transaction_fields.join(', ') || 'not identified'}</p>{selected.text_signals.possible_purpose_text && <p className="mt-1">Possible purpose wording: {selected.text_signals.possible_purpose_text}</p>}{selected.text_signals.possible_condition_text && <p className="mt-1">Possible condition wording: {selected.text_signals.possible_condition_text}</p>}{selected.text_signals.possible_limit_text.length > 0 && <p className="mt-1">Possible limit wording: {selected.text_signals.possible_limit_text.join(' … ')}</p>}{selected.text_signals.possible_exception_text.length > 0 && <p className="mt-1">Possible exception wording: {selected.text_signals.possible_exception_text.join(' … ')}</p>}</div></details>}
              <label className="block text-sm font-medium text-slate-700">Rule name<input required maxLength={256} value={ruleName} onChange={(event) => setRuleName(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 p-3" /></label>
              <label className="block text-sm font-medium text-slate-700">Purpose, in your words<textarea maxLength={1000} rows={3} value={purpose} onChange={(event) => setPurpose(event.target.value)} placeholder="Enter only what the source supports" className="mt-1 w-full rounded-lg border border-slate-300 p-3" /></label>
              <label className="block text-sm font-medium text-slate-700">Affected transactions / conditions<textarea maxLength={1000} rows={3} value={affectedNotes} onChange={(event) => setAffectedNotes(event.target.value)} placeholder="Describe which transactions this proposal would affect" className="mt-1 w-full rounded-lg border border-slate-300 p-3" /></label>
              <label className="block text-sm font-medium text-slate-700">Limits<textarea maxLength={1000} rows={2} value={limitNotes} onChange={(event) => setLimitNotes(event.target.value)} placeholder="Add limits only when supported by the source" className="mt-1 w-full rounded-lg border border-slate-300 p-3" /></label>
              <label className="block text-sm font-medium text-slate-700">Exceptions<textarea maxLength={1000} rows={2} value={exceptionNotes} onChange={(event) => setExceptionNotes(event.target.value)} placeholder="Add exceptions only when supported by the source" className="mt-1 w-full rounded-lg border border-slate-300 p-3" /></label>
            </div>
            <div className="space-y-4 rounded-xl bg-slate-50 p-4 sm:p-5">
              <h3 className="font-semibold text-slate-900">Structured proposal fields</h3>
              <p className="text-sm text-slate-600">These are analyst-entered comparison fields. They are not extracted, legally verified, or executed against transactions.</p>
              <label className="block text-sm font-medium text-slate-700">Transaction field<select value={conditionField} onChange={(event) => setConditionField(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-3">{TRANSACTION_FIELDS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm font-medium text-slate-700">Condition<select value={conditionOperator} onChange={(event) => setConditionOperator(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-3">{OPERATORS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="block text-sm font-medium text-slate-700">Value<input required value={conditionValue} onChange={(event) => setConditionValue(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-3" placeholder="For example, 50000" /></label></div>
              <label className="block text-sm font-medium text-slate-700">Expected action<select value={actionType} onChange={(event) => setActionType(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-3">{ACTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              {actionType === 'set_limit' && <label className="block text-sm font-medium text-slate-700">Limit amount<input required type="number" min="0" value={maxAmount} onChange={(event) => setMaxAmount(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-3" /></label>}
              <label className="block text-sm font-medium text-slate-700">Action note<input maxLength={500} value={actionMessage} onChange={(event) => setActionMessage(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white p-3" placeholder="Optional explanation" /></label>
              <button disabled={!selected || busy !== ''} className="inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-lg bg-blue-700 px-5 font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50">
                {busy === 'compare' && <Loader2 className="animate-spin" size={18} />}{busy === 'compare' ? 'Comparing available source evidence…' : 'Review available source evidence'}
              </button>
            </div>
          </div>
        </section>
      </form>}

      {result && <div className="space-y-4" aria-live="polite">
        <section className="grid gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:grid-cols-[1fr_auto] lg:items-center">
          <div><p className="text-xs font-semibold uppercase tracking-wide text-blue-700">Proposed rule summary</p><h2 className="mt-1 text-xl font-semibold text-slate-900">{result.proposal.name}</h2><p className="mt-1 text-sm text-slate-600">{result.proposal.rule_id} · v{result.proposal.rule_version} · {result.proposal.verification_status.replaceAll('_', ' ')}</p><p className="mt-2 text-xs text-slate-500">Source: {result.proposal.uploaded_file_name} · statement page {result.proposal.source_statement.page}{result.proposal.source_statement.section_reference ? ` · section ${result.proposal.source_statement.section_reference}` : ''}</p></div>
          <span className="h-fit rounded-full bg-amber-100 px-3 py-1.5 text-xs font-semibold text-amber-900">{result.assessment_status.replaceAll('_', ' ')}</span>
          <p className="text-sm text-slate-700 lg:col-span-2">{result.status_message}</p><p className="rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-950 lg:col-span-2">{result.comparison_questions.evidence_sufficiency.message}</p>
        </section>

        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div role="tablist" aria-label="Review evidence sections" className="flex flex-wrap gap-1 border-b border-slate-200 p-2">{[['source','Source evidence'],['wording','Related wording'],['structured','Structured comparison'],['execution','Execution availability']].map(([key,label]) => <button type="button" role="tab" aria-selected={resultTab === key} key={key} onClick={() => setResultTab(key)} className={`min-h-10 rounded-lg px-3 text-sm font-medium ${resultTab === key ? 'bg-blue-700 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>{label}</button>)}</div>
          <div role="tabpanel" className="space-y-4 p-5 sm:p-6">
            {resultTab === 'source' && <><div><h3 className="font-semibold">Uploaded source statement</h3><p className="mt-1 text-xs text-slate-500">{result.proposal.source_statement.statement_id} · page {result.proposal.source_statement.page}{result.proposal.source_statement.section_reference ? ` · section ${result.proposal.source_statement.section_reference}` : ''}</p><blockquote className="mt-3 whitespace-pre-wrap border-l-2 border-blue-300 pl-4 text-sm leading-6 text-slate-800">{result.proposal.source_statement.quote}</blockquote></div><p className="text-sm text-slate-600">Uploaded file hash: <span className="break-all font-mono text-xs">{result.proposal.uploaded_file_sha256}</span></p><p className="text-sm text-slate-700">{result.scope_warning}</p></>}
            {resultTab === 'wording' && <><p className="text-sm text-slate-600">Wording similarity is a screening signal only; it does not prove duplication, equivalence, applicability, or conflict.</p><p className="text-sm text-slate-700">Internal policy repository: {result.bank_rules.message} <Link className="font-medium text-blue-700 underline" to="/bank-policies">Open Bank Policies</Link></p>{result.rbi_matches.length ? <div className="space-y-3">{result.rbi_matches.map(match => <article key={match.evidence_id} className="rounded-lg border border-slate-200 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{match.document_name || match.document_id}</h3><span className="rounded-full bg-slate-100 px-2 py-1 text-xs">{match.classification.replaceAll('_', ' ')}</span></div><p className="mt-1 text-xs text-slate-500">{match.document_id} · {match.evidence_id}{match.section ? ` · ${match.section}` : ''}{match.paragraph ? ` · paragraph ${match.paragraph}` : ''}{match.page ? ` · page ${match.page}` : ''} · status {match.document_status || 'unknown'}</p><blockquote className="mt-3 border-l-2 border-slate-300 pl-3 text-sm leading-5">{match.quote}</blockquote><p className="mt-2 text-xs text-slate-600">{match.why_related} {match.uncertainty}</p>{match.source_url && <a className="mt-2 inline-block text-sm text-blue-700 underline" href={match.source_url} target="_blank" rel="noreferrer">Open registered source reference</a>}</article>)}</div> : <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-700">No related wording was found in the indexed passages. This does not establish that the proposal is safe or conflict-free.</p>}{result.bank_rules.screening_matches?.length > 0 && <details className="rounded-lg border border-slate-200 p-4"><summary className="cursor-pointer text-sm font-semibold">Internal policy text-screening matches ({result.bank_rules.screening_matches.length})</summary><div className="mt-3 space-y-3">{result.bank_rules.screening_matches.map(match => <article key={`${match.policy_id}:${match.policy_version}:${match.clause_id}`} className="border-t border-slate-100 pt-3"><p className="text-sm font-medium">{match.policy_title || 'Title unknown'} · {match.policy_id} · v{match.policy_version} · clause {match.clause_id}</p><p className="mt-1 text-xs text-slate-500">{match.policy_verification_status} · {match.lifecycle_status} · mapping {match.executable_rule_verification_status}</p><blockquote className="mt-2 text-sm text-slate-700">{match.clause_text}</blockquote><p className="mt-1 text-xs text-amber-800">{match.explanation} {match.missing_information?.join('; ')}</p></article>)}</div></details>}</>}
            {resultTab === 'structured' && <><p className="text-sm text-slate-600">Conditions and actions are compared only when source rules are verified/current and both structured definitions pass validation.</p>{result.rule_comparisons.length ? <div className="space-y-3">{result.rule_comparisons.map((pair,index) => <details key={`${pair.existing_rule.source_category}-${pair.existing_rule.evidence_reference}-${index}`} className="rounded-lg border border-slate-200 p-4"><summary className="cursor-pointer text-sm font-semibold">{pair.existing_rule.source_category.replaceAll('_',' ')} · {pair.existing_rule.rule_id || pair.existing_rule.policy_id || pair.existing_rule.evidence_reference || 'source'} · {pair.structured_comparison.category.replaceAll('_',' ')}</summary><p className="mt-2 text-xs text-slate-500">{pair.existing_rule.source_document || 'Source document unknown'}{pair.existing_rule.clause_id ? ` · clause ${pair.existing_rule.clause_id}` : ''} · verification {pair.existing_rule.verification_status} · lifecycle {pair.existing_rule.lifecycle_status}</p><p className="mt-2 text-sm">{pair.structured_comparison.explanation}</p>{pair.structured_comparison.missing_information?.length > 0 && <p className="mt-2 text-xs text-amber-800">Still needed: {pair.structured_comparison.missing_information.join('; ')}</p>}</details>)}</div> : <p className="rounded-lg bg-amber-50 p-4 text-sm text-amber-950">{result.comparison_questions.condition_action_equivalence.message} {result.conflict_detection.message}</p>}</>}
            {resultTab === 'execution' && <div className="space-y-3"><h3 className="font-semibold">Transaction outcome comparison</h3><p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-700">{result.transaction_comparison.message}</p><p className="text-sm text-slate-600">Status: {result.comparison_questions.execution.status.replaceAll('_',' ')} · no transaction outputs were generated.</p><p className="text-xs text-slate-500">Synthetic demonstration data remains isolated in the secondary Analyzer & synthetic demo tool.</p></div>}
          </div>
        </section>

        <section className="rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-6"><div><h2 className="text-lg font-semibold">Human review decision</h2><p className="mt-1 text-sm text-slate-600">Choose a local workflow outcome after reviewing the source and evidence.</p></div><div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"><strong>Local record only.</strong> Reviewer identity is not authenticated. This does not constitute bank-authority approval and cannot activate or deploy a rule.</div><div className="mt-4 grid gap-2 sm:grid-cols-3">{[['approve_for_next_stage','Advance for next-stage review'],['request_changes','Request changes'],['reject','Reject proposal']].map(([value,label]) => <button key={value} type="button" onClick={() => { setDecision(value); setDecisionResult(null) }} aria-pressed={decision === value} className={`min-h-11 rounded-lg border px-3 py-2 text-sm font-semibold ${decision === value ? 'border-blue-700 bg-blue-50 text-blue-900' : 'border-slate-300 text-slate-700 hover:bg-slate-50'}`}>{label}</button>)}</div><label className="mt-3 block text-sm font-medium">Notes (optional)<textarea maxLength={2000} rows={3} value={authorityNotes} onChange={event => setAuthorityNotes(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 p-3" /></label><div className="mt-3 flex flex-wrap items-center gap-3"><button type="button" onClick={saveDecision} disabled={!decision || busy !== '' || Boolean(decisionResult)} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-slate-900 px-5 font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">{busy === 'decision' && <Loader2 className="animate-spin" size={18} />}{busy === 'decision' ? 'Saving local record…' : 'Save local decision'}</button>{decisionResult && <p role="status" className="text-sm text-green-800">{decisionResult.message}</p>}</div></section>
      </div>}
    </div>
  )
}
