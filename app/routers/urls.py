from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.encoding import encode_base62
from app.core.config import BASE_URL
from app.models import URL, Click
from app.schemas import ShortenRequest, ShortenResponse
from app.core.redis_client import redis_client
from app.core.rate_limiter import is_allowed

from sqlalchemy import func as sql_func
from app.schemas import AnalyticsResponse, ClickByDay, ReferrerCount

router = APIRouter()



@router.post("/shorten", response_model=ShortenResponse)
def shorten_url(payload: ShortenRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host

    if not is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Try again shortly.",
            headers={"Retry-After": "1"},
        )

    new_url = URL(long_url=str(payload.long_url))
    db.add(new_url)
    db.commit()
    db.refresh(new_url)

    new_url.short_code = encode_base62(new_url.id)
    db.commit()

    redis_client.setex(f"url:{new_url.short_code}", 3600, new_url.long_url)

    return ShortenResponse(
        short_code=new_url.short_code,
        short_url=f"{BASE_URL}/{new_url.short_code}",
        long_url=new_url.long_url,
    )

@router.get("/analytics/{code}", response_model=AnalyticsResponse)
def get_analytics(code: str, db: Session = Depends(get_db)):
    url_entry = db.query(URL).filter(URL.short_code == code).first()
    if not url_entry:
        raise HTTPException(status_code=404, detail="Short URL not found")

    total_clicks = db.query(Click).filter(Click.url_id == url_entry.id).count()

    clicks_by_day_raw = (
        db.query(
            sql_func.date(Click.clicked_at).label("day"),
            sql_func.count(Click.id).label("count"),
        )
        .filter(Click.url_id == url_entry.id)
        .group_by(sql_func.date(Click.clicked_at))
        .order_by(sql_func.date(Click.clicked_at))
        .all()
    )
    clicks_by_day = [
        ClickByDay(date=str(row.day), count=row.count) for row in clicks_by_day_raw
    ]

    top_referrers_raw = (
        db.query(
            Click.referrer,
            sql_func.count(Click.id).label("count"),
        )
        .filter(Click.url_id == url_entry.id)
        .group_by(Click.referrer)
        .order_by(sql_func.count(Click.id).desc())
        .limit(5)
        .all()
    )
    top_referrers = [
        ReferrerCount(referrer=row.referrer, count=row.count) for row in top_referrers_raw
    ]

    return AnalyticsResponse(
        short_code=url_entry.short_code,
        long_url=url_entry.long_url,
        total_clicks=total_clicks,
        clicks_by_day=clicks_by_day,
        top_referrers=top_referrers,
    )

@router.get("/{code}")
def redirect_to_long_url(code: str, request: Request, db: Session = Depends(get_db)):
    cache_key = f"url:{code}"

    # 1. Check Redis first
    cached_url = redis_client.get(cache_key)
    if cached_url:
        long_url = cached_url
    else:
        # 2. Cache miss — query Postgres
        url_entry = db.query(URL).filter(URL.short_code == code).first()
        if not url_entry:
            raise HTTPException(status_code=404, detail="Short URL not found")
        long_url = url_entry.long_url

        # 3. Populate cache for next time (1 hour TTL)
        redis_client.setex(cache_key, 3600, long_url)

    # Log the click regardless of cache hit/miss — analytics still needs DB truth
    url_id_row = db.query(URL.id).filter(URL.short_code == code).first()
    if url_id_row:
        click = Click(
            url_id=url_id_row[0],
            referrer=request.headers.get("referer"),
            user_agent=request.headers.get("user-agent"),
        )
        db.add(click)
        db.commit()

    return RedirectResponse(url=long_url)