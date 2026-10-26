import { useState } from 'react'
import { Play, FileText, CheckCircle, AlertCircle, Loader2, FlaskConical } from 'lucide-react'

const mockEvidence = [
  { id: 'DOC-CB-2025-CH3-P006', paragraph: '6', page: 18, selected: false },
  { id: 'DOC-CB-2025-CH3-P007', paragraph: '7', page: 18, selected: false },
  { id: 'DOC-CB-2025-CH3-P008', paragraph: '8', page: 19, selected: false },
  { id: 'DOC-CB-2025-CH3-P009', paragraph: '9', page: 19, selected: false },
]

const mockExtractionResult = {
  status: 'success',
  candidate: {
    id: 'CAND-NEW-001',
    obligation: 'Digital lending by a bank involving a LSP, shall be carried out under a contractual agreement between the bank and the LSP, which clearly defines the respective roles, rights, and obligations of each party thereto.',
    type: 'explicit_requirement',
    confidence: 0.92,
    grounded: true,
    sourceQuote: 'Digital lending by a bank involving a LSP, shall be carried out under a contractual agreement between the bank and the LSP, which clearly defines the respective roles, rights, and obligations of each party thereto.',
    sourceId: 'DOC-CB-2025-CH3-P006',
    sourcePage: 18
  }
}

export default function Phase5Extraction() {
  const [documentId, setDocumentId] = useState('DOC-CB-2025')
  const [evidence, setEvidence] = useState(mockEvidence)
  const [isRunning, setIsRunning] = useState(false)
  const [result, setResult] = useState(null)

  const toggleEvidence = (id) => {
    setEvidence(evidence.map(e => 
      e.id === id ? { ...e, selected: !e.selected } : e
    ))
  }

  const runExtraction = async () => {
    const selected = evidence.filter(e => e.selected)
    if (selected.length === 0) {
      alert('Please select at least one evidence passage')
      return
    }

    setIsRunning(true)
    setResult(null)

    // Simulate API call
    await new Promise(resolve => setTimeout(resolve, 2000))
    
    setResult(mockExtractionResult)
    setIsRunning(false)
  }

  const selectedCount = evidence.filter(e => e.selected).length

  return (
    <div className="p-6 animate-fade-in">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Phase 5: Extraction</h1>
        <p className="text-gray-500 mt-1">Run AI extraction on regulatory evidence passages</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Selection Panel */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="font-semibold text-gray-900 mb-4">Select Evidence</h2>
          
          {/* Document Selection */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Document
            </label>
            <select
              value={documentId}
              onChange={(e) => setDocumentId(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="DOC-CB-2025">DOC-CB-2025 (Commercial Banks)</option>
              <option value="DOC-DL-2025">DOC-DL-2025 (Digital Lending)</option>
            </select>
          </div>

          {/* Evidence List */}
          <div className="space-y-2 mb-6">
            {evidence.map((item) => (
              <label
                key={item.id}
                className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                  item.selected 
                    ? 'border-primary-500 bg-primary-50' 
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <input
                  type="checkbox"
                  checked={item.selected}
                  onChange={() => toggleEvidence(item.id)}
                  className="w-4 h-4 text-primary-600 rounded focus:ring-primary-500"
                />
                <div className="flex-1">
                  <p className="font-medium text-gray-900">{item.id}</p>
                  <p className="text-sm text-gray-500">Paragraph {item.paragraph} • Page {item.page}</p>
                </div>
              </label>
            ))}
          </div>

          {/* Run Button */}
          <button
            onClick={runExtraction}
            disabled={isRunning || selectedCount === 0}
            className={`w-full flex items-center justify-center gap-2 py-3 rounded-lg font-medium transition-colors ${
              isRunning || selectedCount === 0
                ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                : 'bg-primary-600 text-white hover:bg-primary-700'
            }`}
          >
            {isRunning ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                Running Extraction...
              </>
            ) : (
              <>
                <Play className="w-5 h-5" />
                Run Extraction ({selectedCount} selected)
              </>
            )}
          </button>
        </div>

        {/* Results Panel */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="font-semibold text-gray-900 mb-4">Extraction Results</h2>

          {!result && !isRunning && (
            <div className="text-center py-12">
              <FlaskConical className="w-16 h-16 text-gray-300 mx-auto mb-4" />
              <p className="text-gray-500">Select evidence and run extraction</p>
              <p className="text-sm text-gray-400 mt-2">Results will appear here</p>
            </div>
          )}

          {result && (
            <div className="space-y-4">
              {/* Status */}
              <div className={`flex items-center gap-2 p-3 rounded-lg ${
                result.status === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
              }`}>
                {result.status === 'success' ? (
                  <CheckCircle className="w-5 h-5" />
                ) : (
                  <AlertCircle className="w-5 h-5" />
                )}
                <span className="font-medium">
                  {result.status === 'success' ? 'Extraction Successful' : 'Extraction Failed'}
                </span>
              </div>

              {/* Candidate Details */}
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <div className="bg-gray-50 px-4 py-2 border-b border-gray-200">
                  <h3 className="font-medium text-gray-900">Candidate Details</h3>
                </div>
                <div className="p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-500">ID</span>
                    <span className="font-mono text-sm">{result.candidate.id}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-500">Type</span>
                    <span className="px-2 py-1 bg-blue-100 text-blue-700 text-xs rounded">
                      {result.candidate.type}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-500">Confidence</span>
                    <span className={`font-medium ${
                      result.candidate.confidence >= 0.8 ? 'text-green-600' : 'text-yellow-600'
                    }`}>
                      {(result.candidate.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-gray-500">Grounded</span>
                    <span className={`flex items-center gap-1 text-sm ${
                      result.candidate.grounded ? 'text-green-600' : 'text-yellow-600'
                    }`}>
                      {result.candidate.grounded ? (
                        <><CheckCircle size={14} /> Yes</>
                      ) : (
                        <><AlertCircle size={14} /> No (interpretation)</>
                      )}
                    </span>
                  </div>
                  
                  <div className="pt-3 border-t border-gray-200">
                    <p className="text-sm text-gray-500 mb-2">Extracted Obligation</p>
                    <p className="text-sm text-gray-700 bg-gray-50 p-3 rounded-lg">
                      {result.candidate.obligation}
                    </p>
                  </div>

                  <div>
                    <p className="text-sm text-gray-500 mb-2">Source Quote</p>
                    <p className="text-sm text-gray-700 bg-primary-50 p-3 rounded-lg border border-primary-200">
                      {result.candidate.sourceQuote}
                    </p>
                  </div>

                  <div className="flex items-center justify-between text-sm text-gray-500">
                    <span>Source: {result.candidate.sourceId}</span>
                    <span>Page {result.candidate.sourcePage}</span>
                  </div>
                </div>
              </div>

              {/* Actions */}
              <div className="flex gap-3">
                <button className="flex-1 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors">
                  Submit for Review
                </button>
                <button className="flex-1 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-colors">
                  View Details
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}