"""engine/ui — `mem.py ui`: one self-contained HTML file to browse the whole memory.

The KB is markdown on disk and the terminal viewer (engine/view.py) reads it fine — but "show me
everything that's in there" wants a surface you can SCAN: every note, its compiled prose, the atom
ledger behind it (claim + verbatim evidence + which conversation), conflicts, the gardener's
verdicts, full-text search, and the link graph. This module snapshots all of that into a single
HTML file with the data embedded — no server, no dependencies, works offline, regenerate any time.

Everything here is read-only over cfg paths; the model is never called.
"""
from __future__ import annotations
import json
import re

from engine import merge as M
from engine import notes as N


# ── data collection (read-only) ──────────────────────────────────────────────
def _source_paths(cfg):
    """source id -> raw transcript path (best-effort, from the inbox archive)."""
    done = cfg.state_dir / "inbox" / "done.jsonl"
    out = {}
    if done.exists():
        for line in done.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("source") and rec.get("abs"):
                    out[rec["source"]] = rec["abs"]
            except Exception:
                continue
    return out


def collect(cfg):
    notes, ledgers = [], {}
    for p in sorted(cfg.knowledge_dir.glob("*.md")):
        if p.name in ("index.md", "README.md"):
            continue
        slug = p.stem
        fields, body = M.parse_note(p.read_text(encoding="utf-8", errors="ignore"))
        ty = M.fget(fields, "type")
        up = M.fget(fields, "updated")
        cf = M.fget(fields, "conflicts")
        src = M.fget(fields, "sources")
        led = N.load_ledger(cfg, slug)
        ledgers[slug] = [{k: a.get(k) for k in ("claim", "evidence", "source", "date", "type")}
                        for a in led]
        notes.append({
            "slug": slug,
            "type": (ty[1] if ty else "?"),
            "updated": (up[1] if up else ""),
            "conflicts": (str(cf[1]).strip() if cf and str(cf[1]).strip() not in ("[]",) else ""),
            "sources": (src[1] if src and src[0] == "list" else []),
            "body": body.strip(),
            "atoms": len(led),
            "pending": N.pending_count(cfg, slug),
        })
    # shared-source edges for the graph (top-k per node, like the galaxy)
    src_sets = {n["slug"]: set(n["sources"]) for n in notes}
    slugs = [n["slug"] for n in notes]
    cand = []
    for i in range(len(slugs)):
        for j in range(i + 1, len(slugs)):
            ov = len(src_sets[slugs[i]] & src_sets[slugs[j]])
            if ov >= 2:
                cand.append((ov, i, j))
    cand.sort(reverse=True)
    deg, edges = {}, []
    for ov, a, b in cand:
        if deg.get(a, 0) < 4 and deg.get(b, 0) < 4:
            edges.append([a, b])
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
    wiki = {n["slug"] for n in notes}
    for i, n in enumerate(notes):
        for m in re.findall(r"\[\[([a-z0-9-]+)\]\]", n["body"]):
            if m in wiki and m != n["slug"]:
                e = sorted((i, slugs.index(m)))
                if e not in edges:
                    edges.append(e)

    from engine.garden import _judged
    unrouted = M.count_unrouted(cfg)
    inbox = sum(1 for _ in cfg.inbox.open()) if cfg.inbox.exists() else 0
    return {
        "notes": notes, "ledgers": ledgers, "edges": edges,
        "sourcePaths": _source_paths(cfg),
        "garden": _judged(cfg),
        "stats": {"notes": len(notes),
                  "atoms": sum(n["atoms"] for n in notes),
                  "pending": sum(n["pending"] for n in notes),
                  "unrouted": unrouted, "inbox": inbox},
    }


def build(cfg, out_path=None):
    data = collect(cfg)
    out = out_path or (cfg.state_dir / "ui.html")
    html = _TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False)
                             .replace("</", "<\\/"))  # never let payload close the script tag
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(out)
    return out


