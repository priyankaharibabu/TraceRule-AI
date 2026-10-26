import test from 'node:test'
import assert from 'node:assert/strict'
import { fetchWithTimeout } from '../src/utils/fetchWithTimeout.js'

test('a request with no response aborts with a clear timeout error', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (_resource, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
  })

  try {
    await assert.rejects(fetchWithTimeout('/mock-stalled-request', {}, 20), /Extraction timed out/)
  } finally {
    globalThis.fetch = originalFetch
  }
})
