import asyncio, os, re, hashlib
from datetime import datetime, timezone
from typing import Any
import feedparser, httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP=FastAPI(title="Exam Intelligence API", version="1.0.0")
APP.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SOURCES = [
    ("PIB", os.getenv("PIB_RSS_URL","https://www.pib.gov.in/RssMain.aspx")),
    ("RBI", os.getenv("RBI_RSS_URL","https://www.rbi.org.in/Scripts/Rss.aspx")),
]
_cache={"updated":None,"items":[]}
_lock=asyncio.Lock()

class Article(BaseModel):
    id:str; source:str; title:str; url:str; published_at:str|None
    category:str="National"; summary:str=""; exam_relevance:list[str]=[]
    verification:str="source-observed"

def classify(title:str)->str:
    t=title.lower()
    if any(x in t for x in ["science","technology","isro","space","research","ai","quantum"]): return "Science & Technology"
    if any(x in t for x in ["environment","climate","forest","wildlife","biodiversity"]): return "Environment"
    if any(x in t for x in ["rbi","bank","inflation","economy","gdp","finance"]): return "Economy"
    if any(x in t for x in ["defence","defense","army","navy","air force","missile"]): return "Defence"
    if any(x in t for x in ["election","parliament","court","constitution","bill","minister"]): return "Polity"
    if any(x in t for x in ["sport","cricket","hockey","olympic"]): return "Sports"
    if any(x in t for x in ["un","unesco","g20","brics","summit","foreign"]): return "International"
    return "National"

async def fetch_source(name,url):
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent":"ExamIntelligence/1.0"}) as c:
            r=await c.get(url)
            r.raise_for_status()
            text=r.text
        parsed=feedparser.parse(text)
        items=[]
        for e in parsed.entries[:30]:
            title=re.sub(r"\s+"," ",str(e.get("title",""))).strip()
            link=str(e.get("link","")).strip()
            if not title: continue
            raw=f"{name}|{title}|{link}"
            ident=hashlib.sha256(raw.encode()).hexdigest()[:16]
            pub=e.get("published") or e.get("updated")
            items.append(Article(id=ident,source=name,title=title,url=link,published_at=pub,category=classify(title),
                summary=f"Source-observed item from {name}. Open the primary source for the complete context.",
                exam_relevance=["UPSC","TGPSC"]))
        return items
    except Exception:
        return []

async def ingest():
    async with _lock:
        all_items=[]
        for name,url in SOURCES:
            all_items.extend(await fetch_source(name,url))
        # stable dedup by normalized title
        seen=set(); unique=[]
        for x in all_items:
            key=re.sub(r"[^a-z0-9]","",x.title.lower())
            if key in seen: continue
            seen.add(key); unique.append(x)
        _cache["items"]=unique
        _cache["updated"]=datetime.now(timezone.utc).isoformat()
        return unique

@APP.get("/health")
async def health():
    return {"status":"ok","service":"exam-intelligence-api","cache_items":len(_cache["items"]),"last_ingest":_cache["updated"]}

@APP.post("/api/ingest")
async def ingest_route():
    items=await ingest()
    return {"status":"ok","count":len(items),"updated":_cache["updated"]}

@APP.get("/api/current-affairs",response_model=list[Article])
async def current_affairs(q:str|None=None,category:str|None=None,limit:int=50):
    if not _cache["items"]: await ingest()
    arr=_cache["items"]
    if q: arr=[x for x in arr if q.lower() in x.title.lower()]
    if category and category!="All": arr=[x for x in arr if x.category==category]
    return arr[:min(limit,100)]

@APP.get("/api/search",response_model=list[Article])
async def search(q:str=Query(min_length=1),limit:int=30):
    if not _cache["items"]: await ingest()
    return [x for x in _cache["items"] if q.lower() in (x.title+" "+x.summary+" "+x.category).lower()][:limit]

@APP.get("/api/today")
async def today():
    items=await current_affairs(limit=100)
    return {"date":datetime.now().date().isoformat(),"count":len(items),"items":items}

if __name__=="__main__":
    import uvicorn
    uvicorn.run(APP,host="0.0.0.0",port=int(os.getenv("PORT","8000")))
