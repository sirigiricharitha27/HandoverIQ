# HandoverIQ — AI-THON Round 2 MVP

Evidence-backed project knowledge handover. This MVP connects to a public GitHub repository (or loads a clearly labelled synthetic demo), organises commits/PRs/issues into contribution and module activity signals, accepts authorised text/Markdown notes, answers questions only from matching evidence, and exports a text handover report.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000. Health check: http://127.0.0.1:8000/api/health

## Deploy on Render

1. Create a **public** GitHub repository and upload all files in this folder.
2. In Render, choose New → Web Service → connect the repository.
3. Render detects `render.yaml`; it generates a session signing secret and enables secure cookies. Otherwise use build command `pip install -r requirements.txt` and start command `uvicorn main:app --host 0.0.0.0 --port $PORT`.
4. Wait for deployment; test the public URL and `/api/health` in an incognito window.

## Current implementation / honest scope

- Public GitHub repository ingestion using GitHub REST API (repository metadata, up to 100 commits, issues and pull requests per endpoint, plus GitHub contributor summary where available).
- Source-code-aware ingestion (up to 24 bounded Python, dependency, template, and static files) with file-level GitHub evidence links.
- Synthetic demo dataset, visibly labelled as fictional.
- Conservative keyword grouping of commit activity into module categories.
- Contribution counts as activity signals only; they are not claims of expertise or sole ownership.
- Upload of UTF-8 `.txt`, `.md` or `.rst` notes up to 1 MB.
- Intent-specific evidence Q&A for project overview, module features, optional/future features, successor handover, technical decisions, source-code inspection, cross-source documentation gaps, and GitHub ownership/activity. Technical code inspection takes priority over generic “not documented” wording. It uses deterministic rules and keyword retrieval, not a generative LLM.
- Downloadable text handover report with source URLs and limitations.

## Known limitations

- No user accounts, persistent database or GitHub OAuth. Workspaces are isolated by a signed browser session, held in server memory, and reset when the service restarts.
- Public GitHub repositories only; no private repository access.
- No semantic embeddings or LLM reasoning in this MVP. Extracted items are rule/keyword based and require human verification.
- Only text/Markdown documents are supported; no PDF, Word or OCR processing.
- No automated webhook/continuous capture; data is fetched on demand.
- Commit activity does not establish expertise, rationale or sole module ownership.
- Report is plain text, not PDF.

## Privacy and data handling

Use only repositories and documents you are authorised to access. Do not upload confidential workplace material without permission. The demo dataset is synthetic. This MVP stores each browser session workspace in process memory and does not intentionally persist repository contents to disk. Workspaces reset when the process restarts. For production, configure a strong `SESSION_SECRET` and set `COOKIE_SECURE=true` when served over HTTPS.

## AI/tool disclosure

This implementation was authored with AI assistance. Runtime features in this MVP use FastAPI, Python, GitHub's public REST API and deterministic keyword/rule-based extraction; no external LLM API is called by the running application. Disclose AI assistance and external tools according to the event form.


## Ask the Evidence — structured synthesis update

The Ask the Evidence endpoint now produces a structured, evidence-grounded response with:
- direct answers for ordinary evidence questions;
- supporting GitHub/document records with source references (uploaded text references include line numbers);
- documented risk/limitation investigations, clearly separated from confirmed vulnerabilities;
- potential documentation gaps based on conservative keyword checks;
- prioritized successor actions with reason, source, and verification status;
- an explicit confidence/unknown-information note.

This is a deterministic, rule-based synthesis layer, not an LLM. It does not claim to inspect runtime behaviour or infer missing design rationale. Uploaded notes remain user-provided evidence and should be confirmed by a human.

### Verification performed
- Python syntax compilation passed.
- FastAPI demo workspace and `/api/ask` prioritized-checklist request returned HTTP 200.
- The connected live GitHub repository was not re-fetched from this environment; test with `sirigiricharitha27/ScamShield-Advanced` in the deployed app after replacing the project files.


## Intent-aware Q&A update (v0.2.0)

Ask the Evidence now routes common handover question types to separate evidence views:
- **Project overview:** purpose, modules, and investigation workflow.
- **Feature inventory:** core documented module features, optional features/integrations, and future research extensions.
- **Successor handover:** setup, technology stack, architecture evidence and unknowns, modules, API routes, database evidence/schema gaps, test instructions versus actual test results, limitations, and first actions.
- **Technical decisions:** documented technology use separated from rationale and alternatives, which remain NOT DOCUMENTED unless a source says otherwise.
- **Cross-source comparison:** README section inventory, uploaded-note excerpts, documented facts, potential gaps, and unverified claims. It does not claim semantic contradiction detection.
- **GitHub activity:** contributor API results when available, commit author strings, commit sample, issues, pull requests, and activity-versus-ownership caveats.

GitHub activity is fetched on connect (up to 100 records per listed collection). Results are a snapshot, not a live feed. Different commit author strings may refer to the same person. Module ownership is never inferred from commit counts.

### Verification performed for v0.2.0
- `python -m py_compile main.py` passed.
- FastAPI TestClient exercised six representative questions (overview, features, successor handover, decisions, cross-source gaps, and GitHub activity); all returned HTTP 200 with intent-specific sections.
- ZIP archive integrity checked after packaging.
- These checks do not replace a full run against every live repository, test suite execution, or security review.


## Version 0.3.0 — source-code-aware question routing

- Public GitHub connection now fetches a bounded set of implementation files (Python, dependency manifests, and relevant template/static files), in addition to README and activity metadata.
- Source files are labelled separately as GitHub source-code evidence and retain their individual GitHub file links.
- Technical implementation questions are routed ahead of generic documentation-gap detection. Phrases such as “NOT DOCUMENTED” no longer accidentally trigger cross-source comparison.
- Source inspection extracts technology usage, Flask/FastAPI route decorators, SQLite `CREATE TABLE` names, fetched-file inventory, and a cautious request-flow summary.
- The answer explicitly distinguishes source declarations from runtime verification and identifies files or details not inspected.
- Cross-source comparison now requires explicit comparison/contradiction language.

**Limits:** source retrieval is bounded to 24 files (each at most 120 KB) and GitHub's public unauthenticated API. The extractor is static and pattern-based; it does not execute repository code, guarantee complete endpoint/schema extraction, or prove runtime correctness. Review the fetched file inventory and test the application before relying on the handover.
