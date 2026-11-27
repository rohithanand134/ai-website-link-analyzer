# AI-Powered Website Link Analysis API

Submit a public website URL to get a brand and competitor overview, five target search queries, SEO recommendations, up to five services with evidence, and target audience segments.

## Architecture

```text
POST /link-analysis
  -> URL checks and DNS validation
  -> IP-pinned HTML fetch and optional same-origin crawl
  -> BeautifulSoup extraction and bounded evidence JSON
  -> One Gemini or OpenAI API call with structured JSON
  -> Pydantic validation, optional competitor research, and evidence checks
  -> Structured JSON response
```

Python 3.11+, FastAPI, Uvicorn, Pydantic v2, Pydantic Settings, HTTPX, BeautifulSoup, the official Google Gen AI and OpenAI SDKs, pytest, pytest-asyncio, and Ruff.

```text
app/
  main.py                      FastAPI routes, lifespan, and error handling
  metrics.py                   Request-scoped stage timings
  limits.py                    Request budgets and in-flight cap
  config.py                    Environment settings
  schemas.py                   Request, response, and provider schemas
  services/
    website_extractor.py       Fetching and extraction
    llm_service.py             Gemini and OpenAI integration
    analysis_service.py         Evidence and pipeline orchestration
    competitor_service.py      Competitor suggestions and optional Brave research
  utils/validators.py          URL and IP security
tests/                         Offline tests and end-to-end mocked calls
examples/                      Request and response JSON
docs/LLM_COMPARISON.md          Provider research, cost and latency estimates
evidence/                      Audit report, test logs, and live demonstration
Dockerfile, render.yaml        Container and Render deployment
```

## Local setup

From this repository's root, create and activate a virtual environment.

```bash
python -m venv .venv
```

Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
```

Windows Command Prompt users can activate with `.venv\Scripts\activate`. On macOS or Linux, run `source .venv/bin/activate` and `cp .env.example .env`.

Set `GEMINI_API_KEY` or `OPENAI_API_KEY` in `.env`, then run the API. Gemini takes precedence when both keys are configured. Each analysis calls the selected provider once.

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 4000 --reload
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | Unset | Enables the Gemini Developer API. |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Gemini model with structured output support. |
| `OPENAI_API_KEY` | Unset | Enables the OpenAI Responses API when the Gemini key is unset. |
| `OPENAI_MODEL` | `gpt-6-luna` | OpenAI Responses model with strict schema and `reasoning.effort=none` support. |
| `COMPETITOR_RESEARCH` | `wikidata` | Discovers official candidate sites without credentials. Set to `off` to disable keyless research. |
| `BRAVE_SEARCH_API_KEY` | Unset | Uses optional Brave discovery instead of Wikidata when configured. |
| `ANALYSIS_PER_DAY` / `ANALYSIS_PER_MINUTE` | `120` / `12` | Global rolling request budgets per process. |
| `ANALYSIS_PER_CALLER_PER_MINUTE` / `ANALYSIS_MAX_IN_FLIGHT` | `5` / `3` | Caller request and process concurrency limits. |
| `LLM_PRICE_MODEL` | Unset | Exact model ID covered by the optional estimate. |
| `LLM_INPUT_USD_PER_MILLION` / `LLM_OUTPUT_USD_PER_MILLION` | Unset | Verified input and output rates used to log a token-cost estimate. |
| `LLM_TIMEOUT_SECONDS` | `60` | Total model call deadline, greater than 0 and at most 120 seconds. |
| `HTTP_TIMEOUT_SECONDS` | `15` | Website-stage deadline, greater than 0 and at most 30 seconds. |
| `MAX_WEBSITE_CHARS` | `16000` | Serialized website evidence budget, from 4096 to 32000 characters. |
| `MAX_CRAWL_PAGES` | `4` | Landing page plus up to three relevant same-origin pages. Set 1 for landing page only. |
| `LOG_LEVEL` | `INFO` | Application logging threshold. |

## API

Swagger UI is available at [localhost:4000/docs](http://localhost:4000/docs). `GET /health` returns `{"status":"ok"}` when the process is running. The root path also returns that status.

```bash
curl -fsS http://localhost:4000/link-analysis \
  -H 'Content-Type: application/json' \
  -d @examples/sample_request.json