# ── template: the whole SPA, data embedded ───────────────────────────────────
_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>memory</title>
<style>
:root{--bg:#0e1013;--panel:#14171c;--panel2:#191d23;--line:#262b33;--ink:#e8ebf0;
 --ink2:#9aa3b2;--dim:#5c6472;--acc:#37d276;--acc2:#7db4f5;--warn:#e0a53c;
 --chip:#1f242c;--mono:"SF Mono",Menlo,Monaco,monospace}
@media (prefers-color-scheme: light){:root{--bg:#f6f7f8;--panel:#ffffff;--panel2:#f1f3f5;
 --line:#e2e5ea;--ink:#15181d;--ink2:#5a6372;--dim:#9aa3b2;--chip:#eef0f3}}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;height:100vh;overflow:hidden}
a{color:var(--acc2);text-decoration:none} a:hover{text-decoration:underline}
#app{display:grid;grid-template-columns:340px 1fr;grid-template-rows:52px 1fr;height:100vh}
header{grid-column:1/3;display:flex;align-items:center;gap:18px;padding:0 18px;
 border-bottom:1px solid var(--line);background:var(--panel)}
header .logo{font-weight:700;letter-spacing:.06em}
header .logo i{color:var(--acc);font-style:normal}
header .stats{display:flex;gap:14px;color:var(--ink2);font-size:12.5px;font-family:var(--mono)}
header .stats b{color:var(--ink);font-weight:600}
header .tabs{margin-left:auto;display:flex;gap:4px}
header .tabs button{background:none;border:1px solid transparent;color:var(--ink2);padding:6px 12px;
 border-radius:8px;cursor:pointer;font-size:13px}
header .tabs button.on{background:var(--chip);color:var(--ink);border-color:var(--line)}
#side{border-right:1px solid var(--line);background:var(--panel);display:flex;flex-direction:column;min-height:0}
#q{margin:12px;padding:9px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel2);
 color:var(--ink);font-size:13.5px;outline:none}
#q:focus{border-color:var(--acc)}
#chips{display:flex;flex-wrap:wrap;gap:6px;padding:0 12px 10px}
#chips span{background:var(--chip);border:1px solid var(--line);border-radius:99px;padding:2px 10px;
 font-size:11.5px;color:var(--ink2);cursor:pointer;user-select:none}
#chips span.on{color:var(--ink);border-color:var(--acc);background:color-mix(in srgb,var(--acc) 12%,transparent)}
#list{overflow-y:auto;flex:1;min-height:0}
.item{padding:9px 14px;border-bottom:1px solid var(--line);cursor:pointer}
.item:hover{background:var(--panel2)}
.item.on{background:var(--panel2);box-shadow:inset 3px 0 0 var(--acc)}
.item .t{font-size:13.5px;font-weight:600}
.item .m{color:var(--dim);font-size:11.5px;font-family:var(--mono);margin-top:2px;display:flex;gap:10px}
.item .m .ty{color:var(--acc2)}
#main{overflow-y:auto;min-height:0;background:var(--bg)}
.pad{padding:26px 34px;max-width:980px}
h1.note{font-size:22px;margin-bottom:4px}
.meta{color:var(--ink2);font-size:12.5px;font-family:var(--mono);margin-bottom:18px;display:flex;gap:16px;flex-wrap:wrap}
.meta .ty{color:var(--acc2)} .meta .pend{color:var(--warn)}
.vtabs{display:flex;gap:6px;margin:0 0 16px}
.vtabs button{background:var(--chip);border:1px solid var(--line);color:var(--ink2);
 padding:5px 13px;border-radius:8px;cursor:pointer;font-size:12.5px}
.vtabs button.on{color:var(--ink);border-color:var(--acc)}
.prose{font-size:14.5px}
.prose h1,.prose h2,.prose h3{margin:20px 0 8px;line-height:1.3}
.prose h1{font-size:19px}.prose h2{font-size:16px}.prose h3{font-size:14.5px}
.prose p{margin:8px 0}
.prose ul{margin:8px 0 8px 22px}
.prose li{margin:3px 0}
.prose code{font-family:var(--mono);font-size:12.5px;background:var(--chip);
 border:1px solid var(--line);border-radius:5px;padding:1px 5px}
.prose strong{color:var(--ink)}
.prose .wl{color:var(--acc);cursor:pointer}
.atom{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px;background:var(--panel)}
.atom .c{font-size:13.5px}
.atom .e{color:var(--ink2);font-size:12.5px;margin-top:6px;padding-left:10px;
 border-left:2px solid var(--acc);font-style:italic}
.atom .s{color:var(--dim);font-size:11px;font-family:var(--mono);margin-top:6px;display:flex;gap:12px}
.conf{border:1px solid color-mix(in srgb,var(--warn) 40%,transparent);border-radius:10px;
 padding:12px 14px;background:color-mix(in srgb,var(--warn) 6%,transparent);
 font-size:12.5px;font-family:var(--mono);white-space:pre-wrap;color:var(--ink2)}
.srcrow{font-family:var(--mono);font-size:12px;padding:7px 10px;border-bottom:1px solid var(--line);
 display:flex;gap:12px;align-items:baseline}
.srcrow .id{color:var(--acc2);white-space:nowrap}
.srcrow .p{color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
canvas#graph{width:100%;height:calc(100vh - 52px);display:block;cursor:grab}
.gv{display:none} .gv.on{display:block}
table.gard{border-collapse:collapse;width:100%;font-size:13px}
table.gard th,table.gard td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line)}
table.gard th{color:var(--ink2);font-weight:600;font-size:12px}
.v-merged{color:var(--acc)} .v-keep{color:var(--ink2)}
.empty{color:var(--dim);padding:40px;text-align:center}
mark{background:color-mix(in srgb,var(--acc) 30%,transparent);color:inherit;border-radius:3px;padding:0 1px}
</style></head><body>
<div id="app">
<header>
  <div class="logo"><i>●</i> memory</div>
  <div class="stats" id="stats"></div>
  <div class="tabs">
    <button data-tab="notes" class="on">Notes</button>
    <button data-tab="graph">Graph</button>
    <button data-tab="garden">Garden</button>
  </div>
