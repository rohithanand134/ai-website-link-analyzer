# Dxora assessment audit

The baseline audit and hosted smoke tests were captured on 22 November 2026 against Render commit `709401d`. The 26 November follow-up added stricter evidence checks, URL redaction, request limits, and keyless competitor discovery. The deployment record identifies the revision that served the saved baseline responses.

## Before and after

PASS means implemented and checked. PARTIAL means some behavior or verification remains outstanding. FAIL means the requirement was missing or broken. UNVERIFIED means the named execution could not be performed.

| Requirement | Before | After | Verification |
| --- | --- | --- | --- |
| FastAPI endpoints and errors | PASS | PASS | Schema, status, JSON serialization and deployed-route checks. |
| Website extraction | PASS | PASS | Metadata, size, timeout and empty-page tests. Live business and minimal URLs succeeded. |
| URL validation and SSRF | PASS | PASS | URL policy, validated IP, original TLS identity, mixed DNS, redirect and proxy tests. Live private addresses were rejected. |
| Gemini and OpenAI | PARTIAL | PARTIAL | Both SDKs pass mocked integration. Live Gemini succeeded on the hosted revision. Earlier live OpenAI requests received HTTP 429. |
| Pydantic response schema | PASS | PASS | Exactly five distinct queries, evidence enums, response serialization and legacy payload coverage. |
| Competitor analysis | PARTIAL | PASS | Mocked source checks and a separate real Wikidata lookup verified three official competitor sites. Full hosted response evidence identifies its revision. |
| Search query generation | PASS | PASS | Five distinct queries in both live responses and schema tests for supported platforms. |
| SEO improvements | PASS | PASS | Requested fields and reasons present. Current title and description come from extraction. |
| Top services | PARTIAL | PASS | Verified entries require affirmative first-party wording. Duplicate services are removed, and unsupported entries become inferred. |
| Target audience | PARTIAL | PASS | Audience fields and labels validated. Geographic focus now requires structured location data or an explicit location phrase. |
| Automated tests and lint | PARTIAL | PASS | Added competitor, redirect, proxy and operational metric assertions. Requested pytest and Ruff commands passed. |
| Docker and Render configuration | PASS | PASS | Docker startup command honors `PORT`. Render reports a successful Docker deployment for the hosted revision. Local Docker CLI was unavailable. |
| LLM comparison and pricing | PARTIAL | PASS | Verified official rates and model specifications. Application and vendor measurements are identified individually. |
| README and evidence | PARTIAL | PASS | Updated extraction, security, competitor labels, metrics, environment configuration and deployment notes. |
| Deployment readiness | PASS | PASS | Docker and Render configuration retain the public service and environment-provided port. Use the deployment record to identify the revision tested. |

## Evidence files

| File | Evidence |
| --- | --- |
| [pytest.txt](pytest.txt) | Executed offline suite. Website and provider transports are mocked where applicable. The local filesystem path is shortened. |
| [lint.txt](lint.txt) | Ruff lint, format, and dependency checks. |
| [startup.txt](startup.txt) | Local Uvicorn startup and HTTP 200 on `/health` using a custom port. |
| [swagger.png](swagger.png) | Browser screenshot of the public Render Swagger UI. |
| [deployment.json](deployment.json) | GitHub revision, Render deployment status and public route statuses at capture. |
| [live-business.json](live-business.json) | Actual Gemini-backed response for Basecamp. |
| [live-minimal.json](live-minimal.json) | Actual Gemini-backed response for Example Domain. |
| [live-metrics.json](live-metrics.json) | Client elapsed times and observed headers for the two hosted requests. |
| [local-extraction.json](local-extraction.json) | Real HTTPS fetch and HTML extraction timings from the updated local code. These runs made zero model calls. |
| [live-competitor-research.json](live-competitor-research.json) | Real Wikidata category discovery and first-party HTTPS checks. Starting from one candidate, the researcher found three verified competitors without a model call. |
| [live-errors.json](live-errors.json) | Hosted missing URL, malformed URL, private IP, localhost and missing page responses. |

The two observed round trips were 10.74 seconds for Basecamp and 4.56 seconds for Example Domain. Both returned all five sections and exactly five queries. These are individual observations. The updated local fetch and extraction stages measured 2,079.31 ms and 271.99 ms for Basecamp and 87.73 ms and 1.76 ms for Example Domain. Estimated cost logging was exercised in the mocked integration test. The hosted revision captured here predates those additions.

## Changes

- `app/metrics.py`, `app/main.py`, `app/config.py`, and `app/services/llm_service.py` record request status, stage timings, token counts, and an optional configured standard-rate estimate.
- `app/schemas.py`, `app/services/analysis_service.py`, `app/services/competitor_service.py`, and `app/services/website_extractor.py` cover source-backed comparisons, service and geography checks, and fetch instrumentation.
- Tests cover competitor evidence, redirect blocking, proxy handling, stage timings, and response compatibility. Lint fixes use the standard regex flag names and the appropriate exception type when an upstream envelope has the wrong type.
- The README, LLM comparison, sample response, example environment file, and evidence directory document the implementation and verification. Docker excludes assessment artifacts from its build context.

## Security findings

The fetcher permits public HTTP and HTTPS on their default ports. Every DNS answer is checked before selecting an IP. Redirects repeat URL and address checks, and connections use the pinned numeric IP with the original hostname for HTTP and TLS. The fetcher disables environment proxies, caps uncompressed HTML at 1 MiB, and enforces a website deadline. The research path uses the same guarded fetcher. Follow-up regression tests exercise application quotas, query-value redaction and the negative-evidence cases. Quotas are per process and reset on restart. The audit did not exercise host-level egress controls.

## Estimated assessment score

| Criteria | Estimate | Maximum |
| --- | ---: | ---: |
| FastAPI implementation | 10 | 10 |
| Website extraction | 14 | 15 |
| LLM integration | 14 | 15 |
| Competitor and search queries | 13 | 15 |
| SEO recommendations | 9 | 10 |
| Services and audience | 9 | 10 |
| LLM model research | 14 | 15 |
| Error handling and documentation | 10 | 10 |
| **Total** | **93** | **100** |

This is an engineering estimate. Deductions reflect static HTML coverage, unmeasured website-analysis accuracy, and outstanding live OpenAI and optional Brave checks.
