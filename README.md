# Social Profiles Manager v2

A scalable FastAPI + PostgreSQL + Redis backend for managing social media profiles.

---

## What changedd from v1

### database.py
- Switched from `create_engine` (sync) to `create_async_engine` (async) using `asyncpg`
- Added `pool_timeout` and `pool_recycle` to prevent stale connections
- Removed hardcoded fallback credentials — `DATABASE_URL` is now required
- `get_db` is now a proper `async` generator

### models.py
- Added **B-tree indexes** on every filter column (`zone`, `party_district`, `constituency`, `designation`) and every sort column (follower counts)
- Added a **composite index** on `(zone, designation)` for the most common combined filter
- Added a **GIN index** for PostgreSQL full-text search (created in Alembic migration, not in Python)
- Converted `*_active_status` and `*_verified_status` columns from `String` to `Boolean` — smaller, faster, type-safe
- Converted `dob` from `String` to `Date` — enables date arithmetic in SQL

### schemas.py
- `ProfileCreate` now **requires** `name` and at least one social media ID
- `ProfileUpdate` validates that at least one field is provided
- Follower counts validated `>= 0`
- `email_id` uses `EmailStr` for format validation
- All string fields have `max_length` matching the DB column
- Added `ProfileListResponse` with `next_cursor` for keyset pagination

### main.py
- **All DB calls are now `async`** — FastAPI's event loop is no longer blocked
- **Keyset (cursor) pagination** replaces `OFFSET/LIMIT` — performance stays constant regardless of page depth
- **Stats consolidated** into a single SQL query using conditional aggregation (`CASE WHEN`) — was 8+ round-trips, now 1
- **`top-profiles`** sorted in SQL with `ORDER BY ... LIMIT 15` — was loading all rows into Python then sorting
- **Active/verified status** comparisons use `== True` boolean checks instead of `ilike("active")` string scans
- **Redis caching** on all stats, analytics, and filter-options endpoints — expensive aggregations are cached for 5-10 minutes
- Cache is **automatically invalidated** on any write (create/update/delete/bulk-delete)
- Analytics endpoints use **single-query conditional aggregation** instead of multiple filtered count queries

### New files
- `cache.py` — async Redis helper with a `@cache_response` decorator
- `alembic.ini` + `migrations/` — Alembic setup for safe schema migrations
- `.env.example` — environment variable template

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your PostgreSQL and Redis connection strings
```

### 3. Run migrations
```bash
alembic upgrade head
```
This creates all tables, converts status columns to boolean, converts dob to Date,
and creates all B-tree + GIN indexes.

### 4. Run the server

**Development:**
```bash
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

**Production** (Gunicorn with async Uvicorn workers):
```bash
gunicorn main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:5000 \
  --timeout 60
```
> **Worker count rule of thumb:** `(2 × CPU cores) + 1`

---

## API changes

### Pagination
The listing endpoint now uses **keyset pagination** instead of `offset/start`.

| v1 | v2 |
|----|----|
| `GET /api/profiles?start=200&limit=50` | `GET /api/profiles?cursor=200&limit=50` |

- `cursor` is the `id` of the last row from the previous page (0 or omitted for the first page)
- The response includes `next_cursor` — pass it as `cursor` in the next request
- `total` is still returned for displaying total counts

### Status fields
Status columns changed from strings to booleans:

| v1 | v2 |
|----|----|
| `"facebook_active_status": "active"` | `"facebook_active_status": true` |
| `"facebook_active_status": "inactive"` | `"facebook_active_status": false` |
| `"facebook_active_status": null` | `"facebook_active_status": null` |

---

## Infrastructure recommendations

| Component | Recommendation |
|-----------|---------------|
| PostgreSQL | Enable `pg_stat_statements` to identify slow queries |
| Redis | Use Redis Sentinel or Cluster for HA in production |
| Gunicorn | 4 workers minimum; scale horizontally behind a load balancer |
| Connection pool | Ensure `pool_size × workers ≤ PostgreSQL max_connections` |
