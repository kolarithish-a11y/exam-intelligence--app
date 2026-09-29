import asyncio
import hashlib
import os
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP = FastAPI(title="Exam Intelligence API", version="2.0.0")
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
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))
DAILY_WINDOW_HOURS = int(os.getenv("DAILY_WINDOW_HOURS", "48"))
MAX_CACHE_ITEMS = int(os.getenv("MAX_CACHE_ITEMS", "1000"))
_cache: dict[str, Any] = {"updated": None, "items": [], "sources": []}
_lock = asyncio.Lock()

class StaticGK(BaseModel):
    concept: str
    facts: list[str] = Field(default_factory=list)
    background: str = ""

class Article(BaseModel):
    id: str
    source: str
    title: str
    url: str
    published_at: str | None = None
    category: str = "National"
    summary: str = ""
    why_important: str = ""
    static_gk: StaticGK
    exam_relevance: list[str] = Field(default_factory=list)
    verification: str = "source-observed"

def clean_text(value: Any) -> str:
    text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()

def iso_date(value: Any) -> str | None:
    if not value: return None
    try:
        if isinstance(value, tuple):
            dt = datetime(*value[:6], tzinfo=timezone.utc)
        else:
            raw = str(value).strip()
            try: dt = parsedate_to_datetime(raw)
            except Exception: dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None

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

def static_gk_for(category: str, title: str) -> StaticGK:
    t = title.lower()
    maps = {
        "Polity": ("Indian Polity & Constitution", ["India has a written Constitution adopted on 26 November 1949 and in force from 26 January 1950.","Parliament consists of the President, Rajya Sabha and Lok Sabha.","The Supreme Court is the apex court under the Constitution."], "Connect the event with the relevant constitutional article, institution, law, amendment, federal feature or parliamentary procedure."),
        "Economy": ("Indian Economy & Financial System", ["The Reserve Bank of India is India's central bank and conducts monetary policy.","The Monetary Policy Committee determines the policy repo rate under the monetary policy framework.","Fiscal policy concerns government taxation, expenditure and borrowing."], "Identify the institution, instrument, indicator and policy channel involved; distinguish monetary policy from fiscal policy."),
        "Environment": ("Environment, Ecology & Biodiversity", ["India is one of the world's megadiverse countries.","The Ramsar Convention concerns wetlands of international importance.","The Convention on Biological Diversity addresses conservation, sustainable use and benefit sharing."], "Link the event to ecosystem type, biodiversity, protected areas, environmental conventions, climate policy and Indian environmental institutions."),
        "Science & Technology": ("Science, Technology & Innovation", ["ISRO is India's national space agency.","Remote sensing, satellites and launch vehicles have applications in communication, navigation, weather and Earth observation.","Scientific current affairs are commonly tested through concepts, applications and institutions."], "Identify the underlying scientific principle, technology, institution, application and any Indian mission or programme involved."),
        "Defence": ("Indian Defence & Security", ["India's armed forces comprise the Army, Navy and Air Force.","DRDO is the principal defence research and development organisation under the Ministry of Defence.","Defence questions often test platforms, exercises, commands, technologies and strategic geography."], "Connect the news to the relevant service, platform, exercise, technology, command structure and strategic region."),
        "International": ("International Relations & Organisations", ["The United Nations was established in 1945.","UNESCO works in education, science, culture and communication.","BRICS is a grouping whose membership and institutional arrangements can change over time."], "Identify the organisation, membership, headquarters, mandate, treaty/framework and India's role."),
        "Sports": ("Sports & Major Events", ["The Olympic Games are governed internationally by the International Olympic Committee.","Major sporting events can generate questions on host cities, venues, governing bodies, records and terminology.","Sports current affairs are often tested as factual one-liners."], "Track the athlete/team, event, governing body, venue, host and achievement without relying on unsourced claims."),
        "Awards & Culture": ("Culture, Heritage, Literature & Awards", ["UNESCO lists include World Heritage, Intangible Cultural Heritage and Memory of the World programmes.","GI registration identifies goods associated with a particular geographical origin and qualities or reputation linked to that origin.","Indian culture questions commonly connect sites, traditions, texts, crafts and institutions."], "Connect the item with its state/region, historical period, tradition, institution, GI/UNESCO status and distinctive features."),
        "Education & Health": ("Education, Health & Human Development", ["Public health includes prevention, surveillance, treatment and health-system capacity.","India's education system is governed through constitutional, statutory and policy frameworks involving Union and State institutions.","Health and education schemes should be studied with their target groups and implementing ministries."], "Identify the ministry/institution, target population, programme objective, implementation mechanism and measurable outcome."),
        "Telangana": ("Telangana: Polity, Economy, Geography & Culture", ["Telangana was formed as a separate State on 2 June 2014.","Hyderabad is the capital of Telangana.","TGPSC questions can connect current events with Telangana geography, history, economy, schemes and administration."], "Connect the news with the relevant Telangana district, department, scheme, geography, history, culture, economy or administrative institution.")
    }
    concept, facts, background = maps.get(category, ("General Studies Static GK", ["Current affairs should be connected to a permanent concept, institution, place, law, person, report or historical background."], "Use the event as an entry point into the underlying static General Studies concept."))
    if "scheme" in t or "programme" in t or "mission" in t: facts.append("For government schemes, remember the nodal ministry/department, target beneficiaries, objective, funding pattern and implementation mechanism.")
    if "gi tag" in t or "geographical indication" in t: facts.append("A Geographical Indication identifies goods whose quality, reputation or other characteristic is essentially attributable to geographical origin.")
    if "unesco" in t: facts.append("UNESCO is a specialised agency of the United Nations concerned with education, science, culture and communication.")
    return StaticGK(concept=concept, facts=facts[:6], background=background)

