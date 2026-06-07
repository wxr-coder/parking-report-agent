# Parking Report Agent

FastAPI service for the Knowledge Stack parking-report-agent take-home assignment. It accepts a Word report template and parking transaction CSV, creates a durable SQLite job, generates the report asynchronously, exposes job status, and returns a completed `.docx`.

## Run

```bash
cp .env.example .env  # optional; only needed for real LLM calls
docker compose up --build
```

Open http://localhost:8000 or use the API directly:

```bash
curl -F "template_file=@sample/停车明细分析报告_模板.docx" \
  -F "data_file=@sample/data.csv" \
  -F "instructions=请生成管理者可读的经营分析" \
  http://localhost:8000/jobs
```

Then poll `GET /jobs/{job_id}` and download from `GET /jobs/{job_id}/download` once the status is `completed`.

Local development:

```bash
uv venv
uv pip install -e ".[dev]"
. .venv/bin/activate
uvicorn app.main:app --reload
pytest
```

## Configuration

The app loads `.env` via typed settings. `.env` is ignored by git.

```env
OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
OPENAI_API_KEY=your-key
OPENAI_MODEL=deepseek-v4-flash
SUBMIT_API_KEY=optional-submit-key
STORAGE_DIR=storage
DATABASE_PATH=storage/jobs.sqlite3
WORKER_COUNT=1
MAX_PENDING_JOBS=8
JOB_MAX_ATTEMPTS=3
JOB_RETRY_BASE_SECONDS=5
JOB_POLL_INTERVAL_SECONDS=1.0
JOB_LOCK_TIMEOUT_SECONDS=300
MAX_UPLOAD_BYTES=10485760
LOG_LEVEL=INFO
```

If `OPENAI_API_KEY` is missing, generation still succeeds with deterministic fallback narrative text. The LLM is never used to compute hard numbers.

If `SUBMIT_API_KEY` is set, `POST /jobs` requires the same value in the `X-API-Key` header.

## Design Overview

The lifecycle is intentionally small and durable:

- `POST /jobs` saves uploads into `storage/jobs/{job_id}/`, inserts a queued SQLite row, wakes the worker pool, and immediately returns `{job_id, status}`.
- Workers atomically claim queued rows from SQLite, mark them `running`, persist stage transitions, and retry failed jobs with exponential backoff until `JOB_MAX_ATTEMPTS`.
- `GET /jobs/{job_id}` reads persisted state: `queued`, `running`, `completed`, or `failed`, plus the current stage and retry counters.
- `GET /jobs/{job_id}/download` returns the generated Word file only after completion.

Report generation is split into clear layers:

- `app/services/metrics.py` parses the CSV and deterministically computes the six required hard metrics, payment/channel distributions, zero-paid count, and parking duration buckets.
- `app/services/charts.py` renders a real matplotlib payment-method chart as PNG.
- `app/services/agent.py` sends only grounded JSON facts to an OpenAI-compatible chat completion endpoint and validates structured JSON output. It falls back safely offline.
- `app/services/report.py` fills the supplied `.docx` template with deterministic metrics, narrative sections, and the chart image.

JSON logs are emitted for `job_submitted`, `job_started`, `job_completed`, `job_failed`, `llm_call`, and `llm_call_skipped`/`llm_call_failed`. Logs avoid upload contents, user instructions, full LLM prompts, and model responses; use `job_id` to correlate failures with controlled server logs.

## Sample Report

`sample/sample_report.docx` is generated from:

- `sample/data.csv`
- `sample/停车明细分析报告_模板.docx`

Regenerate it with:

```bash
python -m scripts.generate_sample
```

## Tests

```bash
pytest
```

Coverage includes:

- API flow test for submit -> status -> download with generation mocked.
- Metrics tests for the six required numbers, including the provided fixture baseline.
