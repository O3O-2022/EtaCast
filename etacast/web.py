"""브라우저 진행판 — 표준 라이브러리 HTTP 서버 하나. 페이지 · JSON API를 같이 낸다.

  GET  /                 진행판 페이지 (?lang=ko|en &theme=dark|light|contrast &accent=%23f2a33a)
  GET  /api/status       전체 상태(JSON)
  POST /api/pause        갱신 멈춤      POST /api/resume   다시 갱신
  POST /api/interval?sec=2   갱신 주기(0.5 · 1 · 2 · 5 · 10 · 아무 양수)
"""
import http.server
import json
import urllib.parse

from . import __version__

PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0f1318;--panel:#171c23;--line:#26303b;--fg:#e8edf2;--dim:#8a99a7;--acc:#f2a33a;--ok:#5fc48a;--bad:#e5615b;--r:6px}
:root[data-theme="light"]{--bg:#f4f6f8;--panel:#fff;--line:#d8dee5;--fg:#1d2630;--dim:#5b6878;--ok:#1f8a58;--bad:#c0392b}
:root[data-theme="contrast"]{--bg:#000;--panel:#000;--line:#fff;--fg:#fff;--dim:#e0e0e0;--ok:#00ff88;--bad:#ff5555}
@media (prefers-color-scheme: light){:root:not([data-theme]){--bg:#f4f6f8;--panel:#fff;--line:#d8dee5;--fg:#1d2630;--dim:#5b6878}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,"Noto Sans KR","Malgun Gothic",sans-serif}
main{max-width:920px;margin:0 auto;padding:20px 16px 40px}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:16px}
h1{font-size:22px;margin:0}
.ctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
button,select{font:inherit;color:var(--fg);background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:6px 10px;cursor:pointer}
button:focus-visible,select:focus-visible{outline:2px solid var(--acc)}
.card{background:var(--panel);border-top:3px solid var(--acc);border-radius:var(--r);padding:16px 18px;margin-bottom:16px}
.card.done{border-top-color:var(--ok)} .card.fail{border-top-color:var(--bad)}
.top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.name{font-weight:700;font-size:17px}.big{font-size:32px;font-weight:800;font-variant-numeric:tabular-nums}
.dim{color:var(--dim)} .bad{color:var(--bad)} .ok{color:var(--ok)}
.bar{height:12px;background:var(--line);border-radius:6px;overflow:hidden;margin-top:6px}
.fill{height:100%;background:var(--acc);transition:width .6s}
.done .fill{background:var(--ok)} .fail .fill{background:var(--bad)}
.stage{margin-top:12px}.stage.wait{opacity:.55}
.row{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:14px}
details{margin-top:12px}summary{cursor:pointer;color:var(--dim)}
pre{white-space:pre-wrap;word-break:break-all;font-size:12px;background:var(--bg);padding:8px;border-radius:var(--r);max-height:220px;overflow:auto;margin:6px 0 0}
@media (max-width:480px){.big{font-size:26px}main{padding:14px 12px}}
</style></head>
<body><main>
<header>
  <div><h1 id="title">__TITLE__</h1><div class="dim" id="sub"></div></div>
  <div class="ctl">
    <select id="iv" aria-label="interval"></select>
    <button id="pp"></button>
    <button id="nt"></button>
  </div>
</header>
<div id="jobs"></div>
<div class="dim" id="empty" style="display:none"></div>
<script>
const T={
ko:{left:"남은 시간",measuring:"재는 중",wait:"대기",done:"끝",fail:"실패",run:"진행 중",
    pause:"멈춤",resume:"다시 갱신",notify:"알림 켜기",notifyOn:"알림 켜짐",sec:"갱신",
    stages:"단계",diag:"오류 · 경고",none:"아직 로그가 없다",ended:"끝남",elapsed:"지난 시간",
    wait_pre:"시작 전",exit:"종료 코드"},
en:{left:"Time left",measuring:"measuring",wait:"waiting",done:"done",fail:"failed",run:"running",
    pause:"Pause",resume:"Resume",notify:"Enable alerts",notifyOn:"Alerts on",sec:"refresh",
    stages:"Stages",diag:"Errors · warnings",none:"No log yet",ended:"Finished",elapsed:"Elapsed",
    wait_pre:"not started",exit:"exit code"}
};
const q=new URLSearchParams(location.search);
const lang=q.get("lang")==="en"?"en":(q.get("lang")==="ko"?"ko":((navigator.language||"ko").slice(0,2)==="en"?"en":"ko"));
const L=T[lang]||T.ko;
const theme=q.get("theme");
if(theme) document.documentElement.dataset.theme=theme;
const accent=q.get("accent");
if(accent) document.documentElement.style.setProperty("--acc",accent);
const SOUND=q.get("sound")==="1";
const OPTIONS=[0.5,1,2,5,10];
let interval=null, paused=false, lastEvent=0, notifyOn=false;
const iv=document.getElementById("iv");
OPTIONS.forEach(s=>{const o=document.createElement("option");o.value=s;o.textContent=s+"s";iv.appendChild(o);});
iv.onchange=()=>fetch("/api/interval?sec="+iv.value,{method:"POST"});
const pp=document.getElementById("pp");
pp.onclick=()=>fetch(paused?"/api/resume":"/api/pause",{method:"POST"});
const nt=document.getElementById("nt");
nt.textContent=L.notify;
nt.onclick=async()=>{if("Notification" in window){const r=await Notification.requestPermission();notifyOn=r==="granted";nt.textContent=notifyOn?L.notifyOn:L.notify;}};
function fmt(s){if(s==null)return L.measuring;s=Math.round(s);if(s<60)return s+(lang==="en"?"s":"초");const m=Math.floor(s/60),r=s%60;return lang==="en"?m+"m "+r+"s":m+"분 "+r+"초";}
function beep(){if(!SOUND)return;try{const c=new AudioContext(),o=c.createOscillator(),g=c.createGain();o.frequency.value=880;g.gain.value=0.15;o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+0.25);}catch(e){}}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function card(j){
  const cls=j.status==="done"?"done":(j.status==="fail"?"fail":"");
  const stLabel={run:L.run,done:L.done,fail:L.fail,wait:L.wait}[j.status];
  const pct=Math.round(j.frac*100);
  const left=(j.status==="done"||j.status==="fail")?(j.status==="done"?L.ended:L.fail):fmt(j.total_eta);
  const stages=j.stages.map(s=>{
    const lab=s.state==="done"?L.done:(s.state==="wait"?L.wait:fmt(s.eta)+" "+(lang==="en"?"left":"남음"));
    return `<div class="stage ${s.state}"><div class="row"><span>${esc(s.name)} <span class="dim">${esc(s.detail||"")}</span></span><span class="dim">${lab}</span></div><div class="bar"><div class="fill" style="width:${s.frac*100}%"></div></div></div>`;
  }).join("");
  const diag=(j.diagnostics||[]).length?`<details><summary>${L.diag} (${j.diagnostics.length})</summary><pre>${j.diagnostics.map(esc).join("\n")}</pre></details>`:"";
  return `<section class="card ${cls}"><div class="top"><div><div class="name">${esc(j.name)}</div><div class="dim">${esc(j.profile)} · ${stLabel}${j.exit_code!=null?" · "+L.exit+" "+j.exit_code:""}</div></div><div style="text-align:right"><div class="dim">${L.left}</div><div class="big ${j.status==="fail"?"bad":(j.status==="done"?"ok":"")}">${j.status==="done"||j.status==="fail"?(j.status==="done"?"100%":"!"):pct+"%"}</div><div class="dim">${left}</div></div></div><div class="bar"><div class="fill" style="width:${pct}%"></div></div>${stages?`<div style="margin-top:10px">${stages}</div>`:""}${diag}</section>`;
}
async function tick(){
  try{
    const r=await fetch("/api/status",{cache:"no-store"});const d=await r.json();
    paused=d.paused; pp.textContent=paused?L.resume:L.pause;
    if(document.activeElement!==iv){iv.value=String(d.interval);}
    if(!d.jobs.length){document.getElementById("jobs").innerHTML="";const e=document.getElementById("empty");e.style.display="";e.textContent=L.none;}
    else{document.getElementById("empty").style.display="none";document.getElementById("jobs").innerHTML=d.jobs.map(card).join("");}
    document.getElementById("sub").textContent=d.now+" · "+(paused?L.pause:(d.interval+"s "+L.sec));
    d.events.forEach(ev=>{if(ev.id>lastEvent){lastEvent=ev.id;beep();if(notifyOn&&"Notification" in window)new Notification("EtaCast",{body:ev.text});}});
    if(d.events.length&&lastEvent===0)lastEvent=d.events[d.events.length-1].id;
  }catch(e){}
  setTimeout(tick,1000);
}
tick();
</script>
</main></body></html>
"""


def make_handler(engine, title):
    page = PAGE.replace("__TITLE__", title.replace("<", "&lt;")).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = f"EtaCast/{__version__}"

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/api/status":
                return self._json(engine.status())
            if u.path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/api/pause":
                engine.pause(True)
            elif u.path == "/api/resume":
                engine.pause(False)
            elif u.path == "/api/interval":
                try:
                    engine.set_interval(urllib.parse.parse_qs(u.query).get("sec", [""])[0])
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            else:
                return self._json({"error": "not found"}, 404)
            self._json({"ok": True, "paused": engine.paused, "interval": engine.interval})

        def log_message(self, *_):
            pass

    return Handler