```

PowerShell

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:4000/link-analysis `
  -ContentType 'application/json' -InFile examples/sample_request.json
```

The [sample response](examples/sample_response.json) uses a fictional IT company to illustrate the shape. It is a test fixture. Live results come from the pages fetched for the request and the provider response.

The response includes the final landing page URL, pages supplied as evidence, and extraction limitations. Citation URLs omit query strings and fragments. Brand category is in `brand.category`. `competitor_analysis.competitive_advantages` contains potential differentiators supported by the subject site. A `verified` service requires an affirmative first-party statement naming the offering. Geographic focus requires a structured location field or an explicit location phrase. Marketing segments are marked `inferred`.

### Competitor evidence

The app researches model-suggested competitors and can fill missing candidates with a bounded Wikidata category search. Wikidata uses public official-website statements to find candidate URLs. Brave can supply search excerpts when configured. Both paths validate and fetch source pages with the same URL and IP checks as the subject fetch. A shared offering is `verified` when present in both sites' sampled material. An overlap supported by the discovery source's description or excerpt is labeled `inferred`. The API returns up to five candidates, cites its sources, and describes differences within the pages inspected. It leaves unsupported gaps blank. [Wikidata Data Access](https://www.wikidata.org/wiki/Help:Data_access) · [Brave Web Search API](https://api-dashboard.search.brave.com/api-reference/web/search/get)

With search disabled or insufficient evidence, suggestions are `unverified` and retain their names with empty evidence fields. Search is optional and can incur provider charges when enabled. [Brave Web Search API](https://api-dashboard.search.brave.com/api-reference/web/search/get)

### Errors

The API returns a stable error code and a sanitized message with guidance.

| Input | Status and code | How to fix the request |
| --- | --- | --- |
| Missing, blank, or non-string URL | `422 invalid_request` | Send a non-empty string in the JSON `url` field. |
| Malformed URL | `400 invalid_url` | Include the full `http://` or `https://` URL, such as `https://example.com`. |
| Localhost or a private destination | `400 blocked_destination` | Use a publicly reachable website URL. |

```json
{
  "success": false,
  "error": {
    "code": "blocked_destination",
    "message": "Website destination must be public. Use the site's public URL instead of localhost or a private address."
  }
}
```

| HTTP | Meaning |
| --- | --- |
| 400 | Malformed URL, unsupported port, or blocked destination |
| 403 / 404 | Source website denied the request or the page was missing |
| 413 | HTML response exceeded 1 MiB |
| 415 | Unsupported content type or content encoding |
| 422 | Invalid request body or unusable static HTML |
| 429 | Application request or concurrency limit reached. Check `Retry-After`. |
| 500 | Unexpected application error |
| 502 | Website DNS, redirect, or gateway failure, or invalid/provider-failed AI response |
| 503 | Provider configuration, authorization, or rate limit |
| 504 | Website or provider timeout |

## Security and cost

- Accept only public HTTP/HTTPS destinations on the default ports. Reject URL credentials, internal names, and private, reserved, loopback, link-local, or mixed public/private DNS results.
- Dial the validated numeric IP with the original HTTP Host and HTTPS certificate hostname. Disable environment proxies and redirect automation. Revalidate each redirect, create a fresh pool per hop, and follow at most three redirects per fetch.
- Use a shared website deadline, a connect timeout of up to five seconds, strict content-type and encoding checks, and a streamed 1 MiB limit per response. Linked pages may fail independently.
- Strip common boilerplate and duplicate text and preserve relevant SEO data, visible content, and selected JSON-LD fields. Only bounded evidence JSON goes to one model call. The model can produce up to 4,500 output tokens.
- Pass untrusted page data in the user message, keep system instructions separate, and give the model zero tools. Schema validation and server-side source checks constrain the returned data. Free-form analysis still depends on the model.
- Keep query strings for the website fetch and strip them from evidence URLs before model calls and from returned citations. Submit public URLs without credentials or other sensitive data.
- Store credentials in environment variables. Request logs record status and stage durations, and provider logs record token counts when available. The Responses call sets `store=False`.