def relevance_for(category: str, title: str) -> list[str]:
    tags = ["UPSC", "TGPSC", "SSC"]
    if category in {"Polity","Economy","International","Environment","Science & Technology","Defence","Telangana"}: tags.append("Prelims")
    if category in {"Polity","Economy","International","Environment","Telangana","Science & Technology"}: tags.append("Mains")
    if any(x in title.lower() for x in ["scheme","programme","mission"]): tags.append("Government Schemes")
    return list(dict.fromkeys(tags))

def why_important_for(category: str) -> str:
    return {
        "Polity":"Potential relevance to constitutional governance, institutions, legislation, federalism or public administration.",
        "Economy":"Potential relevance to monetary/fiscal policy, banking, markets, growth, inflation or public finance.",
        "Environment":"Potential relevance to ecology, biodiversity, climate policy, conservation or environmental governance.",
        "Science & Technology":"Potential relevance to scientific concepts, Indian missions, innovation, technology applications or strategic capability.",
        "Defence":"Potential relevance to defence technology, exercises, platforms, strategic geography or national security.",
        "International":"Potential relevance to India's foreign policy, international organisations, groupings, treaties or global governance.",
        "Sports":"Potential factual relevance to major events, athletes, governing bodies, records and venues.",
        "Awards & Culture":"Potential relevance to heritage, literature, arts, GI tags, UNESCO frameworks and Indian culture.",
        "Education & Health":"Potential relevance to public policy, human development, institutions, schemes and service delivery.",
        "Telangana":"Potential relevance to Telangana-specific polity, geography, economy, schemes, history, culture and TGPSC preparation."
    }.get(category, "Potential relevance to General Studies and objective current-affairs questions.")

