import asyncio
import hashlib
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP = FastAPI(title="Exam Intelligence API", version="1.1.0")
APP.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PIB_RSS = os.getenv("PIB_RSS_URL", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1").strip()
GOOGLE_QUERIES = [
    ("National", "India government ministry scheme policy"),
    ("Economy", "India RBI economy inflation banking budget"),
    ("Science & Technology", "India ISRO science technology AI space research"),
    ("Environment", "India environment climate biodiversity wildlife"),
    ("Defence", "India defence military DRDO navy army air force"),
    ("International", "India international relations UN UNESCO BRICS G20"),
    ("Sports", "India sports Olympics cricket hockey athletics"),
    ("Awards & Culture", "India awards books authors culture heritage GI tag UNESCO"),
    ("Education & Health", "India education health government programme"),
]
_cache: dict[str, Any] = {"updated": None, "items": [], "sources": []}
_lock = asyncio.Lock()
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "900"))

class Article(BaseModel):
    id: str
    source: str
    title: str
    url: str
    published_at: str | None = None
    category: str = "National"
    summary: str = ""
    exam_relevance: list[str] = Field(default_factory=list)
    verification: str = "source-observed"

def clean_text(value: Any) -> str:
    text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()

def classify(title: str, summary: str = "") -> str:
    t = f"{title} {summary}".lower()
    if any(x in t for x in ["telangana", "hyderabad", "telangana government", "revanth"]): return "Telangana"
    if any(x in t for x in ["science", "technology", "isro", "space", "research", "ai", "quantum", "semiconductor"]): return "Science & Technology"
    if any(x in t for x in ["environment", "climate", "forest", "wildlife", "biodiversity", "pollution", "wetland"]): return "Environment"
    if any(x in t for x in ["rbi", "bank", "inflation", "economy", "gdp", "finance", "fiscal", "monetary", "sebi"]): return "Economy"
    if any(x in t for x in ["defence", "defense", "army", "navy", "air force", "missile", "drdo", "military"]): return "Defence"
    if any(x in t for x in ["election", "parliament", "supreme court", "high court", "constitution", "bill", "minister", "cabinet", "ordinance"]): return "Polity"
    if any(x in t for x in ["sport", "cricket", "hockey", "olympic", "athletics", "badminton"]): return "Sports"
    if any(x in t for x in ["un ", "united nations", "unesco", "g20", "brics", "summit", "foreign", "bilateral", "quad"]): return "International"
    if any(x in t for x in ["award", "book", "author", "heritage", "gi tag", "culture", "festival"]): return "Awards & Culture"
    if any(x in t for x in ["education", "school", "university", "health", "hospital", "disease", "vaccin"]): return "Education & Health"
    return "National"

def relevance_for(category: str, title: str) -> list[str]:
    tags = ["UPSC", "TGPSC"]
    if category in {"Polity", "Economy", "International", "Environment", "Science & Technology", "Defence"}: tags.append("Prelims")
    if category in {"Polity", "Economy", "International", "Environment", "Telangana"}: tags.append("Mains")
    if any(x in title.lower() for x in ["scheme", "programme"]): tags.append("Government Schemes")
    return list(dict.fromkeys(tags))

async def fetch_feed(name: str, url: str, category_hint: str | None = None):
    status = {"source": name, "url": url, "ok": False, "count": 0, "error": None}
    if not url:
        status["error"] = "not configured"
        return [], status
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={
            "User-Agent": "ExamIntelligence/1.1", "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8"
        }) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.text
        parsed = feedparser.parse(body)
        items = []
        for entry in parsed.entries[:60]:
            title = clean_text(entry.get("title"))
            link = str(entry.get("link", "")).strip()
            if not title or not link: continue
            summary = clean_text(entry.get("summary") or entry.get("description"))
            source_meta = entry.get("source")
            source_name = name
            if isinstance(source_meta, dict) and source_meta.get("title"):
                source_name = f"{name} · {clean_text(source_meta.get('title'))}"
            category = category_hint or classify(title, summary)
            ident = hashlib.sha256(f"{source_name}|{title}|{link}".encode()).hexdigest()[:20]
            published = entry.get("published") or entry.get("updated")
            items.append(Article(
                id=ident, source=source_name, title=title, url=link,
                published_at=str(published) if published else None,
                category=category,
                summary=f"Fetched from {source_name}. Open the source for the complete verified context.",
                exam_relevance=relevance_for(category, title),
                verification="source-observed" if name in {"PIB", "RBI", "Telangana State Portal"} else "aggregated-source",
            ))
        status["ok"] = True
        status["count"] = len(items)
        return items, status
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        return [], status