</header>
<div id="side">
  <input id="q" placeholder="search notes and atoms…" autocomplete="off">
  <div id="chips"></div>
  <div id="list"></div>
</div>
<div id="main">
  <div class="gv on" id="v-notes"><div class="pad" id="note"><div class="empty">pick a note on the left — or search</div></div></div>
  <div class="gv" id="v-graph"><canvas id="graph"></canvas></div>
  <div class="gv" id="v-garden"><div class="pad" id="garden"></div></div>
</div>
</div>
<script>
const D = __DATA__;
const N = D.notes, L = D.ledgers;
const bySlug = {}; N.forEach((n,i)=>bySlug[n.slug]=i);
const $ = s => document.querySelector(s);

// header stats
$("#stats").innerHTML = `<span><b>${N.length}</b> notes</span>`+
 `<span><b>${D.stats.atoms.toLocaleString("en")}</b> facts</span>`+
 `<span><b>${D.stats.pending}</b> awaiting compaction</span>`+
 `<span><b>${D.stats.unrouted}</b> backlog</span>`;

// type chips
const typeCounts = {};
N.forEach(n=>typeCounts[n.type]=(typeCounts[n.type]||0)+1);
let activeTypes = new Set();
const chipbox = $("#chips");
Object.entries(typeCounts).sort((a,b)=>b[1]-a[1]).forEach(([t,c])=>{
  const s=document.createElement("span");
  s.textContent=`${t} ${c}`;
  s.onclick=()=>{s.classList.toggle("on");
    s.classList.contains("on")?activeTypes.add(t):activeTypes.delete(t);renderList();};
  chipbox.appendChild(s);
});

