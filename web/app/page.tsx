"use client";
import {useEffect,useMemo,useState} from "react";

type Item={id:string;source:string;title:string;url:string;published_at?:string|null;category:string;summary:string;exam_relevance:string[];verification:string};
const cats=["All","National","Polity","Economy","Science & Technology","Environment","Defence","International","Sports","Telangana"];
const fallback=[{id:"starter",source:"System",title:"Connect the live API to populate today's verified edition",url:"#",published_at:null,category:"National",summary:"The production UI is ready. When the API is deployed, source-backed articles appear here automatically.",exam_relevance:["UPSC","TGPSC"],verification:"not-live"}];

export default function Home(){
 const [items,setItems]=useState<Item[]>([]),[cat,setCat]=useState("All"),[q,setQ]=useState(""),[status,setStatus]=useState("Connecting…"),[dark,setDark]=useState(false);
 const load=async()=>{
  try{const r=await fetch(`/api/current-affairs?limit=100`,{cache:"no-store"});if(!r.ok)throw new Error();const x=await r.json();setItems(x);setStatus(`Live source feed · ${x.length} items`)}
  catch{setItems(fallback);setStatus("API not connected · live feed waiting for deployment")}
 };
 useEffect(()=>{load()},[]);
 const shown=useMemo(()=>items.filter(x=>(cat==="All"||x.category===cat)&&(!q||x.title.toLowerCase().includes(q.toLowerCase()))),[items,cat,q]);
 return <main className={dark?"app dark":"app"}>
  <header className="top"><div className="bar"><div className="logo">EI</div><div className="brand"><b>Exam Intelligence</b><small>Current Affairs · Exams · AI Tutor</small></div><div className="actions"><button onClick={()=>setDark(v=>!v)}>◐</button><button onClick={load}>↻</button></div></div></header>
  <section className="hero"><div className="eyebrow">Daily Command Center</div><h1>Good morning, aspirant 👋</h1><p>Live-source current affairs → verification → exam relevance → practice.</p><p style={{fontSize:11}}>● {status}</p><div className="stats"><div className="stat"><b>{shown.length}</b><span>Visible stories</span></div><div className="stat"><b>{items.filter(x=>x.exam_relevance.includes("UPSC")).length}</b><span>UPSC tagged</span></div><div className="stat"><b>{items.filter(x=>x.category==="Telangana").length}</b><span>Telangana</span></div><div className="stat"><b>Live</b><span>API mode</span></div></div></section>
  <div className="notice">No fabricated “today” news is shown. If the API is unavailable, the app explicitly tells you instead of pretending sample content is live.</div>
  <section className="section"><div className="head"><h2>Today's edition</h2><button onClick={load}>Refresh</button></div><input className="search" value={q} onChange={e=>setQ(e.target.value)} placeholder="Search current affairs…"/><div className="chips" style={{marginTop:9}}>{cats.map(c=><button className={cat===c?"chip active":"chip"} key={c} onClick={()=>setCat(c)}>{c}</button>)}</div></section>
  <section className="section"><div className="head"><h2>Current affairs</h2><span style={{fontSize:10,color:"#667085"}}>{shown.length} results</span></div><div className="grid">{shown.map((x,i)=><article className="card" key={x.id}><span className="tag">{x.category}</span><h3>{x.title}</h3><p>{x.summary}</p><div className="meta">{x.source} · {x.verification} · {x.exam_relevance.join(" · ")}</div>{x.url&&x.url!="#"&&<a className="btn full" href={x.url} target="_blank" rel="noreferrer">Open source</a>}</article>)}</div></section>
  <section className="section"><div className="head"><h2>Learning</h2></div><div className="grid"><button className="learn"><div className="ico">📝</div><div><b>Prelims Practice</b><small>Grounded questions</small></div><i>→</i></button><button className="learn"><div className="ico">🤖</div><div><b>AI Tutor</b><small>Teach → check → retest</small></div><i>→</i></button><button className="learn"><div className="ico">📚</div><div><b>Archive</b><small>Year → month → date</small></div><i>→</i></button><button className="learn"><div className="ico">📊</div><div><b>Progress</b><small>Mastery & readiness</small></div><i>→</i></button></div></section>
  <nav className="bottom"><button className="active">⌂<span>Home</span></button><button>📰<span>News</span></button><button>📝<span>Practice</span></button><button>🤖<span>Tutor</span></button><button>📚<span>Archive</span></button></nav>
 </main>
}
