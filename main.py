import csv
import io
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, delete, or_, asc, desc, case
from sqlalchemy.ext.asyncio import AsyncSession

from database import Base, engine, get_db
from models import SocialProfile
from schemas import (
    ProfileCreate, ProfileUpdate, ProfileResponse,
    ProfileListResponse, BulkDeleteRequest,
)
from cache import cache_get, cache_set, invalidate_prefix, close_redis

# ── Bootstrap ──────────────────────────────────────────────────────────────────
app = FastAPI(title="Social Profiles Manager", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.on_event("shutdown")
async def shutdown():
    await close_redis()
    await engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────────────
SORTABLE = {
    "id":                  SocialProfile.id,
    "name":                SocialProfile.name,
    "zone":                SocialProfile.zone,
    "party_district":      SocialProfile.party_district,
    "constituency":        SocialProfile.constituency,
    "designation":         SocialProfile.designation,
    "facebook_followers":  SocialProfile.facebook_followers,
    "twitter_followers":   SocialProfile.twitter_followers,
    "instagram_followers": SocialProfile.instagram_followers,
}

# TTL constants (seconds)
STATS_TTL     = 300   # 5 minutes  — aggregate stats
ANALYTICS_TTL = 300   # 5 minutes  — chart data
OPTIONS_TTL   = 600   # 10 minutes — filter dropdowns (change less often)


def _build_search_filter(search: str):
    """
    Case-insensitive partial match across name, constituency,
    designation, zone, email and all social media IDs.
    Works without any special index — fast enough for local use.
    """
    term = f"%{search}%"
    return or_(
        SocialProfile.name.ilike(term),
        SocialProfile.constituency.ilike(term),
        SocialProfile.designation.ilike(term),
        SocialProfile.zone.ilike(term),
        SocialProfile.email_id.ilike(term),
        SocialProfile.facebook_id.ilike(term),
        SocialProfile.twitter_id.ilike(term),
        SocialProfile.instagram_id.ilike(term),
    )


def _apply_filters(stmt, search, zone, party_district, constituency,
                   designation, active_only, verified_only):
    if search:
        stmt = stmt.where(_build_search_filter(search))
    if zone:
        stmt = stmt.where(SocialProfile.zone == zone)
    if party_district:
        stmt = stmt.where(SocialProfile.party_district == party_district)
    if constituency:
        stmt = stmt.where(SocialProfile.constituency == constituency)
    if designation:
        stmt = stmt.where(SocialProfile.designation == designation)
    if active_only:
        stmt = stmt.where(or_(
            SocialProfile.facebook_active_status  == True,   # noqa: E712
            SocialProfile.twitter_active_status   == True,
            SocialProfile.instagram_active_status == True,
        ))
    if verified_only:
        stmt = stmt.where(or_(
            SocialProfile.facebook_verified_status  == True,  # noqa: E712
            SocialProfile.twitter_verified_status   == True,
            SocialProfile.instagram_verified_status == True,
        ))
    return stmt


# ── Pages ──────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/analytics")
async def analytics_page():
    return FileResponse("static/analytics.html")


# ── List / Keyset pagination ───────────────────────────────────────────────────
@app.get("/api/profiles", response_model=ProfileListResponse)
async def list_profiles(
    # Keyset cursor: pass the last `id` from the previous page.
    # Omit (or pass 0) for the first page.
    cursor:         int  = Query(0, ge=0,
                        description="Last ID from previous page; 0 for first page."),
    limit:          int  = Query(50, ge=1, le=200),
    search:         Optional[str] = None,
    zone:           Optional[str] = None,
    party_district: Optional[str] = None,
    constituency:   Optional[str] = None,
    designation:    Optional[str] = None,
    active_only:    bool = False,
    verified_only:  bool = False,
    sort_by:        str  = "id",
    sort_order:     str  = "asc",
    db: AsyncSession = Depends(get_db),
):
    col     = SORTABLE.get(sort_by, SocialProfile.id)
    ordered = desc(col) if sort_order == "desc" else asc(col)

    # ── Count (filtered, no pagination) ───────────────────────────────────────
    count_stmt = select(func.count()).select_from(SocialProfile)
    count_stmt = _apply_filters(count_stmt, search, zone, party_district,
                                constituency, designation, active_only, verified_only)
    total = (await db.execute(count_stmt)).scalar_one()

    # ── Data with keyset ───────────────────────────────────────────────────────
    stmt = select(SocialProfile)
    stmt = _apply_filters(stmt, search, zone, party_district, constituency,
                          designation, active_only, verified_only)

    # Keyset: only works cleanly when sorting by id.
    # For other sort columns we fall back to a simple WHERE id > cursor approach
    # since implementing full keyset for arbitrary columns requires composite keys.
    if cursor > 0:
        if sort_by == "id":
            stmt = stmt.where(
                SocialProfile.id > cursor if sort_order == "asc"
                else SocialProfile.id < cursor
            )
        else:
            # For non-id sorts, keyset pagination requires knowing the sort
            # column's value at the cursor position. We use a subquery for this.
            cursor_val_stmt = select(col).where(SocialProfile.id == cursor)
            cursor_val = (await db.execute(cursor_val_stmt)).scalar_one_or_none()
            if cursor_val is not None:
                stmt = stmt.where(
                    col > cursor_val if sort_order == "asc" else col < cursor_val
                )

    stmt  = stmt.order_by(ordered, asc(SocialProfile.id)).limit(limit + 1)
    rows  = (await db.execute(stmt)).scalars().all()

    # If we got limit+1 rows there is another page; set next_cursor to last row's id.
    has_more    = len(rows) > limit
    rows        = rows[:limit]
    next_cursor = rows[-1].id if has_more and rows else None

    return ProfileListResponse(
        rows=[ProfileResponse.model_validate(r) for r in rows],
        total=total,
        next_cursor=next_cursor,
    )


# ── Single record ──────────────────────────────────────────────────────────────
@app.get("/api/profiles/{profile_id}", response_model=ProfileResponse)
async def get_profile(profile_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.get(SocialProfile, profile_id)
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")
    return result


# ── Create ─────────────────────────────────────────────────────────────────────
@app.post("/api/profiles", response_model=ProfileResponse, status_code=201)
async def create_profile(body: ProfileCreate, db: AsyncSession = Depends(get_db)):
    p = SocialProfile(**body.model_dump())
    db.add(p)
    await db.commit()
    await db.refresh(p)
    # Bust caches that aggregate profile data
    await invalidate_prefix("stats")
    await invalidate_prefix("analytics")
    await invalidate_prefix("options")
    return p


# ── Update ─────────────────────────────────────────────────────────────────────
@app.put("/api/profiles/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: int,
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(SocialProfile, profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    await db.commit()
    await db.refresh(p)
    await invalidate_prefix("stats")
    await invalidate_prefix("analytics")
    return p


# ── Delete single ──────────────────────────────────────────────────────────────
@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: int, db: AsyncSession = Depends(get_db)):
    p = await db.get(SocialProfile, profile_id)
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    await db.delete(p)
    await db.commit()
    await invalidate_prefix("stats")
    await invalidate_prefix("analytics")
    return {"message": "Deleted"}


# ── Bulk delete ────────────────────────────────────────────────────────────────
@app.post("/api/profiles/bulk-delete")
async def bulk_delete(body: BulkDeleteRequest, db: AsyncSession = Depends(get_db)):
    stmt = (
        delete(SocialProfile)
        .where(SocialProfile.id.in_(body.ids))
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    await db.commit()
    await invalidate_prefix("stats")
    await invalidate_prefix("analytics")
    return {"deleted": result.rowcount}


# ── Stats — single consolidated query ─────────────────────────────────────────
@app.get("/api/stats")
async def stats(db: AsyncSession = Depends(get_db)):
    """
    All aggregate stats in ONE round-trip using conditional aggregation.
    Previously this made 8+ separate queries.
    """
    cached = await cache_get("stats:all")
    if cached:
        return cached

    stmt = select(
        func.count(SocialProfile.id).label("total"),

        # Facebook
        func.sum(case((SocialProfile.facebook_active_status   == True, 1), else_=0)).label("fb_active"),   # noqa: E712
        func.sum(case((SocialProfile.facebook_verified_status == True, 1), else_=0)).label("fb_verified"),
        func.sum(func.coalesce(SocialProfile.facebook_followers,  0)).label("fb_followers"),

        # Twitter
        func.sum(case((SocialProfile.twitter_active_status   == True, 1), else_=0)).label("tw_active"),    # noqa: E712
        func.sum(case((SocialProfile.twitter_verified_status == True, 1), else_=0)).label("tw_verified"),
        func.sum(func.coalesce(SocialProfile.twitter_followers,   0)).label("tw_followers"),

        # Instagram
        func.sum(case((SocialProfile.instagram_active_status   == True, 1), else_=0)).label("ig_active"),  # noqa: E712
        func.sum(case((SocialProfile.instagram_verified_status == True, 1), else_=0)).label("ig_verified"),
        func.sum(func.coalesce(SocialProfile.instagram_followers, 0)).label("ig_followers"),
    )
    row = (await db.execute(stmt)).one()

    # ── By designation (top 12) ────────────────────────────────────────────────
    desig_stmt = (
        select(SocialProfile.designation, func.count(SocialProfile.id).label("c"))
        .group_by(SocialProfile.designation)
        .order_by(desc("c"))
        .limit(12)
    )
    desig_rows = (await db.execute(desig_stmt)).all()

    # ── By zone ────────────────────────────────────────────────────────────────
    zone_stmt = (
        select(SocialProfile.zone, func.count(SocialProfile.id).label("c"))
        .group_by(SocialProfile.zone)
        .order_by(desc("c"))
    )
    zone_rows = (await db.execute(zone_stmt)).all()

    result = {
        "total": row.total,
        "facebook": {
            "active":    int(row.fb_active   or 0),
            "verified":  int(row.fb_verified or 0),
            "followers": int(row.fb_followers or 0),
        },
        "twitter": {
            "active":    int(row.tw_active   or 0),
            "verified":  int(row.tw_verified or 0),
            "followers": int(row.tw_followers or 0),
        },
        "instagram": {
            "active":    int(row.ig_active   or 0),
            "verified":  int(row.ig_verified or 0),
            "followers": int(row.ig_followers or 0),
        },
        "by_designation": [{"label": d or "Unknown", "count": c} for d, c in desig_rows],
        "by_zone":        [{"label": z or "Unknown", "count": c} for z, c in zone_rows],
    }
    await cache_set("stats:all", result, ttl=STATS_TTL)
    return result


# ── Platform comparison ────────────────────────────────────────────────────────
@app.get("/api/analytics/platform-comparison")
async def platform_comparison(
    zone:           Optional[str] = None,
    party_district: Optional[str] = None,
    constituency:   Optional[str] = None,
    designation:    Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    cache_key  = f"analytics:platform:{zone}:{party_district}:{constituency}:{designation}"
    cached     = await cache_get(cache_key)
    if cached:
        return cached

    stmt = select(
        func.avg(SocialProfile.facebook_followers).label("fb_avg"),
        func.avg(SocialProfile.twitter_followers).label("tw_avg"),
        func.avg(SocialProfile.instagram_followers).label("ig_avg"),
    )
    stmt   = _apply_filters(stmt, None, zone, party_district, constituency, designation, False, False)
    row    = (await db.execute(stmt)).one()
    result = {
        "labels":   ["Facebook", "Twitter", "Instagram"],
        "datasets": [{
            "label":           "Avg Followers",
            "data":            [int(row.fb_avg or 0), int(row.tw_avg or 0), int(row.ig_avg or 0)],
            "backgroundColor": ["#1877F2", "#1DA1F2", "#E1306C"],
        }],
    }
    await cache_set(cache_key, result, ttl=ANALYTICS_TTL)
    return result


# ── Top profiles — pure SQL, no Python sorting ────────────────────────────────
@app.get("/api/analytics/top-profiles")
async def top_profiles(
    zone:           Optional[str] = None,
    party_district: Optional[str] = None,
    constituency:   Optional[str] = None,
    designation:    Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    cache_key = f"analytics:top:{zone}:{party_district}:{constituency}:{designation}"
    cached    = await cache_get(cache_key)
    if cached:
        return cached

    total_followers = (
        func.coalesce(SocialProfile.facebook_followers,  0) +
        func.coalesce(SocialProfile.twitter_followers,   0) +
        func.coalesce(SocialProfile.instagram_followers, 0)
    ).label("total")

    stmt = (
        select(SocialProfile.name, SocialProfile.zone, total_followers)
        .order_by(desc("total"))
        .limit(15)
    )
    stmt   = _apply_filters(stmt, None, zone, party_district, constituency, designation, False, False)
    rows   = (await db.execute(stmt)).all()
    labels = [
        (r[0][:20] + "…" if len(r[0]) > 20 else r[0]) if r[0] else "Unknown"
        for r in rows
    ]
    result = {
        "labels":   labels,
        "datasets": [{
            "label":           "Total Followers",
            "data":            [int(r[2]) for r in rows],
            "backgroundColor": "#36A2EB",
        }],
    }
    await cache_set(cache_key, result, ttl=ANALYTICS_TTL)
    return result


# ── Active status distribution ─────────────────────────────────────────────────
@app.get("/api/analytics/active-status")
async def active_status_dist(
    zone:           Optional[str] = None,
    party_district: Optional[str] = None,
    constituency:   Optional[str] = None,
    designation:    Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    cache_key = f"analytics:active:{zone}:{party_district}:{constituency}:{designation}"
    cached    = await cache_get(cache_key)
    if cached:
        return cached

    stmt = select(
        func.sum(case((SocialProfile.facebook_active_status  == True, 1), else_=0)).label("fb"),  # noqa: E712
        func.sum(case((SocialProfile.twitter_active_status   == True, 1), else_=0)).label("tw"),
        func.sum(case((SocialProfile.instagram_active_status == True, 1), else_=0)).label("ig"),
    )
    stmt   = _apply_filters(stmt, None, zone, party_district, constituency, designation, False, False)
    row    = (await db.execute(stmt)).one()
    result = {
        "labels":   ["Facebook", "Twitter", "Instagram"],
        "datasets": [{
            "label":           "Active Profiles",
            "data":            [int(row.fb or 0), int(row.tw or 0), int(row.ig or 0)],
            "backgroundColor": ["#1877F2", "#1DA1F2", "#E1306C"],
        }],
    }
    await cache_set(cache_key, result, ttl=ANALYTICS_TTL)
    return result


# ── Verified status distribution ───────────────────────────────────────────────
@app.get("/api/analytics/verified-status")
async def verified_status_dist(
    zone:           Optional[str] = None,
    party_district: Optional[str] = None,
    constituency:   Optional[str] = None,
    designation:    Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    cache_key = f"analytics:verified:{zone}:{party_district}:{constituency}:{designation}"
    cached    = await cache_get(cache_key)
    if cached:
        return cached

    stmt = select(
        func.sum(case((SocialProfile.facebook_verified_status  == True, 1), else_=0)).label("fb"),  # noqa: E712
        func.sum(case((SocialProfile.twitter_verified_status   == True, 1), else_=0)).label("tw"),
        func.sum(case((SocialProfile.instagram_verified_status == True, 1), else_=0)).label("ig"),
    )
    stmt   = _apply_filters(stmt, None, zone, party_district, constituency, designation, False, False)
    row    = (await db.execute(stmt)).one()
    result = {
        "labels":   ["Facebook", "Twitter", "Instagram"],
        "datasets": [{
            "label":           "Verified Profiles",
            "data":            [int(row.fb or 0), int(row.tw or 0), int(row.ig or 0)],
            "backgroundColor": ["#1877F2", "#1DA1F2", "#E1306C"],
        }],
    }
    await cache_set(cache_key, result, ttl=ANALYTICS_TTL)
    return result


# ── Followers by zone ──────────────────────────────────────────────────────────
@app.get("/api/analytics/zone-followers")
async def zone_followers(
    zone:           Optional[str] = None,
    party_district: Optional[str] = None,
    constituency:   Optional[str] = None,
    designation:    Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    cache_key = f"analytics:zone:{zone}:{party_district}:{constituency}:{designation}"
    cached    = await cache_get(cache_key)
    if cached:
        return cached

    total_col = (
        func.coalesce(SocialProfile.facebook_followers,  0) +
        func.coalesce(SocialProfile.twitter_followers,   0) +
        func.coalesce(SocialProfile.instagram_followers, 0)
    )
    stmt = (
        select(SocialProfile.zone, func.sum(total_col).label("total"))
        .group_by(SocialProfile.zone)
        .order_by(desc("total"))
        .limit(12)
    )
    stmt   = _apply_filters(stmt, None, zone, party_district, constituency, designation, False, False)
    rows   = (await db.execute(stmt)).all()
    result = {
        "labels":   [r[0] or "Unknown" for r in rows],
        "datasets": [{
            "label":           "Total Followers by Zone",
            "data":            [int(r[1] or 0) for r in rows],
            "backgroundColor": "#FFCE56",
        }],
    }
    await cache_set(cache_key, result, ttl=ANALYTICS_TTL)
    return result


# ── Profiles by designation ────────────────────────────────────────────────────
@app.get("/api/analytics/designation-count")
async def designation_count(
    zone:           Optional[str] = None,
    party_district: Optional[str] = None,
    constituency:   Optional[str] = None,
    designation:    Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    cache_key = f"analytics:desig:{zone}:{party_district}:{constituency}:{designation}"
    cached    = await cache_get(cache_key)
    if cached:
        return cached

    stmt = (
        select(SocialProfile.designation, func.count(SocialProfile.id).label("cnt"))
        .group_by(SocialProfile.designation)
        .order_by(desc("cnt"))
        .limit(10)
    )
    stmt   = _apply_filters(stmt, None, zone, party_district, constituency, designation, False, False)
    rows   = (await db.execute(stmt)).all()
    result = {
        "labels":   [r[0] or "Unknown" for r in rows],
        "datasets": [{
            "label":           "Profiles by Designation",
            "data":            [r[1] for r in rows],
            "backgroundColor": "#4BC0C0",
        }],
    }
    await cache_set(cache_key, result, ttl=ANALYTICS_TTL)
    return result


# ── Filter options ─────────────────────────────────────────────────────────────
@app.get("/api/filter-options")
async def filter_options(db: AsyncSession = Depends(get_db)):
    cached = await cache_get("options:all")
    if cached:
        return cached

    async def distinct(col):
        stmt = select(col).distinct().where(col.isnot(None)).order_by(col)
        return [r[0] for r in (await db.execute(stmt)).all()]

    result = {
        "zones":           await distinct(SocialProfile.zone),
        "party_districts": await distinct(SocialProfile.party_district),
        "constituencies":  await distinct(SocialProfile.constituency),
        "designations":    await distinct(SocialProfile.designation),
    }
    await cache_set("options:all", result, ttl=OPTIONS_TTL)
    return result


# ── CSV Export ─────────────────────────────────────────────────────────────────
EXPORT_FIELDS = [
    "id", "zone", "party_district", "constituency", "designation", "name",
    "whatsapp_number", "dob", "address", "email_id",
    "facebook_id",  "facebook_followers",  "facebook_active_status",  "facebook_verified_status",
    "twitter_id",   "twitter_followers",   "twitter_active_status",   "twitter_verified_status",
    "instagram_id", "instagram_followers", "instagram_active_status", "instagram_verified_status",
]


@app.get("/api/export/csv")
async def export_csv(
    search:         Optional[str] = None,
    zone:           Optional[str] = None,
    party_district: Optional[str] = None,
    constituency:   Optional[str] = None,
    designation:    Optional[str] = None,
    active_only:    bool = False,
    verified_only:  bool = False,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SocialProfile).order_by(SocialProfile.id)
    stmt = _apply_filters(stmt, search, zone, party_district, constituency,
                          designation, active_only, verified_only)
    rows = (await db.execute(stmt)).scalars().all()

    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS)
    w.writeheader()
    for r in rows:
        w.writerow({f: getattr(r, f, None) for f in EXPORT_FIELDS})

    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="export.csv"'},
    )


# ── Entrypoint ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=5000, reload=True)