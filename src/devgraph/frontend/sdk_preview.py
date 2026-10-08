"""Opt-in historical compatibility preview; new clients use generic Wallet transport."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'wasm-unsafe-eval' chrome-extension:; "
        "style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

HTML = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Devgraph SDK preview</title><link rel="stylesheet" href="./style.css">
<main><p class="eyebrow">DEVGRAPH · HISTORICAL COMPATIBILITY PREVIEW</p>
<h1>Legacy SDK compatibility fixture</h1>
<p>Merged Wallet removed this bridge. Use the Kanban page for generic Wallet setup.
This opt-in page is retained only for historical compatibility testing.</p>
<p>The installed Wallet approves access and signs named operations.
Approving this local origin trusts all code served from it.</p>
<div class="actions"><button id="connect">Connect Wallet</button>
<button id="read-access" disabled>Approve read access</button></div>
<form id="work"><label>Kind<select id="kind"><option>Issue</option><option>Task</option>
<option>Project</option><option>Initiative</option><option>Proposal</option></select></label>
<label>Work ID<input id="work-id" required value="sdk-example"></label>
<label>Title<input id="title" required value="Created with the web SDK"></label>
<div class="actions"><button id="read" type="button" disabled>Read Work</button>
<button id="create" type="submit" disabled>Prepare &amp; confirm create</button>
<button id="retry" type="button" disabled>Confirm same-key retry</button></div></form>
<p id="status" role="status">Install the matched preview extension and native host first.</p>
<pre id="result" aria-label="Result"></pre>
<p class="note">An uncertain result may already have committed. Keep this page open and
use the same-key retry; a duplicate returns the original record
with no historical Work snapshot. If the connection is lost, reconnect Wallet first.</p>
</main><script type="module" src="./app.js"></script></html>"""

CSS = """body{margin:0;
background:#111512;
color:#e9eee9;
font:16px/1.55 system-ui,sans-serif}
main{max-width:760px;
margin:60px auto;
padding:0 24px}h1{font-size:42px;
line-height:1.1}
.eyebrow{font-size:12px;
letter-spacing:.16em;
color:#9dccaa}.actions{display:flex;
gap:12px;
flex-wrap:wrap;
margin:20px 0}
form{border-top:1px solid #3a493d;
margin-top:32px;
padding-top:24px}label{display:block;
margin:16px 0}
input,select{display:block;
box-sizing:border-box;
width:100%;
padding:10px;
background:#1e2720;
color:inherit;
border:1px solid #4f6554;
border-radius:5px}
button{padding:11px 16px;
border:0;
border-radius:5px;
background:#b5d8b8;
color:#142218;
font-weight:600;
cursor:pointer}
button:disabled{opacity:.4;
cursor:default}pre{padding:20px;
background:#1b211c;
white-space:pre-wrap;
overflow-wrap:anywhere;
border-radius:6px}
.note{font-size:13px;
color:#acb8af}#status{color:#b5d8b8}"""

JS = """import {initialize,
connectCastalia,
exportJson} from './pkg/index.js';

const byId=id=>document.getElementById(id);

let runtime,
connection,
client,
read_context,
prepared,
busy=false;

const report=value=>{byId('result').textContent=exportJson(value)};

async function run(action){if(busy)return;
busy=true;
try{await action()}
catch(error){byId('status').textContent=error.code||'operation_failed'}
finally{busy=false}}
byId('connect').onclick=()=>run(async()=>{runtime??=await initialize();
read_context=undefined;
byId('read').disabled=true;
if(connection){await connection.reconnect()}
else{connection=await connectCastalia();
client=runtime.createClient({connection})}
byId('connect').textContent='Reconnect Wallet';
byId('status').textContent='Wallet connected';
byId('read-access').disabled=false;
byId('create').disabled=false});

byId('read-access').onclick=()=>run(async()=>{read_context=await connection.requestReadAccess();
byId('read').disabled=false;
byId('status').textContent='Read access approved'});

byId('read').onclick=()=>run(async()=>{report(await client.getWork(byId('kind').value,
byId('work-id').value,
{read_context}));
byId('status').textContent='Current Work state'});

async function execute(){const attempt=await prepared.authorize();
const result=await attempt.execute();
report(result);
byId('status').textContent=result.kind;
byId('retry').disabled=result.kind!=='outcome_unknown'}
byId('work').onsubmit=event=>{event.preventDefault();
run(async()=>{prepared?.dispose();
const id=byId('work-id').value;
prepared=client.prepare({schema:'devgraph.work-request.v1',
operation:'create',
kind:byId('kind').value,
id,
expected_version:null,
payload:{id,
title:byId('title').value}},
{idempotency_key:crypto.randomUUID()});
await execute()})};

byId('retry').onclick=()=>run(execute);

addEventListener('pagehide',
()=>{runtime?.dispose();
void connection?.dispose()});

"""


def register_sdk_preview(app: FastAPI, package_dir: str | Path) -> None:
    """Serve only matched runtime assets from an explicit build directory."""
    root = Path(package_dir).resolve(strict=True)
    assets = {
        "index.js": (root / "index.js", "text/javascript"),
        "internal/devgraph_web.js": (root / "internal/devgraph_web.js", "text/javascript"),
        "internal/devgraph_web_factory.js": (
            root / "internal/devgraph_web_factory.js", "text/javascript"
        ),
        "internal/devgraph_web_bg.wasm": (
            root / "internal/devgraph_web_bg.wasm", "application/wasm"
        ),
    }
    for path, _ in assets.values():
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("sdk_preview_artifact_missing")

    @app.get("/sdk-preview/", include_in_schema=False)
    def preview():
        return HTMLResponse(HTML, headers=HEADERS)

    @app.get("/sdk-preview/app.js", include_in_schema=False)
    def script():
        return Response(JS, media_type="text/javascript", headers=HEADERS)

    @app.get("/sdk-preview/style.css", include_in_schema=False)
    def style():
        return Response(CSS, media_type="text/css", headers=HEADERS)

    @app.get("/sdk-preview/pkg/{asset:path}", include_in_schema=False)
    def asset(asset: str):
        selected = assets.get(asset)
        if selected is None:
            raise HTTPException(status_code=404)
        path, media_type = selected
        return FileResponse(path, media_type=media_type, headers=HEADERS)