def make_article(name: str, entry: Any, category_hint: str | None = None) -> Article | None:
    title, link = clean_text(entry.get("title")), str(entry.get("link","")).strip()
    if not title or not link: return None
    summary_raw = clean_text(entry.get("summary") or entry.get("description"))
    category = category_hint or classify(title, summary_raw)
    source_meta, source_name = entry.get("source"), name
    if isinstance(source_meta, dict) and source_meta.get("title"): source_name = f"{name} · {clean_text(source_meta.get('title'))}"
    published = iso_date(entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published") or entry.get("updated"))
    ident = hashlib.sha256(f"{source_name}|{title}|{link}".encode()).hexdigest()[:20]
    return Article(id=ident,source=source_name,title=title,url=link,published_at=published,category=category,
        summary=summary_raw or f"Source-observed item from {source_name}. Open the original source for full context.",
        why_important=why_important_for(category),static_gk=static_gk_for(category,title),exam_relevance=relevance_for(category,title),
        verification="source-observed" if name in {"PIB","RBI","Telangana State Portal"} else "aggregated-source")

async def fetch_feed(name: str, url: str, category_hint: str | None = None):
    status={"source":name,"url":url,"ok":False,"count":0,"error":None}
    if not url: status["error"]="not configured"; return [],status
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=True,headers={"User-Agent":"ExamIntelligence/2.0","Accept":"application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8"}) as client:
            response=await client.get(url); response.raise_for_status(); parsed=feedparser.parse(response.text)
        items=[]
        for entry in parsed.entries[:80]:
            item=make_article(name,entry,category_hint)
            if item: items.append(item)
        status["ok"],status["count"]=True,len(items); return items,status
    except Exception as exc:
        status["error"]=f"{type(exc).__name__}: {str(exc)[:180]}"; return [],status

async def fetch_telangananews():
    url="https://www.telangana.gov.in/news/"
    status={"source":"Telangana State Portal","url":url,"ok":False,"count":0,"error":None}
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=True,headers={"User-Agent":"ExamIntelligence/2.0"}) as client:
            response=await client.get(url); response.raise_for_status(); soup=BeautifulSoup(response.text,"html.parser")
        items,seen=[],set()
        for a in soup.find_all("a",href=True):
            title,href=clean_text(a.get_text(" ",strip=True)),str(a.get("href","")).strip()
            if len(title)<25 or "read more" in title.lower(): continue
            if href.startswith("/"): href="https://www.telangana.gov.in"+href
            if not href.startswith("https://www.telangana.gov.in/"): continue
            key=re.sub(r"[^a-z0-9]","",title.lower())
            if key in seen: continue
            seen.add(key)
            items.append(Article(id=hashlib.sha256(f"telangana|{title}|{href}".encode()).hexdigest()[:20],source="Telangana State Portal",title=title,url=href,category="Telangana",
                summary="Fetched from the official Telangana State Portal. Open the source for complete context.",why_important=why_important_for("Telangana"),
                static_gk=static_gk_for("Telangana",title),exam_relevance=["TGPSC","SSC","Prelims","Mains"],verification="source-observed"))
            if len(items)>=60: break
        status["ok"],status["count"]=True,len(items); return items,status
    except Exception as exc:
        status["error"]=f"{type(exc).__name__}: {str(exc)[:180]}"; return [],status

def dedupe(items: list[Article]) -> list[Article]:
    seen,unique=set(),[]
    for item in items:
        key=re.sub(r"[^a-z0-9]","",item.title.lower())
        if key and key not in seen: seen.add(key); unique.append(item)
    unique.sort(key=lambda x:x.published_at or "",reverse=True)
    return unique

async def ingest():
    async with _lock:
        feeds=[("PIB",PIB_RSS,None)]
        for category,query in GOOGLE_QUERIES:
            feeds.append((f"Google News · {category}","https://news.google.com/rss/search?q="+quote_plus(query)+"&hl=en-IN&gl=IN&ceid=IN:en",category))
        results=await asyncio.gather(*(fetch_feed(name,url,hint) for name,url,hint in feeds),fetch_telangananews())
        all_items,statuses=[],[]
        for result_items,status in results: all_items.extend(result_items); statuses.append(status)
        _cache["items"],_cache["updated"],_cache["sources"]=dedupe(all_items)[:MAX_CACHE_ITEMS],datetime.now(timezone.utc).isoformat(),statuses
        return _cache["items"]

def cache_stale():
    value=_cache.get("updated")
    if not value: return True
    try: return datetime.now(timezone.utc)-datetime.fromisoformat(value)>timedelta(seconds=CACHE_TTL_SECONDS)
    except Exception: return True

def in_daily_window(item: Article):
    if not item.published_at: return True
    try: return datetime.now(timezone.utc)-datetime.fromisoformat(item.published_at)<=timedelta(hours=DAILY_WINDOW_HOURS)
    except Exception: return True