async def fetch_telangananews():
    url = "https://www.telangana.gov.in/news/"
    status = {"source": "Telangana State Portal", "url": url, "ok": False, "count": 0, "error": None}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "ExamIntelligence/1.1"}) as client:
            response = await client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
        items, seen = [], set()
        for a in soup.find_all("a", href=True):
            title, href = clean_text(a.get_text(" ", strip=True)), str(a.get("href", "")).strip()
            if len(title) < 25 or "read more" in title.lower(): continue
            if href.startswith("/"): href = "https://www.telangana.gov.in" + href
            if not href.startswith("https://www.telangana.gov.in/"): continue
            key = re.sub(r"[^a-z0-9]", "", title.lower())
            if key in seen: continue
            seen.add(key)
            ident = hashlib.sha256(f"telangana|{title}|{href}".encode()).hexdigest()[:20]
            items.append(Article(
                id=ident, source="Telangana State Portal", title=title, url=href,
                category="Telangana",
                summary="Fetched from the official Telangana State Portal. Open the source for the complete verified context.",
                exam_relevance=["TGPSC", "Prelims", "Mains"], verification="source-observed"
            ))
            if len(items) >= 40: break
        status["ok"], status["count"] = True, len(items)
        return items, status
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        return [], status

async def ingest():
    async with _lock:
        feeds = [("PIB", PIB_RSS, None)]
        for category, query in GOOGLE_QUERIES:
            url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-IN&gl=IN&ceid=IN:en"
            feeds.append((f"Google News · {category}", url, category))
        results = await asyncio.gather(
            *(fetch_feed(name, url, hint) for name, url, hint in feeds),
            fetch_telangananews(),
        )
        all_items, statuses = [], []
        for result_items, status in results:
            all_items.extend(result_items); statuses.append(status)
        seen, unique = set(), []
        for item in all_items:
            key = re.sub(r"[^a-z0-9]", "", item.title.lower())
            if key and key not in seen:
                seen.add(key); unique.append(item)
        unique.sort(key=lambda x: x.published_at or "", reverse=True)
        _cache["items"], _cache["updated"], _cache["sources"] = unique[:500], datetime.now(timezone.utc).isoformat(), statuses
        return _cache["items"]

def cache_stale():
    value = _cache.get("updated")
    if not value: return True
    try: return datetime.now(timezone.utc) - datetime.fromisoformat(value) > timedelta(seconds=CACHE_TTL_SECONDS)
    except Exception: return True

@APP.get("/health")
async def health():
    return {"status":"ok","service":"exam-intelligence-api","cache_items":len(_cache["items"]),"last_ingest":_cache["updated"],"sources_ok":sum(1 for s in _cache["sources"] if s.get("ok")),"sources_total":len(_cache["sources"])}

@APP.get("/api/source-status")
async def source_status():
    if not _cache["sources"] or cache_stale(): await ingest()
    return {"updated":_cache["updated"],"sources":_cache["sources"],"items":len(_cache["items"])}

@APP.post("/api/ingest")
async def ingest_route():
    items = await ingest()
    return {"status":"ok","count":len(items),"updated":_cache["updated"],"sources":_cache["sources"]}

@APP.get("/api/current-affairs", response_model=list[Article])
async def current_affairs(q: str | None=None, category: str | None=None, limit: int=Query(default=50, ge=1, le=100)):
    if not _cache["items"] or cache_stale(): await ingest()
    arr = _cache["items"]
    if q:
        needle = q.lower()
        arr = [x for x in arr if needle in (x.title+" "+x.summary+" "+x.source+" "+x.category).lower()]
    if category and category != "All": arr = [x for x in arr if x.category == category]
    return arr[:limit]

@APP.get("/api/search", response_model=list[Article])
async def search(q: str=Query(min_length=1), limit: int=Query(default=30, ge=1, le=100)):
    if not _cache["items"] or cache_stale(): await ingest()
    needle = q.lower()
    return [x for x in _cache["items"] if needle in (x.title+" "+x.summary+" "+x.category+" "+x.source).lower()][:limit]

@APP.get("/api/today")
async def today():
    items = await current_affairs(limit=100)
    return {"date":datetime.now(timezone.utc).date().isoformat(),"count":len(items),"items":items,"updated":_cache["updated"]}

@APP.on_event("startup")
async def startup_ingest():
    asyncio.create_task(ingest())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(APP, host="0.0.0.0", port=int(os.getenv("PORT","8000")))
