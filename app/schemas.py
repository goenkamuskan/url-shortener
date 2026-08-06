from pydantic import BaseModel, HttpUrl
from datetime import datetime
from typing import Optional
from typing import List


class ShortenRequest(BaseModel):
    long_url: HttpUrl


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    long_url: str


class ClickByDay(BaseModel):
    date: str
    count: int


class ReferrerCount(BaseModel):
    referrer: Optional[str]
    count: int


class AnalyticsResponse(BaseModel):
    short_code: str
    long_url: str
    total_clicks: int
    clicks_by_day: List[ClickByDay]
    top_referrers: List[ReferrerCount]