The `Server-Timing` header reports `fetch`, `extract`, `llm`, and `total` in milliseconds when those stages run. Optional `competitors` timing includes its own fetch and extraction, which also contribute to those stage totals. A model-matched, configured rate logs an estimated cost at standard uncached rates when the provider reports both token counts. Compare the estimate with provider billing for actual cost.

The hosted service uses Gemini 3.1 Flash-Lite. At standard paid rates its estimated cost is **$0.00425** for 5,000 input and 2,000 output tokens. The same workload costs approximately **$0.00150** on GPT-6 Luna. See the [model comparison](docs/LLM_COMPARISON.md) and [live performance evidence](evidence/live-metrics.json).

## Tests

```bash
python -m pytest -v
python -m ruff check --select E,F,I app tests
python -m ruff format --check app tests
```

Tests mock DNS, website requests, and both provider transports. They cover IP pinning, redirect revalidation, extraction, size and time limits, provider errors, malformed and refused output, schema checks, evidence labels, and prompt role separation. End-to-end tests exercise the HTTP endpoint and both official SDKs using test credentials.

The [assessment report and evidence](evidence/README.md) link to the offline test transcripts, local startup log, Swagger capture, and separate live Gemini requests.

## Deploy to Render

1. Publish this directory as the root of a public GitHub repository and link that repository in **Render Dashboard → New → Blueprint**.
2. Review the Docker web service and plan defined in `render.yaml`. For OpenAI, enter `OPENAI_API_KEY` when prompted. For Gemini, enter `your_api_key_here` at the OpenAI prompt, deploy, then add `GEMINI_API_KEY` in the service's **Environment** tab and choose **Save, rebuild, and deploy**.
3. Copy the service hostname from Render. The container binds to `0.0.0.0` and uses the host-provided `PORT`, with 4000 as the local default.

The expected routes are `https://your-app.onrender.com/`, `/health`, `/docs`, and `POST /link-analysis`. Use your assigned hostname in these smoke commands.

```bash
curl -fsS https://your-app.onrender.com/health
curl -fsS https://your-app.onrender.com/docs
curl -fsS https://your-app.onrender.com/link-analysis \
  -H 'Content-Type: application/json' \
  -d @examples/sample_request.json
```

Optional local Docker smoke test. The container receives credentials from your local `.env`.

```bash
docker build -t ai-link-analysis .
docker run --rm --env-file .env -e PORT=4000 -p 4000:4000 ai-link-analysis
```

[Render Blueprints](https://render.com/docs/blueprint-spec) · [Render web services](https://render.com/docs/web-services)

### GitHub submission

Create an empty public repository on GitHub. From the project directory, run the following and check the staged file list before committing. Use your own repository URL.

```bash
git init -b main
git add .
git diff --cached --name-only
git commit -m "Build website link analysis API"
git remote add origin https://github.com/YOUR_USERNAME/ai-link-analysis.git
git push -u origin main
```

Submit the GitHub repository URL and the Render service URL after verifying the hosted health, Swagger, and analysis routes.

## Known limitations and future improvements

- Static HTML extraction has limited visibility into client-rendered websites and gated or compressed-only pages. Internal discovery favors common English link labels and paths.
- Competitor research checks a bounded set of search results and may yield fewer than three evidenced candidates. Search volumes, ad costs, and market share require separate data. Accuracy across a representative website corpus is unmeasured.
- The public assessment route applies rolling and concurrency limits in memory. Counters reset on process restart and are independent across replicas. A multi-instance deployment requires shared gateway limits or provider budget controls.
- A bounded cache keyed by normalized site content and model version could reduce repeat costs. A browser-based extractor could support JavaScript-heavy sites with its own outbound network controls.
