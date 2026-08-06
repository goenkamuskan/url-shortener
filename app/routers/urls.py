from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import json
from app.core.database import get_db
from app.core.encoding import encode_base62
from app.core.config import BASE_URL
from app.models import URL, Click
from app.schemas import ShortenRequest, ShortenResponse
from app.core.redis_client import redis_client
from app.core.rate_limiter import is_allowed

from sqlalchemy import func as sql_func
from app.schemas import AnalyticsResponse, ClickByDay, ReferrerCount
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks


def log_click(url_id: int, referrer: str | None, user_agent: str | None):
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        click = Click(url_id=url_id, referrer=referrer, user_agent=user_agent)
        db.add(click)
        db.commit()
    finally:
        db.close()

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

    redis_client.setex(
    f"url:{new_url.short_code}", 3600,
    json.dumps({"long_url": new_url.long_url, "url_id": new_url.id})
    )

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
def redirect_to_long_url(
    code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    cache_key = f"url:{code}"
    cached = redis_client.get(cache_key)

    if cached:
        data = json.loads(cached)
        long_url = data["long_url"]
        url_id = data["url_id"]
    else:
        url_entry = db.query(URL).filter(URL.short_code == code).first()
        if not url_entry:
            raise HTTPException(status_code=404, detail="Short URL not found")
        long_url = url_entry.long_url
        url_id = url_entry.id
        redis_client.setex(
            cache_key, 3600,
            json.dumps({"long_url": long_url, "url_id": url_id})
        )

    background_tasks.add_task(
        log_click,
        url_id,
        request.headers.get("referer"),
        request.headers.get("user-agent"),
    )

    return RedirectResponse(url=long_url)