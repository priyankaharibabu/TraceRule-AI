# Phase 4 — NVIDIA LLM Connectivity

Phase 4 provides an optional, bounded connection to NVIDIA-hosted chat models. The model can suggest a structured extraction candidate from one validated Phase 2 evidence passage. Every result remains provisional and requires human review. The integration does not change obligation files, make regulatory decisions, approve rules, or execute model output.

## Setup

1. Copy `.env.example` to `.env` and set `NVIDIA_API_KEY` to an NVIDIA API key. `.env` is ignored by Git.
2. Set `NVIDIA_MODEL` to a model currently enabled for your account. The default is `openai/gpt-oss-20b`; NVIDIA's catalog and availability can change. The account's selected model is checked by making one small completion.
3. Configure timeout, retry count, token limit, temperature, and JSON mode in `.env` as needed.

The service uses Python's standard library and NVIDIA's OpenAI-compatible `POST /v1/chat/completions` endpoint at `https://integrate.api.nvidia.com`. Structured JSON mode sends `response_format: {"type":"json_object"}`. If the selected model does not support it, the request fails closed; the client does not accept a free-form fallback. See [NVIDIA NIM LLM API reference](https://docs.api.nvidia.com/nim/reference/llm-apis) and [NVIDIA model example](https://build.nvidia.com/openai/gpt-oss-20b).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `NVIDIA_API_KEY` | empty | Required secret; process environment overrides `.env` |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | HTTPS API base URL |
| `NVIDIA_MODEL` | `openai/gpt-oss-20b` | Account-enabled model identifier |
| `NVIDIA_TIMEOUT_SECONDS` | `20` | Per-request timeout (1–300) |
| `NVIDIA_RETRY_COUNT` | `2` | Retries after transient network, timeout, 429, or 5xx failures (0–5) |
| `NVIDIA_MAX_TOKENS` | `512` | Completion token limit |
| `NVIDIA_TEMPERATURE` | `0.0` | Sampling temperature (0–2) |
| `NVIDIA_JSON_MODE` | `true` | Request JSON object mode |

No API key is needed to run tests. Do not put secrets in source files or logs.

## Services

- `backend.services.nvidia_llm.LLMClient.complete(...)` is the shared chat interface and returns content, model, latency, retry count, and token usage when supplied. Errors are sanitized and structured.
- `backend.services.nvidia_health.check_nvidia_health()` returns a structured status and catches configuration and transient failures. A successful small completion confirms authentication, network reachability, and availability of the configured model. It does not enumerate the NVIDIA catalog.
- `backend.services.structured_extraction.extract_candidate_obligation(evidence_id)` accepts only evidence IDs in the validated Phase 2 dataset. It checks the evidence document/page references, validates strict JSON against the supported response shape, and returns the candidate in memory. It never writes to `obligations.csv` or a review queue.

The extraction confidence describes faithful extraction only; it is not legal confidence. Passage text is sent to the configured hosted model. Do not use this prototype with personal or confidential information. No request or response body is logged; logs contain timestamp, model, latency, token usage when available, status, and retry count.

## Commands (PowerShell)

From the project directory:

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py" -v
.\.venv\Scripts\python.exe -c "import json; from backend.services.nvidia_health import check_nvidia_health; print(json.dumps(check_nvidia_health(), indent=2))"
```

With a key configured, the health check sends a minimal connectivity prompt and may consume a small number of tokens. It reports `not_configured` without making a network request when the key is missing.

## Troubleshooting and limits

- `missing_api_key` / `not_configured`: set `NVIDIA_API_KEY` in the process environment or `.env`.
- `authentication_failed`: verify the key and account access; credentials are never included in error output.
- `provider_error` or model unavailable: confirm the model identifier is enabled for the account and try a currently available NVIDIA catalog model.
- `timeout`, `network_error`, or `rate_limited`: check network access and retry later; configured retries are bounded.
- `invalid_provider_response`, `malformed_json`, or a schema validation error: the response is rejected; no loose parsing or free-form fallback occurs.

Model catalog, access, supported parameters, and JSON-mode behavior vary over time and by model. This connection layer is not an obligation reviewer and cannot resolve DOC-CB-2025 status, amendment incorporation, provision applicability, or the DOC-DL-2025 relationship. Phase 5 agents, if later authorized, should call this wrapper and keep all candidates pending human review.