// search + list
let query = "";
$("#q").addEventListener("input", e=>{query=e.target.value.trim().toLowerCase();renderList();});
function matches(n){
  if(activeTypes.size && !activeTypes.has(n.type)) return 0;
  if(!query) return 1;
  const q=query;
  if(n.slug.toLowerCase().includes(q)) return 3;
  if(n.body.toLowerCase().includes(q)) return 2;
  if((L[n.slug]||[]).some(a=>(a.claim||"").toLowerCase().includes(q))) return 1.5;
  return 0;
}
let current = null;
function renderList(){
  const scored=N.map(n=>[matches(n),n]).filter(x=>x[0]>0);
  scored.sort((a,b)=> b[0]-a[0] || (b[1].updated||"").localeCompare(a[1].updated||""));
  $("#list").innerHTML = scored.map(([,n])=>
    `<div class="item ${n.slug===current?'on':''}" data-s="${n.slug}">
      <div class="t">${esc(n.slug)}</div>
      <div class="m"><span class="ty">${n.type}</span><span>${n.atoms} facts</span>`+
      (n.pending?`<span style="color:var(--warn)">${n.pending} pending</span>`:"")+
      `<span>${n.updated||""}</span></div></div>`).join("") ||
    `<div class="empty">nothing matches</div>`;
  document.querySelectorAll(".item").forEach(el=>el.onclick=()=>openNote(el.dataset.s));
}
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}

// tiny markdown → html (headers, lists, bold, code, wikilinks) + search highlight
function md(t){
  let h = esc(t);
  h = h.replace(/`([^`]+)`/g,"<code>$1</code>");
  h = h.replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>");
  h = h.replace(/\[\[([a-z0-9-]+)\]\]/g,(m,s)=> s in bySlug ? `<span class="wl" data-s="${s}">${s}</span>` : m);
  const lines = h.split("\n"); const out=[]; let ul=false;
  for(const ln of lines){
    const l=ln.trimEnd();
    if(/^### /.test(l)){if(ul){out.push("</ul>");ul=false} out.push("<h3>"+l.slice(4)+"</h3>")}
    else if(/^## /.test(l)){if(ul){out.push("</ul>");ul=false} out.push("<h2>"+l.slice(3)+"</h2>")}
    else if(/^# /.test(l)){if(ul){out.push("</ul>");ul=false} out.push("<h1>"+l.slice(2)+"</h1>")}
    else if(/^[-*] /.test(l)){if(!ul){out.push("<ul>");ul=true} out.push("<li>"+l.slice(2)+"</li>")}
    else if(!l){if(ul){out.push("</ul>");ul=false} out.push("")}
    else out.push("<p>"+l+"</p>");
  }
  if(ul)out.push("</ul>");
  let html=out.join("\n");
  if(query) try{html=html.replace(new RegExp("("+query.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")+")","gi"),"<mark>$1</mark>")}catch(e){}
  return html;
}

let vtab = "prose";
function openNote(slug, vt){
  current = slug; if(vt) vtab = vt;
  document.querySelector('[data-tab="notes"]').click();
  const n = N[bySlug[slug]]; const atoms = L[slug]||[];
  const tabs = `<div class="vtabs">
    <button data-v="prose" class="${vtab==='prose'?'on':''}">Prose</button>
    <button data-v="atoms" class="${vtab==='atoms'?'on':''}">Facts · ${atoms.length}</button>
    <button data-v="src" class="${vtab==='src'?'on':''}">Conversations · ${n.sources.length}</button>
    ${n.conflicts?`<button data-v="conf" class="${vtab==='conf'?'on':''}">Conflicts</button>`:""}
  </div>`;
  let bodyHtml="";
  if(vtab==="prose") bodyHtml = `<div class="prose">${md(n.body)}</div>`;
  else if(vtab==="atoms") bodyHtml = atoms.length ? atoms.map(a=>
    `<div class="atom"><div class="c">${esc(a.claim)}</div>`+
    (a.evidence?`<div class="e">“${esc(a.evidence)}”</div>`:"")+
    `<div class="s"><span>${a.type||""}</span><span>${a.date||""}</span>`+
    (a.source?`<span title="${esc(D.sourcePaths[a.source]||a.source)}">${esc(String(a.source).slice(0,12))}…</span>`:"")+
    `</div></div>`).join("")
    : `<div class="empty">no ledger yet — this note predates the compiled-notes era and hasn't been touched since</div>`;
  else if(vtab==="src") bodyHtml = n.sources.map(s=>
    `<div class="srcrow"><span class="id">${esc(s).slice(0,14)}…</span><span class="p">${esc(D.sourcePaths[s]||"path unknown (session archived)")}</span></div>`).join("")
    || `<div class="empty">no sources recorded</div>`;
  else if(vtab==="conf") bodyHtml = `<div class="conf">${esc(n.conflicts)}</div>`;
  $("#note").innerHTML =
    `<h1 class="note">${esc(slug)}</h1>
     <div class="meta"><span class="ty">${n.type}</span><span>updated ${n.updated||"?"}</span>
       <span>${n.atoms} facts in ledger</span>${n.pending?`<span class="pend">${n.pending} awaiting compaction</span>`:""}</div>
     ${tabs}${bodyHtml}`;
  document.querySelectorAll(".vtabs button").forEach(b=>b.onclick=()=>openNote(slug,b.dataset.v));
  document.querySelectorAll(".wl").forEach(w=>w.onclick=()=>openNote(w.dataset.s,"prose"));
  renderList();
  $("#main").scrollTop = 0;
  history.replaceState(null,"","#"+slug+"/"+vtab);
}

