export const EXTRACTION_REQUEST_TIMEOUT_MS = 7 * 60 * 1000

export async function fetchWithTimeout(resource, options = {}, timeoutMs = EXTRACTION_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    return await fetch(resource, { ...options, signal: controller.signal })
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error('Extraction timed out while waiting for the local analysis workflow. Check provider settings and try again.')
    }
    throw error
  } finally {
    clearTimeout(timer)
  }
}
