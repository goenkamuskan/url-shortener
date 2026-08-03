from fastapi import FastAPI
from app.routers import urls

app = FastAPI(title="URL Shortener")

app.include_router(urls.router)


@app.get("/")
def root():
    return {"status": "ok", "service": "url-shortener"}