// tabs
document.querySelectorAll("header .tabs button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("header .tabs button").forEach(x=>x.classList.toggle("on",x===b));
  document.querySelectorAll(".gv").forEach(v=>v.classList.remove("on"));
  $("#v-"+b.dataset.tab).classList.add("on");
  if(b.dataset.tab==="graph") initGraph();
  if(b.dataset.tab==="garden") renderGarden();
});

// garden tab
function renderGarden(){
  const g = D.garden||{};
  const rows = Object.entries(g).sort();
  $("#garden").innerHTML = `<h1 class="note">Gardener verdicts</h1>
    <div class="meta"><span>${rows.filter(([,v])=>v.verdict==="merged").length} families merged</span>
    <span>${rows.filter(([,v])=>v.verdict==="keep").length} kept as distinct topics</span></div>
    <table class="gard"><tr><th>family</th><th>verdict</th><th>members at judgment</th><th>when</th></tr>`+
    rows.map(([k,v])=>`<tr><td>${esc(k)}</td><td class="v-${v.verdict}">${v.verdict}</td>
      <td>${(v.members||[]).map(m=> m in bySlug?`<span class="wl" data-s="${m}">${m}</span>`:esc(m)).join(", ")}</td>
      <td style="font-family:var(--mono);font-size:12px">${v.at||""}</td></tr>`).join("")+"</table>";
  document.querySelectorAll("#garden .wl").forEach(w=>w.onclick=()=>openNote(w.dataset.s,"prose"));
}