async def historical_fetch(q: str | None, category: str | None, from_date: str, to_date: str | None):
    try:
        start=datetime.fromisoformat(from_date).date(); end=datetime.fromisoformat(to_date or from_date).date()
        if end<start: raise ValueError("to_date must be on/after from_date")
    except Exception as exc: raise ValueError(f"Invalid date range: {exc}")
    before=end+timedelta(days=1); base=q.strip() if q else "India current affairs"
    if category and category!="All": base=f"{base} {category}"
    query=f"{base} after:{start.isoformat()} before:{before.isoformat()}"
    items,_=await fetch_feed("Google News · Historical Search","https://news.google.com/rss/search?q="+quote_plus(query)+"&hl=en-IN&gl=IN&ceid=IN:en",category if category and category!="All" else None)
    return dedupe(items)[:200]

@APP.get("/health")
async def health():
    return {"status":"ok","service":"exam-intelligence-api","cache_items":len(_cache["items"]),"last_ingest":_cache["updated"],"sources_ok":sum(1 for s in _cache["sources"] if s.get("ok")),"sources_total":len(_cache["sources"]),"refresh_seconds":CACHE_TTL_SECONDS,"daily_window_hours":DAILY_WINDOW_HOURS}

@APP.get("/api/source-status")
async def source_status():
    if not _cache["sources"] or cache_stale(): await ingest()
    return {"updated":_cache["updated"],"sources":_cache["sources"],"items":len(_cache["items"])}

@APP.post("/api/ingest")
async def ingest_route():
    items=await ingest()
    return {"status":"ok","count":len(items),"updated":_cache["updated"],"sources":_cache["sources"]}

@APP.get("/api/current-affairs",response_model=list[Article])
async def current_affairs(q:str|None=None,category:str|None=None,from_date:str|None=None,to_date:str|None=None,limit:int=Query(default=50,ge=1,le=200)):
    if from_date: return (await historical_fetch(q,category,from_date,to_date))[:limit]
    if not _cache["items"] or cache_stale(): await ingest()
    arr=[x for x in _cache["items"] if in_daily_window(x)]
    if q:
        needle=q.lower(); arr=[x for x in arr if needle in (x.title+" "+x.summary+" "+x.source+" "+x.category).lower()]
    if category and category!="All": arr=[x for x in arr if x.category==category]
    return arr[:limit]

@APP.get("/api/search",response_model=list[Article])
async def search(q:str=Query(min_length=1),from_date:str|None=None,to_date:str|None=None,category:str|None=None,limit:int=Query(default=100,ge=1,le=200)):
    if from_date: return (await historical_fetch(q,category,from_date,to_date))[:limit]
    if not _cache["items"] or cache_stale(): await ingest()
    needle=q.lower()
    return [x for x in _cache["items"] if needle in (x.title+" "+x.summary+" "+x.category+" "+x.source).lower()][:limit]

@APP.get("/api/today")
async def today():
    items=await current_affairs(limit=200)
    return {"date":datetime.now(timezone.utc).date().isoformat(),"count":len(items),"items":items,"updated":_cache["updated"],"message":"Daily edition shows recent items; use date search for historical coverage."}

@APP.get("/api/archive")
async def archive(date:str|None=None,from_date:str|None=None,to_date:str|None=None,q:str|None=None,category:str|None=None,limit:int=Query(default=100,ge=1,le=200)):
    target_from=date or from_date
    if not target_from: raise ValueError("Provide date or from_date")
    return (await historical_fetch(q,category,target_from,to_date or target_from))[:limit]

async def refresh_loop():
    while True:
        try:
            if cache_stale(): await ingest()
        except Exception: pass
        await asyncio.sleep(60)

@APP.on_event("startup")
async def startup_ingest():
    asyncio.create_task(ingest())
    asyncio.create_task(refresh_loop())

if __name__=="__main__":
    import uvicorn
    uvicorn.run(APP,host="0.0.0.0",port=int(os.getenv("PORT","8000")))
