from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.encoding import encode_base62
from app.core.config import BASE_URL
from app.models import URL, Click
from app.schemas import ShortenRequest, ShortenResponse

router = APIRouter()


@router.post("/shorten", response_model=ShortenResponse)
def shorten_url(payload: ShortenRequest, db: Session = Depends(get_db)):
    new_url = URL(long_url=str(payload.long_url))
    db.add(new_url)
    db.commit()
    db.refresh(new_url)  # populates new_url.id from the DB

    new_url.short_code = encode_base62(new_url.id)
    db.commit()

    return ShortenResponse(
        short_code=new_url.short_code,
        short_url=f"{BASE_URL}/{new_url.short_code}",
        long_url=new_url.long_url,
    )


@router.get("/{code}")
def redirect_to_long_url(code: str, request: Request, db: Session = Depends(get_db)):
    url_entry = db.query(URL).filter(URL.short_code == code).first()
    if not url_entry:
        raise HTTPException(status_code=404, detail="Short URL not found")

    # log the click (kept simple/synchronous for now — we'll revisit for performance later)
    click = Click(
        url_id=url_entry.id,
        referrer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
    )
    db.add(click)
    db.commit()

    return RedirectResponse(url=url_entry.long_url)