// graph tab: force layout precomputed cheaply here (few hundred nodes)
let gInit=false;
function initGraph(){
  if(gInit) return; gInit=true;
  const cv=$("#graph"), ctx=cv.getContext("2d");
  const DPR=devicePixelRatio||1;
  function size(){cv.width=cv.clientWidth*DPR;cv.height=cv.clientHeight*DPR;}
  size(); addEventListener("resize",size);
  const P=N.map((n,i)=>({i,x:Math.cos(i*2.4)*(.3+.6*Math.random()),y:Math.sin(i*2.4)*(.3+.6*Math.random()),
    r:3+Math.sqrt(n.atoms+1)*1.1}));
  for(let it=0;it<260;it++){
    const T=.05*(1-it/260);
    for(let a=0;a<P.length;a++)for(let b=a+1;b<P.length;b++){
      const dx=P[a].x-P[b].x,dy=P[a].y-P[b].y,d2=dx*dx+dy*dy+1e-4;
      if(d2<.2){const f=.002/d2;P[a].x+=dx*f;P[a].y+=dy*f;P[b].x-=dx*f;P[b].y-=dy*f;}}
    for(const[a,b]of D.edges){
      const dx=P[a].x-P[b].x,dy=P[a].y-P[b].y,d=Math.hypot(dx,dy)+1e-6,f=(d-.16)*.05/d;
      P[a].x-=dx*f;P[a].y-=dy*f;P[b].x+=dx*f;P[b].y+=dy*f;}
    for(const p of P){p.x-=p.x*.01;p.y-=p.y*.01;}
  }
  {let cx=0,cy=0;P.forEach(p=>{cx+=p.x;cy+=p.y});cx/=P.length;cy/=P.length;
   let mr=1e-6;P.forEach(p=>{p.x-=cx;p.y-=cy;mr=Math.max(mr,Math.hypot(p.x,p.y))});
   P.forEach(p=>{p.x/=mr;p.y/=mr});}
  const TC={project:"#7db4f5",reference:"#37d276",user:"#b49af8",decision:"#e0a53c",
    concept:"#e87ba4",feedback:"#e66767",claim:"#d95926",entity:"#4fc3c8"};
  let ox=cv.clientWidth/2, oy=cv.clientHeight/2, sc=Math.min(ox,oy)*.85, hov=-1, drag=false,lx=0,ly=0;
  function draw(){
    ctx.setTransform(DPR,0,0,DPR,0,0);
    ctx.clearRect(0,0,cv.clientWidth,cv.clientHeight);
    ctx.strokeStyle="rgba(125,180,245,.12)";ctx.lineWidth=1;
    for(const[a,b]of D.edges){
      const lit=hov>=0&&(a===hov||b===hov);
      ctx.strokeStyle=lit?"rgba(55,210,118,.6)":"rgba(125,180,245,.10)";
      ctx.beginPath();ctx.moveTo(ox+P[a].x*sc,oy+P[a].y*sc);ctx.lineTo(ox+P[b].x*sc,oy+P[b].y*sc);ctx.stroke();}
    for(const p of P){
      const n=N[p.i], X=ox+p.x*sc, Y=oy+p.y*sc, isH=p.i===hov;
      ctx.globalAlpha=hov>=0&&!isH?.45:1;
      ctx.fillStyle=TC[n.type]||"#9aa3b2";
      ctx.beginPath();ctx.arc(X,Y,p.r*(isH?1.4:1),0,7);ctx.fill();
      ctx.globalAlpha=1;
      if(isH||p.r>7){ctx.fillStyle="var(--ink)";ctx.fillStyle=isH?"#e8ebf0":"#9aa3b2";
        ctx.font="11px system-ui";ctx.textAlign="center";ctx.fillText(n.slug,X,Y-p.r-6);}}
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
  cv.addEventListener("mousemove",e=>{
    const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
    if(drag){ox+=mx-lx;oy+=my-ly;lx=mx;ly=my;}
    hov=-1;let bd=18;
    for(const p of P){const d=Math.hypot(ox+p.x*sc-mx,oy+p.y*sc-my)-p.r;if(d<bd){bd=d;hov=p.i}}
    cv.style.cursor=drag?"grabbing":(hov>=0?"pointer":"grab");});
  cv.addEventListener("mousedown",e=>{drag=true;const r=cv.getBoundingClientRect();lx=e.clientX-r.left;ly=e.clientY-r.top;});
  addEventListener("mouseup",()=>drag=false);
  cv.addEventListener("click",()=>{if(hov>=0&&!drag)openNote(N[hov].slug,"prose")});
  cv.addEventListener("wheel",e=>{e.preventDefault();
    const f=Math.exp(-e.deltaY*.0013),ns=Math.min(Math.max(sc*f,60),4000);
    const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;
    ox=mx+(ox-mx)*ns/sc;oy=my+(oy-my)*ns/sc;sc=ns;},{passive:false});
}

renderList();
// deep-link: #slug, #slug/tab, or #!graph / #!garden
{const h=decodeURIComponent(location.hash.slice(1));
 if(h.startsWith("!")) document.querySelector(`[data-tab="${h.slice(1)}"]`)?.click();
 else if(h){const [s,vt]=h.split("/"); if(s in bySlug) openNote(s, vt||"prose");}}
</script></body></html>"""
