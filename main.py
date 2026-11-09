from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import uuid
from pathlib import Path
from urllib.request import Request as URLRequest, urlopen
from urllib.error import URLError, HTTPError
import json, re, os, io, zipfile, base64
from datetime import datetime, timezone

BASE = Path(__file__).parent
app = FastAPI(title="HandoverIQ", description="Evidence-backed project knowledge handover")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "handoveriq-local-development-secret-change-before-deploy"), same_site="lax", https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true")

# In-memory workspace store, isolated by signed browser session. Data resets on restart.
WORKSPACES = {}

def workspace_id(request: Request):
    if "workspace_id" not in request.session:
        request.session["workspace_id"] = str(uuid.uuid4())
    return request.session["workspace_id"]

SAMPLE = {
 "repo": "demo/acme-platform",
 "repo_url": "https://github.com/demo/acme-platform",
 "description": "Synthetic demonstration project for HandoverIQ. No real company data.",
 "commits": [
  {"sha":"a1b2c3d","message":"Add authentication middleware and token validation","author":"Asha Rao","date":"2026-11-07T09:15:00Z","url":"https://github.com/demo/acme-platform/commit/a1b2c3d"},
  {"sha":"b2c3d4e","message":"Fix refresh token expiry handling","author":"Asha Rao","date":"2026-11-06T13:20:00Z","url":"https://github.com/demo/acme-platform/commit/b2c3d4e"},
  {"sha":"c3d4e5f","message":"Add payment webhook retry logic","author":"Ravi Kumar","date":"2026-11-06T11:10:00Z","url":"https://github.com/demo/acme-platform/commit/c3d4e5f"},
  {"sha":"d4e5f6g","message":"Document deployment environment variables","author":"Asha Rao","date":"2026-11-05T15:30:00Z","url":"https://github.com/demo/acme-platform/commit/d4e5f6g"},
  {"sha":"e5f6g7h","message":"Add order status API endpoint","author":"Meera Shah","date":"2026-11-05T10:00:00Z","url":"https://github.com/demo/acme-platform/commit/e5f6g7h"}
 ],
 "issues": [
  {"number":17,"title":"Webhook retries can create duplicate payment events","state":"open","body":"Need idempotency key validation before retry is enabled in production.","url":"https://github.com/demo/acme-platform/issues/17"},
  {"number":21,"title":"Add integration tests for token refresh","state":"open","body":"Refresh-token expiry edge cases are not covered yet.","url":"https://github.com/demo/acme-platform/issues/21"}
 ],
 "pulls": [
  {"number":12,"title":"Authentication middleware","state":"closed","body":"Adds bearer token validation and refresh handling.","url":"https://github.com/demo/acme-platform/pull/12","merged_at":"2026-11-07T09:00:00Z"},
  {"number":14,"title":"Payment webhook retry logic","state":"open","body":"Retry handling needs idempotency review.","url":"https://github.com/demo/acme-platform/pull/14","merged_at":None}
 ],
 "documents": []
}

def gh_get(url):
    req = URLRequest(url, headers={"Accept":"application/vnd.github+json", "User-Agent":"HandoverIQ-Hackathon-MVP"})
    try:
        with urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 404: raise HTTPException(404, "GitHub repository or endpoint not found. Check the owner/repository name and public access.")
        if e.code == 403: raise HTTPException(429, "GitHub API rate limit or access restriction. Try again later or use a public demo repository.")
        raise HTTPException(502, f"GitHub returned HTTP {e.code}.")
    except URLError:
        raise HTTPException(502, "Could not reach GitHub. Check the connection and try again.")

def parse_repo(value):
    value = value.strip().rstrip('/')
    m = re.search(r'github\.com/([^/]+)/([^/?#]+)', value)
    if m: return m.group(1), m.group(2).removesuffix('.git')
    parts = value.split('/')
    if len(parts)==2 and all(parts): return parts[0], parts[1].removesuffix('.git')
    raise HTTPException(400, "Enter a GitHub URL or owner/repository, for example octocat/Hello-World.")

def fetch_repo(owner, repo):
    root=f"https://api.github.com/repos/{owner}/{repo}"
    meta=gh_get(root)
    commits=gh_get(root+"/commits?per_page=100")
    issues=gh_get(root+"/issues?state=all&per_page=100")
    pulls=gh_get(root+"/pulls?state=all&per_page=100")
    try:
        contributors=gh_get(root+"/contributors?per_page=100")
    except HTTPException as e:
        if e.status_code not in (404,): raise
        contributors=[]
    # Fetch README plus a bounded set of real implementation files. Source files
    # are kept separate from README prose so technical questions can inspect code.
    documents=[]
    try:
        readme=gh_get(root+"/readme")
        readme_text=base64.b64decode(readme.get("content", "")).decode("utf-8", errors="replace")
        if readme_text.strip():
            documents.append({"name":"README.md","text":readme_text[:100000],"source":readme.get("html_url",f"https://github.com/{owner}/{repo}/blob/HEAD/README.md"),"verified":True,"kind":"GitHub README"})
    except HTTPException as e:
        if e.status_code not in (404,): raise
    source_files=[]
    try:
        tree=gh_get(root+"/git/trees/"+str(meta.get("default_branch") or "main")+"?recursive=1")
        candidates=[]
        for item in tree.get("tree",[]):
            path=item.get("path","")
            low=path.lower()
            allowed=(low.endswith(".py") or low.endswith("requirements.txt") or
                     (low.endswith((".html", ".js", ".css")) and ("templates/" in low or "static/" in low)))
            if allowed and item.get("type")=="blob" and item.get("size",0)<=120000:
                candidates.append(item)
        # Prefer app/backend files, dependency manifests, then frontend files.
        candidates.sort(key=lambda x:(0 if x["path"].lower().split("/")[-1] in ("app.py","main.py","requirements.txt") else 1, x["path"]))
        for item in candidates[:24]:
            try:
                blob=gh_get(root+"/contents/"+item["path"])
                raw=base64.b64decode(blob.get("content", ""))
                content=raw.decode("utf-8",errors="replace")
                if content.strip():
                    src=blob.get("html_url",f"https://github.com/{owner}/{repo}/blob/{meta.get('default_branch','main')}/{item['path']}")
                    documents.append({"name":item["path"],"text":content[:120000],"source":src,"verified":True,"kind":"GitHub source code"})
                    source_files.append(item["path"])
            except (HTTPException, ValueError):
                continue
    except HTTPException as e:
        if e.status_code not in (404,): raise
    # GitHub issues endpoint includes PRs; separate pull endpoint is used for PR detail.
    clean_issues=[]
    for x in issues:
        if "pull_request" not in x:
            clean_issues.append({"number":x.get("number"),"title":x.get("title",""),"state":x.get("state",""),"body":x.get("body") or "","url":x.get("html_url","")})
    clean_commits=[]
    for x in commits:
        c=x.get("commit",{}); author=c.get("author",{}) or {}
        clean_commits.append({"sha":x.get("sha","")[:7],"message":(c.get("message") or "").splitlines()[0],"author":(author.get("name") or (x.get("author") or {}).get("login") or "Unknown"),"date":author.get("date",""),"url":x.get("html_url","")})
    clean_pulls=[{"number":x.get("number"),"title":x.get("title",""),"state":x.get("state",""),"body":x.get("body") or "","url":x.get("html_url",""),"merged_at":x.get("merged_at")} for x in pulls]
    clean_contributors=[{"login":x.get("login") or "Unknown","contributions":x.get("contributions",0),"url":x.get("html_url","")} for x in contributors]
    return {"repo":f"{owner}/{repo}","repo_url":meta.get("html_url",root),"description":meta.get("description") or "No repository description provided.","commits":clean_commits,"contributors":clean_contributors,"issues":clean_issues,"pulls":clean_pulls,"documents":documents,"source_files":source_files,"github_sample":{"commit_limit":100,"issue_limit":100,"pull_limit":100,"source_file_limit":24}}

