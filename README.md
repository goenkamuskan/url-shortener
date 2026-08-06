# URL Shortener with Caching, Rate Limiting & Analytics

A backend service that shortens URLs, tracks click analytics, and demonstrates
core system design patterns: caching, rate limiting, and background task
processing — built to understand *why* these patterns exist, not just to
check a box.

## Features

- **URL shortening** using base62-encoded auto-increment IDs (collision-free
  by construction — no retry logic needed)
- **Redis caching** (cache-aside pattern) on the redirect hot path
- **Token bucket rate limiting** on the shorten endpoint, implemented from
  scratch using Redis
- **Click analytics** — total clicks, clicks-by-day, top referrers
- **Async click logging** via FastAPI background tasks, keeping the redirect
  response off the write path

## Tech Stack

- **FastAPI** (Python) — web framework
- **PostgreSQL** — persistent storage (`urls`, `clicks` tables, indexed on
  hot-path columns)
- **Redis** — caching + rate limit state
- **Docker Compose** — local Postgres + Redis
- **SQLAlchemy** — ORM

## Architecture Decisions

**Base62 encoding over random strings.** Short codes are derived from each
row's auto-incrementing Postgres ID, encoded in base62 (`0-9a-zA-Z`). This
guarantees uniqueness for free — no collision checks, no retries — and keeps
codes short.

**Cache-aside, not write-through, for URL lookups.** On `POST /shorten`, the
new mapping is written to Redis immediately (write-through) so the very
first redirect is already a cache hit. On `GET /{code}`, a cache miss falls
back to Postgres and repopulates Redis with a 1-hour TTL.

**Click logging is asynchronous.** The redirect response returns immediately;
the actual `INSERT` into the `clicks` table happens via a FastAPI
`BackgroundTask` after the response is sent. This keeps analytics durable
without making every redirect wait on a database write.

**Rate limiting via token bucket, not a fixed window.** Implemented directly
against Redis: each client IP gets a bucket of 5 tokens that refill at 1
token/second. This avoids the burst-at-window-boundary problem that fixed
windows have, and only needs two Redis calls per request.

## Performance Investigation

Initial load testing (`autocannon`, 50 concurrent connections, 10s) showed
~215ms average latency on cached redirects — surprisingly high for a
Redis-backed lookup. Investigation ruled out two hypotheses before finding
the real cause:

1. **Hypothesis: synchronous click-logging on the request path.** Moved
   logging to a background task — latency was unchanged. Ruled out.
2. **Hypothesis: a stray DB call remained on the cache-hit path** (fetching
   `url_id` for the click record). Fixed by storing `url_id` alongside the
   URL in the Redis cache itself, so cache hits need zero DB calls — latency
   was still unchanged. Ruled out.
3. **Actual cause: `uvicorn` running as a single worker process.** FastAPI's
   sync route handlers run on a limited thread pool within one process; at
   50 concurrent connections, requests were queuing for a free thread.

**Result after switching to 4 worker processes:**

| Metric | 1 worker | 4 workers | Change |
|---|---|---|---|
| Median latency | 226 ms | 76 ms | **-65%** |
| Avg latency | ~215 ms | 115 ms | **-47%** |
| Throughput | ~235 req/s | 430 req/s | **+85%** |

The lesson: caching and async writes were both correct changes, but they
weren't the bottleneck — server concurrency configuration was. Load testing
each change individually is what surfaced this instead of assuming the fix
worked.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/shorten` | Create a short URL (rate-limited: 5 req/burst, 1/sec refill) |
| GET | `/{code}` | Redirect to the original URL, logs a click |
| GET | `/analytics/{code}` | Total clicks, clicks-by-day, top referrers |

### Example

```bash
curl -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"long_url": "https://example.com"}'
# → {"short_code":"3","short_url":"http://localhost:8000/3","long_url":"https://example.com/"}

curl -I http://localhost:8000/3
# → 307 Temporary Redirect, location: https://example.com/

curl http://localhost:8000/analytics/3
# → {"total_clicks":1,"clicks_by_day":[...],"top_referrers":[...]}
```

## Running Locally

```bash
# 1. Clone and enter the project
git clone https://github.com/goenkamuskan/url-shortener.git
cd url-shortener

# 2. Set up Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Start Postgres + Redis
docker compose up -d

# 4. Configure environment
cp .env.example .env

# 5. Create database tables
python -m app.init_db

# 6. Run the server (multi-worker, for realistic performance)
uvicorn app.main:app --workers 4
```

Server runs at `http://localhost:8000`.

## Load Testing

```bash
npm install -g autocannon
autocannon -c 50 -d 10 http://localhost:8000/<short_code>
```

## Possible Next Steps

- Custom short-code aliases
- URL expiration enforcement (`expires_at` is already in the schema)
- Per-user accounts and link ownership
- Deploy to Railway/Render with managed Postgres + Redis