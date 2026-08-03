from pydantic import BaseModel, HttpUrl
from datetime import datetime
from typing import Optional


class ShortenRequest(BaseModel):
    long_url: HttpUrl


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    long_url: str


class AnalyticsResponse(BaseModel):
    short_code: str
    total_clicks: int