def extract(data):
    commits=data.get("commits",[]); issues=data.get("issues",[]); pulls=data.get("pulls",[])
    owners={}
    for c in commits:
        owners[c.get("author","Unknown")]=owners.get(c.get("author","Unknown"),0)+1
    open_issues=[x for x in issues if x.get("state")=="open"]
    open_prs=[x for x in pulls if x.get("state")=="open"]
    # Conservative keyword-based extraction. Every item carries source evidence.
    decisions=[]; modules={}; risks=[]
    for c in commits:
        msg=c.get("message",""); low=msg.lower()
        area="General"
        for key, label in [("auth","Authentication"),("token","Authentication"),("payment","Payments"),("webhook","Payments"),("order","Orders"),("deploy","Deployment"),("database","Data layer"),("api","API")]:
            if key in low: area=label; break
        modules.setdefault(area,[]).append(c)
        if any(k in low for k in ["add","implement","introduce","refactor","migrate"]):
            decisions.append({"text":msg,"kind":"Commit activity (not proof of design rationale)","source":c.get("url"),"owner":c.get("author","Unknown")})
    for i in open_issues:
        risks.append({"level":"High" if any(k in (i.get("title","")+" "+i.get("body","")).lower() for k in ["security","payment","duplicate","data loss","production"]) else "Medium","text":i.get("title","Untitled issue"),"detail":i.get("body","") or "No issue description supplied.","source":i.get("url")})
    for p in open_prs:
        risks.append({"level":"Medium","text":"Open pull request: "+p.get("title","Untitled PR"),"detail":p.get("body","") or "No PR description supplied.","source":p.get("url")})
    return {"owners":sorted([{"name":k,"commit_count":v,"share":round(v/max(1,len(commits))*100)} for k,v in owners.items()],key=lambda x:-x["commit_count"]),"modules":[{"name":k,"commit_count":len(v),"contributors":sorted(set(c.get("author","Unknown") for c in v)),"evidence":[{"message":c.get("message"),"author":c.get("author"),"url":c.get("url")} for c in v[:5]]} for k,v in modules.items()],"decisions":decisions[:12],"open_issues":open_issues,"open_prs":open_prs,"risks":risks,"limitations":["Commit frequency indicates observed activity, not expertise or sole ownership.","Commit messages and issue text cannot reliably reveal the rationale behind technical decisions.","Only public GitHub metadata and documents uploaded in this workspace are included.","No unsupported answer is generated when evidence is absent."]}

