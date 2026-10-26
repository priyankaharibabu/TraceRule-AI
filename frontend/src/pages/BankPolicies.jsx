import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, FileUp, Loader2, ShieldAlert } from 'lucide-react'

async function readResponse(response) {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || 'The request could not be completed.')
  return payload
}

export default function BankPolicies() {
  const [repository, setRepository] = useState(null)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = useCallback(async () => {
    setBusy('load')
    setError('')
    try {
      setRepository(await readResponse(await fetch('/api/bank-policies')))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  async function createPreview(event) {
    event.preventDefault()
    if (!file) return
    setBusy('preview')
    setError('')
    setNotice('')
    setPreview(null)
    try {
      const form = new FormData()
      form.append('file', file)
      setPreview(await readResponse(await fetch('/api/bank-policies/import/preview', { method: 'POST', body: form })))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  async function commitPreview() {
    if (!preview?.preview_id) return
    setBusy('commit')
    setError('')
    setNotice('')
    try {
      const result = await readResponse(await fetch('/api/bank-policies/import/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preview_id: preview.preview_id }),
      }))
      setNotice(result.message)
      setPreview(null)
      setFile(null)
      await refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="min-h-screen p-5 sm:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <header>
          <p className="text-sm font-semibold uppercase tracking-wide text-blue-700">Internal policy repository</p>
          <h1 className="mt-1 text-3xl font-bold text-slate-900">Bank Policies</h1>
          <p className="mt-2 max-w-3xl text-slate-600">Review imported policy versions and clauses, then preview a CSV or JSON export before saving it.</p>
        </header>

        <section className="rounded-2xl border border-amber-300 bg-amber-50 p-5" aria-label="Prototype access limits">
          <div className="flex gap-3"><ShieldAlert className="mt-0.5 shrink-0 text-amber-800" size={22} /><div><h2 className="font-semibold text-amber-950">Local prototype access</h2><p className="mt-1 text-sm text-amber-900">This page is restricted to a local connection, but it has no user authentication. Imported source and approval claims are stored as unverified; importing does not approve or activate a policy.</p></div></div>
        </section>

        {error && <div role="alert" className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-900">{error}</div>}
        {notice && <div role="status" className="rounded-xl border border-green-300 bg-green-50 p-4 text-sm text-green-900"><CheckCircle2 className="mr-2 inline" size={18} />{notice}</div>}

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-7">
          <h2 className="text-xl font-semibold text-slate-900">Import policy data</h2>
          <p className="mt-1 text-sm text-slate-600">Choose an authoritative bank export in UTF-8 CSV or JSON format (up to 10 MB). The original file is read only. Review the validation preview before committing.</p>
          <form onSubmit={createPreview} className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end">
            <label className="block flex-1 text-sm font-medium text-slate-700">CSV or JSON file<input type="file" accept=".csv,.json,text/csv,application/json" onChange={(event) => { setFile(event.target.files?.[0] || null); setPreview(null); setError('') }} className="mt-2 block w-full rounded-lg border border-slate-300 p-3" /></label>
            <button disabled={!file || busy !== ''} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-blue-700 px-5 font-semibold text-white hover:bg-blue-800 disabled:opacity-50">{busy === 'preview' ? <Loader2 className="animate-spin" size={18} /> : <FileUp size={18} />}{busy === 'preview' ? 'Validating…' : 'Preview import'}</button>
          </form>
          <p className="mt-3 text-xs text-slate-500">The import format and field meanings are documented in docs/BANK_POLICY_REPOSITORY.md. Missing provenance or approval details remain visible as incomplete; no values are filled in for you.</p>
        </section>

        {preview && <section className="rounded-2xl border border-blue-200 bg-white p-5 shadow-sm sm:p-7" aria-live="polite">
          <h2 className="text-xl font-semibold text-slate-900">Import validation preview</h2>
          <p className="mt-1 text-sm text-slate-600">{preview.message} Source: {preview.source_filename} · SHA-256 {preview.source_sha256}</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-3"><Count label="Accepted versions" value={preview.summary.accepted_policy_versions} /><Count label="Incomplete versions" value={preview.summary.incomplete_policy_versions} /><Count label="Rejected versions" value={preview.summary.rejected_policy_versions} /><Count label="Duplicate records" value={preview.summary.duplicate_records} /><Count label="Accepted clauses" value={preview.summary.accepted_clauses} /><Count label="Incomplete clauses" value={preview.summary.incomplete_clauses} /></div>
          {[['Ready to save', preview.accepted], ['Incomplete; saved as unverified if committed', preview.incomplete]].map(([label, items]) => <div className="mt-5" key={label}><h3 className="font-semibold text-slate-800">{label}</h3>{items.length ? <ul className="mt-2 space-y-2">{items.map((item) => <li className="rounded-lg border border-slate-200 p-3 text-sm" key={`${item.policy_id}:${item.version}`}><strong>{item.policy_title || 'Title missing'}</strong> · {item.policy_id} · version {item.version} · {item.clause_count} clauses{item.missing_information?.length > 0 && <p className="mt-1 text-amber-800">Missing: {item.missing_information.join('; ')}</p>}</li>)}</ul> : <p className="mt-2 text-sm text-slate-500">None.</p>}</div>)}
          {preview.errors.length > 0 && <div className="mt-5"><h3 className="font-semibold text-slate-800">Validation issues</h3><ul className="mt-2 max-h-56 space-y-2 overflow-auto">{preview.errors.map((item, index) => <li className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900" key={`${item.row}-${index}`}>Row {item.row} · {item.type}: {item.message}</li>)}</ul></div>}
          <button type="button" onClick={commitPreview} disabled={busy !== '' || (!preview.summary.accepted_policy_versions && !preview.summary.incomplete_policy_versions)} className="mt-6 min-h-12 rounded-lg bg-slate-900 px-5 font-semibold text-white disabled:opacity-50">{busy === 'commit' ? 'Saving validated records…' : 'Commit previewed records'}</button>
        </section>}

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-7">
          <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-semibold text-slate-900">Policy versions and clauses</h2><p className="mt-1 text-sm text-slate-600">{repository ? `${repository.policy_count} policies · ${repository.version_count} versions · ${repository.clause_count} clauses` : 'Loading repository…'}</p></div><button type="button" onClick={refresh} disabled={busy !== ''} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700">{busy === 'load' ? 'Loading…' : 'Refresh'}</button></div>
          {busy === 'load' && !repository ? <p className="mt-5 flex items-center gap-2 text-sm text-slate-600"><Loader2 className="animate-spin" size={18} />Loading repository…</p> : repository?.policy_count === 0 ? <p className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">{repository?.message}</p> : <div className="mt-5 space-y-4">{repository?.policies.map((policy) => <article key={policy.policy_id} className="rounded-xl border border-slate-200 p-4"><h3 className="font-semibold text-slate-900">{policy.policy_title || 'Untitled policy'} <span className="font-normal text-slate-500">· {policy.policy_id}</span></h3><p className="mt-1 text-sm text-slate-600">{policy.policy_category || 'Category not supplied'}{policy.owning_organization ? ` · ${policy.owning_organization}` : ''}</p><div className="mt-3 space-y-3">{policy.versions.map((version) => <div className="rounded-lg bg-slate-50 p-3" key={`${policy.policy_id}:${version.version}`}><div className="flex flex-wrap items-center gap-2"><strong className="text-sm">Version {version.version}</strong><span className="rounded-full bg-slate-200 px-2 py-1 text-xs">{version.status}</span><span className="rounded-full bg-amber-100 px-2 py-1 text-xs text-amber-900">{version.provenance?.verification_status || 'unverified'}</span><span className="text-xs text-slate-500">Effective {version.effective_from || 'date missing'}{version.effective_to ? ` through ${version.effective_to}` : ''}</span></div><p className="mt-2 text-xs text-slate-600">Policy source: {version.source_document_reference || 'not supplied'} · Import file: {version.provenance?.source_filename || 'unknown'} · Approval events: {version.approval_details?.length || 0} · Completeness: {version.import_completeness?.status || 'unknown'}</p><div className="mt-3 space-y-2">{version.clauses.map((clause) => <details className="rounded-md border border-slate-200 bg-white p-3" key={`${clause.clause_id}:${clause.record_created_at}`}><summary className="cursor-pointer text-sm font-medium text-slate-800">{clause.clause_title || clause.clause_id} <span className="font-normal text-slate-500">· {clause.clause_verification_status || clause.verification_status}</span></summary><p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{clause.clause_text}</p><p className="mt-2 text-xs text-slate-500">{clause.source_document_reference || 'Source document reference missing'}{clause.section ? ` · ${clause.section}` : ''}{clause.paragraph ? ` · paragraph ${clause.paragraph}` : ''}{clause.page ? ` · page ${clause.page}` : ''}</p></details>)}</div></div>)}</div></article>)}</div>}
        </section>

        <p className="flex gap-2 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950"><AlertTriangle className="shrink-0" size={18} />Text matches in proposed-rule review are screening signals only. Imported policies stay unverified and cannot be treated as active requirements or used for transaction outcomes.</p>
      </div>
    </div>
  )
}

function Count({ label, value }) {
  return <div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold text-slate-900">{value}</p></div>
}