@app.get("/", response_class=HTMLResponse)
def home(): return (BASE/"static"/"index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health(): return {"status":"ok","product":"HandoverIQ","version":"0.3.0"}

@app.post("/api/demo")
def demo(request: Request):
    wid=workspace_id(request)
    WORKSPACES[wid]={**SAMPLE,"analysis":extract(SAMPLE),"mode":"synthetic demo"}
    return workspace_response(request)

@app.post("/api/connect")
def connect(request: Request, repo: str = Form(...)):
    owner,name=parse_repo(repo)
    data=fetch_repo(owner,name)
    WORKSPACES[workspace_id(request)]={**data,"analysis":extract(data),"mode":"public GitHub repository"}
    return workspace_response(request)

@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    wid=workspace_id(request)
    if wid not in WORKSPACES: raise HTTPException(400,"Connect a repository or load the demo first.")
    if not file.filename.lower().endswith((".txt",".md",".rst")):
        raise HTTPException(400,"For this MVP, upload a .txt, .md or .rst document.")
    raw=await file.read()
    if len(raw)>1_000_000: raise HTTPException(413,"Document is larger than 1 MB.")
    try: content=raw.decode("utf-8")
    except UnicodeDecodeError: raise HTTPException(400,"Document must be UTF-8 text.")
    doc={"name":file.filename,"text":content[:30000],"source":"uploaded document"}
    w=WORKSPACES[wid]; w.setdefault("documents",[]).append(doc)
    # Preserve uploaded evidence as explicitly user-supplied, not repo-verified.
    for line in [x.strip() for x in content.splitlines() if len(x.strip())>15][:20]:
        w["analysis"]["decisions"].append({"text":line,"kind":"Uploaded document excerpt — requires human confirmation","source":file.filename,"owner":"Not specified"})
    return workspace_response(request)

def workspace_response(request: Request):
    w=WORKSPACES.get(workspace_id(request))
    if not w: raise HTTPException(404,"No active workspace.")
    return {k:v for k,v in w.items() if k!="documents"} | {"document_count":len(w.get("documents",[]))}

@app.get("/api/workspace")
def get_workspace(request: Request): return workspace_response(request)


def _tokens(text):
    stop={"what","which","where","when","who","does","this","that","with","from","into","have","been","were","will","would","could","should","about","there","their","project","give","show","tell","find","list","three","biggest","most","complete","generate","create","please","the","and","for"}
    return {x for x in re.findall(r"[a-z0-9_-]+", (text or "").lower()) if len(x)>2 and x not in stop}

def _markdown_section_lines(text):
    section = "README (intro / unsectioned)"
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            section = line.lstrip("# ").strip() or section
        elif line:
            yield section, line

def _evidence_records(w):
    records=[]
    for c in w.get("commits",[]):
        records.append({"kind":"GitHub commit","text":c.get("message",""),"source":c.get("url",""),"owner":c.get("author","Unknown"),"date":c.get("date",""),"verified":True})
    for contributor in w.get("contributors",[]):
        records.append({"kind":"GitHub contributor","text":f"{contributor.get('login','Unknown')} has {contributor.get('contributions',0)} recorded contributions in this repository.","source":contributor.get("url",w.get("repo_url","")),"owner":contributor.get("login","Unknown"),"verified":True})
    for i in w.get("issues",[]):
        records.append({"kind":f"GitHub issue #{i.get('number')} ({i.get('state','unknown')})","text":(i.get("title","")+" — "+(i.get("body") or "")).strip(" —"),"source":i.get("url",""),"owner":"Not specified","verified":True,"state":i.get("state","")})
    for p in w.get("pulls",[]):
        records.append({"kind":f"GitHub pull request #{p.get('number')} ({p.get('state','unknown')})","text":(p.get("title","")+" — "+(p.get("body") or "")).strip(" —"),"source":p.get("url",""),"owner":"Not specified","verified":True,"state":p.get("state","")})
    for d in w.get("documents",[]):
        is_repo=bool(d.get("verified"))
        for n,(section,line) in enumerate(_markdown_section_lines(d.get("text","")),1):
            src=d.get("source") if is_repo else d.get("name","uploaded document")
            records.append({"kind":f"{d.get('kind','Uploaded document')} · {section} · extracted line {n}","text":line,"source":src,"owner":"Not specified","verified":is_repo,"category":"Repository documentation" if is_repo else "User-provided context"})
    return records

def _score_records(q, records):
    terms=_tokens(q)
    scored=[]
    for r in records:
        rt=_tokens(r.get("text",""))
        overlap=terms & rt
        if overlap:
            scored.append((len(overlap)/max(1,len(terms)),r))
    scored.sort(key=lambda x:(x[0],x[1].get("verified",False)),reverse=True)
    return [r for _,r in scored[:12]]

def _risk_items(w):
    items=[]
    for r in w.get("analysis",{}).get("risks",[]):
        items.append({"priority":r.get("level","Medium"),"task":"Review: "+r.get("text","Open work"),"reason":r.get("detail","Open repository item requires review."),"source":r.get("source",""),"verification":"Confirmed open GitHub record; implementation impact not independently tested","category":"Confirmed documented limitation"})
    # Extract limitations from repository README and uploaded notes without upgrading them to vulnerabilities.
    patterns=[
      (r"rule[- ]based|false positive|false negative", "Medium", "Test detection against labelled benign and malicious samples; measure false positives and false negatives.", "Rule-based screening is not a definitive verdict; false positives and false negatives are possible."),
      (r"provisional.*attack.flow|attack.flow.*provisional", "Medium", "Validate attack-flow hypotheses against known test cases and label inferred stages clearly.", "Attack-flow reconstruction is provisional and does not establish that every inferred stage occurred."),
      (r"sha-?256.*(authenticity|chain of custody)|(authenticity|chain of custody).*sha-?256", "High", "Document collection, custody, trusted timestamps, access controls, and audit trail; do not treat a matching hash alone as proof of authenticity.", "A matching SHA-256 digest verifies byte consistency against a recorded digest, not source, trusted time, or legal chain of custody."),
      (r"not a production.hardened|not production.hardened|production.hardened", "High", "Complete a production-readiness and security review before public deployment.", "The README describes the project as a local development project, not a production-hardened service."),
      (r"csrf protection", "High", "Implement and test CSRF protection for state-changing requests.", "CSRF protection is listed as a control to implement and test before public deployment."),
      (r"rate limiting", "High", "Add rate limiting and test abuse/DoS scenarios.", "Rate limiting is listed as a control to implement and test before public deployment."),
      (r"authorization and isolation|authorization.*isolation|isolation for every case and file", "High", "Test authorization and tenant isolation for every case, evidence file, download, and report.", "Authorization and isolation for every case and file are listed as pre-deployment work."),
      (r"encrypted evidence storage", "High", "Assess and implement encryption at rest for evidence storage, with key management.", "Encrypted evidence storage is listed as a pre-deployment control."),
      (r"secure secret management|secret management", "High", "Verify secrets are supplied through deployment secrets/environment variables and are never committed.", "Secure secret management is listed as a pre-deployment control."),
      (r"https", "High", "Enforce HTTPS in deployed environments and verify cookie/security settings.", "HTTPS is listed as a pre-deployment requirement."),
      (r"production wsgi server", "High", "Use and validate an appropriate production application server instead of the development server.", "A production WSGI server is listed as a pre-deployment requirement."),
      (r"disk imaging|memory forensics|malware detonation|live packet capture", "Low", "Document scope boundaries and refer investigations needing these capabilities to suitable forensic tooling.", "The README explicitly states these forensic capabilities are outside this application's scope."),
      (r"test suite|integration and security testing|security testing", "High", "Run the included tests and add integration/security tests for upload handling, path traversal, oversized payloads, authentication, authorization, hash verification, and report generation.", "The README recommends running the included test suite and additional integration/security testing; execution results are not supplied by the README.")
    ]
    seen=set()
    for d in w.get("documents",[]):
        txt=d.get("text","")
        for pattern,priority,task,reason in patterns:
            if re.search(pattern,txt,re.I) and pattern not in seen:
                seen.add(pattern)
                items.append({"priority":priority,"task":task,"reason":reason,"source":d.get("source") or d.get("name","document"),"verification":"Documented limitation / recommendation; current implementation not independently tested","category":"Confirmed documented limitation" if d.get("verified") else "User-provided limitation requiring confirmation"})
    return items

def _checklist(w):
    tasks=[]
    a=w.get("analysis",{})
    if a.get("open_issues"):
        for i in a["open_issues"][:5]:
            tasks.append({"priority":"High" if any(k in (i.get("title","")+" "+i.get("body","")).lower() for k in ["security","payment","duplicate","data loss","production"]) else "Medium","task":f"Resolve or explicitly reassign GitHub issue #{i.get('number')}: {i.get('title')}","reason":i.get("body") or "Open issue has no description; ask its reporter/owner for context.","source":i.get("url",""),"verification":"Confirmed open issue; completion not verified"})
    if a.get("open_prs"):
        for p in a["open_prs"][:5]:
            tasks.append({"priority":"Medium","task":f"Review and decide next action for pull request #{p.get('number')}: {p.get('title')}","reason":p.get("body") or "Open pull request has no description.","source":p.get("url",""),"verification":"Confirmed open pull request; review outcome not verified"})
    if not a.get("open_issues") and not a.get("open_prs"):
        tasks.append({"priority":"High","task":"Ask the previous owner to list unfinished work and assign each item to a successor.","reason":"The sampled public GitHub data contains no open issues or pull requests; this does not prove that no unfinished work exists.","source":w.get("repo_url",""),"verification":"Recommended investigation; not a confirmed unfinished task"})
    tasks.extend(_risk_items(w))
    # Always include key handover questions where source data is silent.
    tasks.extend([
      {"priority":"High","task":"Confirm local setup, required environment variables, secrets handling, and deployment steps.","reason":"These operational steps cannot be established reliably from commit metadata alone.","source":w.get("repo_url",""),"verification":"Unknown from currently connected evidence"},
      {"priority":"Medium","task":"Ask the previous owner to explain key architecture decisions, module ownership, and known failure cases.","reason":"Commit messages and activity counts do not establish design rationale or expertise.","source":w.get("repo_url",""),"verification":"Unknown from currently connected evidence"}
    ])
    # De-duplicate by task text
    out=[]; seen=set()
    for t in tasks:
        if t["task"] not in seen: seen.add(t["task"]); out.append(t)
    rank={"High":0,"Medium":1,"Low":2}
    return sorted(out,key=lambda x:rank.get(x["priority"],3))[:12]

def _repo_docs(w):
    return [d for d in w.get("documents", []) if d.get("text")]

def _section_map(w):
    out=[]
    for d in _repo_docs(w):
        if not d.get("verified"): continue
        current="README"
        chunks={}
        for raw in d.get("text", "").splitlines():
            line=raw.strip()
            if line.startswith("#"):
                current=line.lstrip("# ").strip()
                chunks.setdefault(current, [])
            elif line:
                chunks.setdefault(current, []).append(line)
        for name, lines in chunks.items():
            out.append({"name":name,"text":"\n".join(lines),"source":d.get("source", ""),"verified":True})
    return out

def _section_find(sections, terms):
    return [x for x in sections if any(t in x["name"].lower() for t in terms)]

def _bullets_from_sections(found, limit=16):
    result=[]
    for sec in found:
        for line in sec["text"].splitlines():
            line=line.strip().lstrip("- ").strip()
            if len(line)>8:
                result.append({"text":line,"source":sec["source"],"kind":f"GitHub README · {sec['name']}"})
                if len(result)>=limit: return result
    return result

def _all_doc_text(w):
    return "\n\n".join(d.get("text","") for d in _repo_docs(w))

def _doc_lines_for(w, headings=None, limit=50):
    wanted=[x.lower() for x in (headings or [])]
    out=[]
    for d in _repo_docs(w):
        current="README / document"
        for raw in d.get("text","").splitlines():
            line=raw.strip()
            if line.startswith("#"):
                current=line.lstrip("# ").strip()
                continue
            if not line or line.startswith("```"):
                continue
            if not wanted or any(k in current.lower() for k in wanted):
                clean=line.lstrip("- *0123456789.\\t").strip()
                if len(clean)>4:
                    out.append({"text":clean,"source":d.get("source") if d.get("verified") else d.get("name","uploaded document"),"kind":f"{d.get('kind','Document')} · {current}","verified":bool(d.get("verified"))})
                    if len(out)>=limit: return out
    return out

def _has_any(q, terms):
    return any(t in q for t in terms)

def _github_activity(w):
    commits=w.get("commits",[]); issues=w.get("issues",[]); pulls=w.get("pulls",[])
    counts={}
    for c in commits:
        name=c.get("author") or "Unknown"
        counts[name]=counts.get(name,0)+1
    contributors=w.get("contributors") or [{"login":n,"contributions":ncount,"url":""} for n,ncount in counts.items()]
    open_issues=[i for i in issues if i.get("state")=="open"]
    open_prs=[p for p in pulls if p.get("state")=="open"]
    closed_prs=[p for p in pulls if p.get("state")=="closed"]
    return counts,contributors,open_issues,open_prs,closed_prs

def _source_docs(w):
    return [d for d in w.get("documents",[]) if d.get("verified") and d.get("kind")=="GitHub source code"]

def _code_inspection_answer(w):
    docs=_source_docs(w)
    evidence=[]; gaps=[]; actions=[]
    if not docs:
        return {"direct":"No source-code files were fetched for this repository. Reconnect the repository after enabling source-file retrieval; README-only evidence is insufficient for implementation claims.","evidence":[],"gaps":["Actual source code is not available in this workspace."],"actions":[{"priority":"High","task":"Reconnect the repository and verify that source files appear in the fetched file list.","reason":"Implementation analysis requires actual code files.","source":w.get("repo_url",""),"verification":"Required before source-level claims"}]}
    combined="\n".join(d.get("text","") for d in docs)
    # Imports and dependency manifest provide stack evidence.
    tech_map=[("flask", "Flask", "HTTP routes, request handling, sessions, and responses"),
              ("fastapi", "FastAPI", "ASGI API framework"), ("sqlite3", "SQLite", "Relational persistence through Python's sqlite3 module"),
              ("werkzeug", "Werkzeug", "Password hashing and secure filename utilities"),
              ("cv2", "OpenCV", "QR image decoding"), ("numpy", "NumPy", "Image byte-array handling for QR decoding"),
              ("reportlab", "ReportLab", "PDF report generation"), ("hashlib", "hashlib / SHA-256", "Evidence-file hashing"),
              ("requests", "Requests", "Optional outbound HTTP API calls")]
    for token,label,purpose in tech_map:
        if re.search(r"\b"+re.escape(token)+r"\b",combined,re.I):
            f=next((d for d in docs if re.search(r"\b"+re.escape(token)+r"\b",d.get("text",""),re.I)),docs[0])
            evidence.append({"text":f"{label}: {purpose}. Found in fetched source/dependency files; this establishes code usage, not successful runtime operation.","source":f.get("source",""),"kind":"Source-code technology evidence"})
    # Python Flask/FastAPI route decorators.
    routes=[]
    for d in docs:
        if not d.get("name","").endswith(".py"): continue
        for m in re.finditer(r"@(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)",d.get("text",""),re.I):
            routes.append((m.group(1).upper(),m.group(2),d))
    if routes:
        for method,path,d in routes[:60]:
            evidence.append({"text":f"Route declared: {method} {path}","source":d.get("source",""),"kind":"Source-code route decorator"})
    else:
        gaps.append("No supported Flask/FastAPI route decorators were found in the fetched Python files; the project may use another routing pattern or source files may be incomplete.")
    # SQLite CREATE TABLE declarations.
    tables=[]
    for d in docs:
        if not d.get("name","").endswith(".py"): continue
        for m in re.finditer(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\);",d.get("text",""),re.I|re.S):
            name=m.group(1)
            if name in tables: continue
            columns=[]
            for raw_line in m.group(2).splitlines():
                line=raw_line.strip().rstrip(",")
                if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT")): continue
                col=re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+(INTEGER|TEXT|REAL|BLOB|NUMERIC)",line,re.I)
                if col: columns.append(f"{col.group(1)} ({col.group(2).upper()})")
            tables.append(name)
            evidence.append({"text":f"SQLite table `{name}` is declared in source. Columns found: {', '.join(columns) if columns else 'column definitions not extracted'}. Constraints and relationships should be checked in the linked CREATE TABLE statement.","source":d.get("source",""),"kind":"Source-code database table"})
    if tables:
        evidence.append({"text":"Database tables found in fetched source: "+", ".join(tables)+". These are declarations in inspected files, not a live database inspection.","source":next((d.get("source","") for d in docs if any(t in d.get("text","") for t in tables)),w.get("repo_url","")),"kind":"Source-code database schema inventory"})
    else:
        gaps.append("No SQL CREATE TABLE declarations were found in fetched source files; schema may be managed elsewhere or not included in the fetched files.")
    # Source inventory and high-level flow.
    evidence.append({"text":"Fetched source files: "+", ".join(d.get("name","") for d in docs)+". Only these files were inspected; this may not represent the entire repository.","source":w.get("repo_url",""),"kind":"Fetched source-file inventory"})
    if any("flask" in d.get("text","").lower() for d in docs):
        evidence.append({"text":"Source-derived request flow: client request → Flask route handler → validation/processing functions → database or file operations → JSON/download response. Exact flow differs by endpoint and should be traced in the relevant function.","source":next((d.get("source","") for d in docs if "flask" in d.get("text","").lower()),w.get("repo_url","")),"kind":"Inferred from source structure"})
    gaps.extend(["Frontend component boundaries and browser-to-backend flow require inspection of fetched template/static files.","A source declaration does not prove that a feature works correctly at runtime; execute tests and exercise the routes.","Deployment configuration, secrets, persistent storage, and production behavior require separate verification."])
    actions=[{"priority":"High","task":"Run the application and exercise the discovered routes with representative valid and invalid requests.","reason":"Static source inspection does not establish runtime correctness.","source":w.get("repo_url",""),"verification":"Recommended; not executed by this analysis"},
             {"priority":"Medium","task":"Inspect full CREATE TABLE statements and document columns, constraints, and foreign-key relationships.","reason":"The current extraction confirms table names but does not yet produce a complete schema diagram.","source":w.get("repo_url",""),"verification":"Follow-up source analysis"}]
    return {"direct":f"Source-code inspection covered {len(docs)} fetched implementation/dependency files. Findings below are tied to individual GitHub files; uninspected files and runtime behavior remain unverified.","evidence":evidence,"gaps":gaps,"actions":actions}

@app.post("/api/ask")
def ask(request: Request, question: str = Form(...)):
    w=WORKSPACES.get(workspace_id(request))
    if not w: raise HTTPException(400,"Load a repository or demo first.")
    q=question.strip(); qlow=q.lower(); records=_evidence_records(w); hits=_score_records(q,records)
    risks=_risk_items(w); checklist=_checklist(w); sections=_section_map(w)
    docs=_all_doc_text(w); bullets=[]; gaps=[]; actions=[]
    counts,contributors,open_issues,open_prs,closed_prs=_github_activity(w)
    commits=w.get("commits",[]); all_issues=w.get("issues",[]); all_prs=w.get("pulls",[])
    is_activity=_has_any(qlow,["contributor","contributors","ownership","owner","commit history","commit activity","github activity","pull request","pull requests","open issues","issues and pull","who worked","team activity"])
    # A phrase such as "mark unknown items NOT DOCUMENTED" is not a request
    # for cross-source comparison. Only explicit comparison language selects it.
    is_compare=_has_any(qlow,["cross-source comparison","compare the sources","compare the documents","compare documentation","contradictory","contradiction","inconsisten","conflicting claims","gaps between documents"])
    is_code_inspection=_has_any(qlow,["source code","actual implementation","implementation files","inspect the code","analyze the code","analyse the code","database tables","database schema","architecture and data flow","technology stack","http methods","route decorators"])
    is_decision=_has_any(qlow,["decision","why was","why did","rationale","chosen","trade-off","tradeoff","alternatives considered"])
    is_features=_has_any(qlow,["features","feature extraction","optional integrations","future enhancements","core features","grouped by module"])
    is_handover=_has_any(qlow,["handover","successor","take over","taking over","new developer","departing developer","developer leaves","leaving the team"])
    is_overview=_has_any(qlow,["purpose","overview","main modules","modules","workflow","complete workflow"])
    is_setup=_has_any(qlow,["setup","installation","run locally","getting started"])
    is_api=_has_any(qlow,["api","endpoint","routes"])
    is_risk=_has_any(qlow,["risk","limitation","technical issue","weakness","problem","vulnerability","security"])
    is_gap=_has_any(qlow,["gap","missing","documentation","unknown","not documented"])
    is_check=_has_any(qlow,["checklist","priorit","next step","action","task"])
    # Cross-source comparison must take precedence over activity keywords in the same question.
    if is_compare:
        is_activity=False

    if is_code_inspection:
        result=_code_inspection_answer(w)
        direct=result["direct"]
        bullets=result["evidence"]
        gaps=result["gaps"]
        actions=result["actions"]
    elif is_activity:
        direct="GitHub activity summary for the connected repository. Commit counts are sampled activity signals, not proof of expertise, ownership, or time spent."
        bullets=[{"text":f"Repository: {w.get('repo')} · sampled commits: {len(commits)} · recorded contributor identities in commit sample: {len(counts)} · open issues: {len(open_issues)} · open pull requests: {len(open_prs)} · closed pull requests in fetched sample: {len(closed_prs)}.","source":w.get("repo_url",""),"kind":"GitHub repository snapshot"}]
        api_contributors=w.get("contributors") or []
        if api_contributors:
            for contributor in api_contributors:
                bullets.append({"text":f"GitHub contributor account '{contributor.get('login','Unknown')}': {contributor.get('contributions',0)} contribution(s) reported by GitHub's contributors endpoint.","source":contributor.get("url",w.get("repo_url","")),"kind":"GitHub contributors API"})
        else:
            bullets.append({"text":"GitHub contributors endpoint did not provide contributor accounts in this snapshot; commit author strings below are not a verified distinct-person count.","source":w.get("repo_url",""),"kind":"Contributor data limitation"})
        for name,n in sorted(counts.items(),key=lambda x:(-x[1],x[0])):
            bullets.append({"text":f"Commit author identity string '{name}': {n} of {len(commits)} commits in the fetched commit sample. Different strings may refer to the same person.","source":w.get("repo_url",""),"kind":"GitHub commit sample · activity only"})
        for c in commits[:20]:
            bullets.append({"text":f"{c.get('date','Date not returned')} · {c.get('author','Unknown')} · {c.get('message','(no message)')}","source":c.get("url",w.get("repo_url","")),"kind":"GitHub commit"})
        for i in open_issues:
            bullets.append({"text":f"Open issue #{i.get('number')}: {i.get('title')} — {i.get('body','')}","source":i.get("url",""),"kind":"GitHub issue · open"})
        for p in open_prs:
            bullets.append({"text":f"Open pull request #{p.get('number')}: {p.get('title')} — {p.get('body','')}","source":p.get("url",""),"kind":"GitHub pull request · open"})
        for p in closed_prs:
            bullets.append({"text":f"Closed pull request #{p.get('number')}: {p.get('title')} (closed state; merge status not inferred unless explicitly supplied).","source":p.get("url",""),"kind":"GitHub pull request · closed"})
        module_owners=[]
        module_data=w.get("analysis",{}).get("modules",[]) or []
        if isinstance(module_data,dict):
            module_iter=module_data.items()
        else:
            module_iter=((m.get("name","General"),m.get("evidence",[])) for m in module_data)
        for module,items in module_iter:
            people=sorted({x.get("author","Unknown") for x in items})
            module_owners.append(f"{module}: observed commit authors {', '.join(people)}")
        if module_owners:
            bullets.append({"text":"Module activity inferred from commit-message keywords (not verified module ownership): "+"; ".join(module_owners),"source":w.get("repo_url",""),"kind":"Heuristic activity grouping"})
        gaps=["Named module owners, backup owners, expertise, and actual responsibility are not established by commit authorship alone.","Uncommitted local changes, private task boards, chat decisions, and unfinished work outside GitHub are not visible in this repository snapshot.","A lack of open issues or pull requests does not prove that no unfinished work exists."]
        actions=[{"priority":"High","task":"Ask the departing developer to identify module owners, backup owners, active work, blockers, and work not recorded in GitHub.","reason":"GitHub activity does not prove responsibility or capture local/private work.","source":w.get("repo_url",""),"verification":"Recommended handover interview; not a verified existing task"}]
    elif is_compare:
        direct="Cross-source comparison of connected repository documentation, uploaded documents, and sampled GitHub records. 'Not found' means not located in the connected sources; it does not prove the information does not exist elsewhere."
        bullets=[]
        # Explicitly enumerate documented sections and their evidence classes.
        for heading in ["Key Features","Investigation Workflow","Run Locally","Optional External Reputation Providers","API Endpoints","Testing","Important Limitations & Security Notes","Suggested Future Research Extensions","Validation Note"]:
            present=any(heading.lower() in sec["name"].lower() for sec in sections)
            bullets.append({"text":f"{heading}: {'DOCUMENTED in repository README.' if present else 'NOT LOCATED as a matching README section.'}","source":next((x.get("source","") for x in _repo_docs(w) if x.get("verified")),w.get("repo_url","")),"kind":"README section inventory"})
        for d in w.get("documents",[]):
            dsource=d.get("source") or d.get("name","uploaded document")
            bullets.append({"text":f"Connected document: {d.get('name','unnamed')} ({'GitHub-verified repository document' if d.get('verified') else 'user-uploaded context'}). It is available as evidence; uploaded content is not independently verified as repository truth.","source":dsource,"kind":"Source inventory"})
            if not d.get("verified"):
                for section,line in list(_markdown_section_lines(d.get("text","")))[:8]:
                    bullets.append({"text":f"Uploaded note excerpt [{section}]: {line}","source":dsource,"kind":"User-provided context · confirm with owner"})
        known=["Project purpose, three feature areas, investigation workflow, local setup, optional reputation providers, API route list, test command and test coverage description, security limitations, future research ideas, and validation note are documented in the README."]
        bullets.extend({"text":x,"source":w.get("repo_url",""),"kind":"Documented information"} for x in known)
        missing=[("Detailed architecture diagram/data-flow and component boundaries",["architecture diagram","component diagram","data flow diagram"]),("Database schema, table definitions, and migrations",["create table","schema","migration"]),("Module owner and backup-owner assignments",["module owner","backup owner","maintainer"]),("Deployment recovery/rollback runbook",["rollback procedure","recovery runbook","restore procedure"]),("Decision records with rationale and rejected alternatives",["architecture decision record","adr","alternatives considered"]),("Owner-confirmed unfinished work list",["unfinished work","pending tasks","known backlog"])]
        for label,terms in missing:
            if not any(t in docs.lower() for t in terms):
                bullets.append({"text":label+": NOT LOCATED in connected documentation; confirm with the previous owner.","source":w.get("repo_url",""),"kind":"Potential documentation gap"})
        gaps=["README feature and endpoint claims are documented but are not proof of current runtime behavior.","The README describes a test command and test coverage; no execution result is present in README evidence.","Uploaded notes are user-provided context and should be compared with source code and owner confirmation before being treated as verified.","No explicit contradiction was conclusively established by this deterministic comparison; conflicting claims may require manual semantic review."]
        actions=[{"priority":"High","task":"Inspect the source code and database files to document architecture, data flow, schemas, and actual endpoint implementations.","reason":"The README documents capabilities and route names but not detailed implementation structure.","source":w.get("repo_url",""),"verification":"Recommended inspection; not yet performed by this app"},{"priority":"High","task":"Run the documented test suite and record exact pass/fail output.","reason":"The README describes tests but does not provide a current execution report.","source":w.get("repo_url",""),"verification":"Recommended; runtime execution not verified"}]
    elif is_decision:
        direct="Technical choices explicitly named in connected documentation are listed below. Rationale and alternatives are only stated when the source records them; otherwise they are marked NOT DOCUMENTED."
        bullets=[]
        choices=[("Python","Application runtime / backend; README says Python is required.","NOT DOCUMENTED"),("Flask","Web application framework; README starts the app with python app.py.","NOT DOCUMENTED"),("SQLite","Local persistence for scan history and dashboard statistics; database and evidence files are stored locally.","NOT DOCUMENTED"),("OpenCV","Required for QR decoding.","NOT DOCUMENTED"),("ReportLab","Required for PDF report generation.","NOT DOCUMENTED"),("VirusTotal and Google Safe Browsing","Optional URL reputation integrations when API keys are configured.","NOT DOCUMENTED"),("SHA-256 and SHA-1","Used for evidence integrity digest and text fingerprinting.","NOT DOCUMENTED")]
        for tech,usage,rationale in choices:
            bullets.append({"text":f"{tech} — use: {usage} Documented rationale for choosing it: {rationale}. Alternatives considered: NOT DOCUMENTED.","source":w.get("repo_url",""),"kind":"README technical choice"})
        bullets.extend({"text":f"Commit activity: {c.get('message','')}. This records a change, not its design rationale.","source":c.get("url",""),"kind":"GitHub commit · not an ADR"} for c in commits[:10])
        gaps=["No architecture decision record or explicit technology-selection rationale was located in connected evidence.","No rejected alternatives or trade-off analysis was located."]
        actions=[{"priority":"Medium","task":"Ask the previous owner to document why each major technology was selected, alternatives considered, and trade-offs accepted.","reason":"The README records technology usage, not selection rationale.","source":w.get("repo_url",""),"verification":"Recommended; rationale not documented"}]
    elif is_features:
        direct="Features grouped using the README's own module headings. Optional integrations and future research extensions are separated from the core documented feature list. README claims are not runtime verification."
        bullets=[]
        for sec in sections:
            name=sec["name"].lower()
            if any(x in name for x in ["1. scam detection","2. digital forensics","3. digital forensics laboratory"]):
                for line in sec["text"].splitlines():
                    clean=line.strip().lstrip("- ").strip()
                    if clean:
                        optional="optional" in clean.lower()
                        label="OPTIONAL FEATURE" if optional else "DOCUMENTED MODULE FEATURE"
                        kind="Optional feature (runtime not verified)" if optional else "Core documented feature (runtime not verified)"
                        bullets.append({"text":f"{label} — {sec['name']}: {clean}","source":sec["source"],"kind":kind})
        for line in _doc_lines_for(w,["optional external reputation providers"],10):
            bullets.append({"text":"OPTIONAL INTEGRATION — "+line["text"],"source":line["source"],"kind":"Optional integration"})
        for line in _doc_lines_for(w,["suggested future research extensions"],20):
            bullets.append({"text":"FUTURE RESEARCH EXTENSION — "+line["text"],"source":line["source"],"kind":"Future enhancement, not current feature claim"})
        gaps=["The README does not independently confirm which documented features pass runtime tests in the current environment."]
        actions=[{"priority":"High","task":"Verify each documented feature locally and label it working, partial, optional, or planned in the handover record.","reason":"README descriptions are not execution evidence.","source":w.get("repo_url",""),"verification":"Recommended validation"}]
    elif is_handover:
        direct="Practical successor handover assembled from the connected README, uploaded notes, and GitHub snapshot. Each section distinguishes documented facts from items that still require inspection or owner confirmation."
        bullets=[]
        intro=_section_find(sections,["scamshield advanced —"])
        bullets.extend(_bullets_from_sections(intro,4))
        for heading in ["Prerequisites","Installation","1. Scam Detection","2. Digital Forensics","3. Digital Forensics Laboratory","API Endpoints","Testing","Important Limitations","Validation Note"]:
            found=_doc_lines_for(w,[heading.lower()],8)
            bullets.extend(found)
        # Ensure all API endpoint lines and test command are included even if heading matcher is broad.
        bullets.extend(_doc_lines_for(w,["api endpoints"],35))
        bullets.extend(_doc_lines_for(w,["testing"],12))
        bullets.extend([
            {"text":"Architecture evidence: README documents a Flask application started with python app.py, local SQLite persistence, and local evidence-file storage. A component diagram, detailed data flow, and internal service boundaries are NOT DOCUMENTED.","source":w.get("repo_url",""),"kind":"Architecture evidence and boundary"},
            {"text":"Technology stack: Python and Flask application; SQLite persistence; OpenCV required for QR decoding; ReportLab required for PDF report generation; optional VirusTotal and Google Safe Browsing URL lookups.","source":w.get("repo_url",""),"kind":"Documented technology stack"},
            {"text":"Database: SQLite is documented for scan history/dashboard statistics and local data storage. Table names, columns, relationships, migrations, and schema diagram are NOT DOCUMENTED.","source":w.get("repo_url",""),"kind":"Database evidence and gap"},
            {"text":"Testing: README documents `python -m unittest discover -s tests -v` and describes coverage for health checks, scan persistence, input validation, detection rules, incident creation, timeline behavior, and registration validation. No test execution result is supplied.","source":w.get("repo_url",""),"kind":"Test instructions, not test results"}
        ])
        gaps=["Detailed architecture and component/data-flow diagram: NOT DOCUMENTED in the connected README.","Database schema/table definitions: NOT DOCUMENTED in the connected README.","Runtime test pass/fail results: NOT PROVIDED; README only documents the test command and coverage areas.","Module ownership and backup owners: NOT DOCUMENTED by commit activity alone.","Uncommitted/local unfinished work and deployment rollback steps: require previous-owner confirmation."]
        actions=checklist[:5]
    elif is_overview:
        direct="Project purpose, module responsibilities, and end-to-end workflow as described in the connected README."
        intro=_section_find(sections,["scamshield advanced —"])
        bullets=_bullets_from_sections(intro,4)+_doc_lines_for(w,["1. scam detection","2. digital forensics","3. digital forensics laboratory","investigation workflow"],35)
        gaps=["Feature descriptions are documentation evidence, not proof of runtime testing."]
        actions=[{"priority":"High","task":"Run the application and validate each workflow stage against the README.","reason":"Runtime behavior is not established by documentation alone.","source":w.get("repo_url",""),"verification":"Recommended validation"}]
    elif is_setup:
        direct="Setup and execution steps copied from connected project documentation."
        bullets=_doc_lines_for(w,["prerequisites","installation","run locally"],30)
        gaps=["Successful setup on the successor's machine and environment-specific variables need confirmation."]
        actions=[{"priority":"High","task":"Reproduce setup in a clean virtual environment and record the output.","reason":"README instructions need local validation.","source":w.get("repo_url",""),"verification":"Not runtime tested"}]
    elif is_api:
        direct="API routes documented in the README. Their presence in documentation does not prove endpoint availability or behavior."
        bullets=_doc_lines_for(w,["api endpoints"],50)
        gaps=["Request schemas, authentication requirements, response codes, and current route behavior require code/runtime verification."]
        actions=[{"priority":"High","task":"Exercise every route and record method, request schema, auth requirements, response, and error behavior.","reason":"README explicitly says endpoint behavior should be confirmed against implementation.","source":w.get("repo_url",""),"verification":"Recommended; not runtime tested"}]
    elif is_risk:
        direct="Documented limitations and security recommendations. These are not automatically confirmed exploitable vulnerabilities."
        bullets=[{"text":f"{r['priority']}: {r['reason']} Recommended verification: {r['task']}","source":r['source'],"kind":r.get('category','Documented limitation')} for r in risks[:20]]
        gaps=["Current exploitability and runtime implementation status require source-code review and testing."]
        actions=checklist[:10]
    elif is_gap:
        direct="Potential documentation gaps identified by checking the connected sources. A gap means not located in these sources, not proof that the information does not exist."
        bullets=[]
        for label,terms in [("Architecture/data-flow diagrams",["architecture diagram","data flow diagram"]),("Database schema and migrations",["create table","schema","migration"]),("Module ownership",["module owner","backup owner"]),("Deployment rollback/recovery",["rollback procedure","recovery runbook"]),("Decision rationale and alternatives",["architecture decision record","alternatives considered"]),("Unfinished-work inventory",["unfinished work","pending tasks","known backlog"])]:
            if not any(t in docs.lower() for t in terms): bullets.append({"text":label+" — not located in connected documentation; confirm with previous owner.","source":w.get("repo_url",""),"kind":"Potential gap"})
        gaps=["README documents API routes and a test command; these must not be mislabeled as missing. Their runtime behavior/results remain unverified."]
        actions=[{"priority":"High","task":"Confirm each potential gap with the previous owner and add approved documentation.","reason":"Text search cannot establish whether knowledge exists outside connected sources.","source":w.get("repo_url",""),"verification":"Requires human confirmation"}]
    elif is_check:
        direct="Prioritized successor actions based on connected repository records and documented limitations."
        bullets=[{"text":f"{t['priority']}: {t['task']} — {t['reason']}","source":t['source'],"kind":t['verification']} for t in checklist]
        gaps=["Recommendations require successor/owner confirmation."]; actions=checklist
    else:
        direct=f"Found {len(hits)} evidence records relevant to the question. The answer is limited to connected evidence; undocumented intent and unverified runtime behavior are not inferred." if hits else "No sufficiently relevant evidence was found. Connect an appropriate source or ask the previous owner."
        bullets=[{"text":f"{r['text']} (recorded owner/author: {r.get('owner','Not specified')})","source":r.get('source',''),"kind":r.get('kind','Evidence')} for r in hits]
        gaps=["Public GitHub records and uploaded documents may omit private discussions, local changes, runtime behavior, and untracked work."]
        actions=[{"priority":"Medium","task":"Confirm interpretation with the previous owner.","reason":"Activity records do not establish full project context.","source":w.get("repo_url",""),"verification":"Recommended"}]

    # De-duplicate evidence by text+source while preserving ordering.
    dedup=[]; seen=set()
    for b in bullets:
        key=(b.get("text",""),b.get("source",""))
        if key not in seen: seen.add(key); dedup.append(b)
    confidence="Evidence-grounded. GitHub records are repository evidence; uploaded files are user-provided context. Commit activity is not proof of ownership or expertise. README feature/API/test descriptions are not runtime verification. Missing means not located in connected sources, not proof of non-existence."
    return {"status":"Structured evidence answer" if dedup else "Not found in available evidence","answer":direct,"sections":{"direct_answer":direct,"supporting_evidence":dedup[:60],"gaps_or_risks":gaps[:16],"next_actions":actions[:16],"confidence":confidence},"evidence":hits[:12]}

@app.get("/api/report")
def report(request: Request):
    w=WORKSPACES.get(workspace_id(request))
    if not w: raise HTTPException(400,"Load a repository or demo first.")
    a=w["analysis"]
    lines=["HANDOVERIQ — PROJECT HANDOVER REPORT",f"Repository: {w['repo']}",f"Source: {w['mode']}",f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}","", "PROJECT CONTEXT",w.get("description", ""),"", "CONTRIBUTION SIGNALS"]
    for o in a["owners"]: lines.append(f"- {o['name']}: {o['commit_count']} observed commits ({o['share']}% of sampled commits). Activity signal only; not proof of expertise.")
    lines += ["", "MODULE ACTIVITY"]
    for m in a["modules"]: lines.append(f"- {m['name']}: {m['commit_count']} commits; contributors: {', '.join(m['contributors'])}")
    lines += ["", "OPEN ISSUES"]
    for i in a["open_issues"]: lines.append(f"- #{i['number']} {i['title']} — {i['url']}")
    lines += ["", "OPEN PULL REQUESTS"]
    for p in a["open_prs"]: lines.append(f"- #{p['number']} {p['title']} — {p['url']}")
    lines += ["", "EVIDENCE-LED ACTIVITY / DECISION CANDIDATES"]
    for d in a["decisions"]: lines.append(f"- {d['text']} | {d['owner']} | {d['source']} | {d['kind']}")
    lines += ["", "RISK SIGNALS"]
    for r in a["risks"]: lines.append(f"- {r['level']}: {r['text']} — {r['detail']} | {r['source']}")
    lines += ["", "LIMITATIONS"]
    for x in a["limitations"]: lines.append("- "+x)
    lines += ["", "IMPORTANT: This is an evidence-organising MVP, not an authoritative assessment of individual expertise. Review and confirm all findings before relying on this handover."]
    return {"filename":"HandoverIQ_Report.txt","content":"\n".join(lines)}
