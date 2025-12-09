import os
import sys
import requests
import shutil
import hashlib
import uuid
import subprocess
import json

from api.timeline import build_timeline
from typing import Any, Dict
from api.ingest_utils import build_and_index_case_corpus
from fastapi import Body, FastAPI, UploadFile, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path

from api.embedder import semantic_search, embed_texts

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

app = FastAPI(title="Pre-Investigation DFIR Agent")

# STATIC UI
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "/data/artifacts")
os.makedirs(ARTIFACT_DIR, exist_ok=True)


# HELPERS
def save_upload(file: UploadFile, target_path: str) -> None:
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "wb") as f:
        shutil.copyfileobj(file.file, f)


def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def kick_extract_task(image_path: str, case_id: str) -> None:
    # Use the same Python interpreter as the FastAPI process
    subprocess.Popen(
        [sys.executable, "/app/worker/extract_job.py", image_path, case_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# INGEST FILE
@app.post("/ingest_file")
async def ingest_image(file: UploadFile, background_tasks: BackgroundTasks):
    case_id = str(uuid.uuid4())
    dest_dir = os.path.join(ARTIFACT_DIR, case_id)
    os.makedirs(dest_dir, exist_ok=True)

    image_path = os.path.join(dest_dir, file.filename)
    save_upload(file, image_path)
    sha = hash_file(image_path)

    ingest_meta = {
        "case_id": case_id,
        "filename": file.filename,
        "sha256": sha,
    }
    with open(os.path.join(dest_dir, "ingest.json"), "w", encoding="utf-8") as m:
        json.dump(ingest_meta, m, ensure_ascii=False, indent=2)

    background_tasks.add_task(kick_extract_task, image_path, case_id)
    return {"case_id": case_id, "filename": file.filename, "sha256": sha}


# INGEST TEXT
@app.post("/ingest")
def ingest_text(body: Dict[str, Any] = Body(...)):
    """
    Ingest a plain text snippet into a case (semantic index).
    Accepts any JSON dict; we extract 'text', 'case_id', and 'metadata' manually
    to be robust to small front-end differences.
    """
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing 'text' in request body."},
        )

    # Allow client-provided case_id but fall back to a new UUID
    raw_case_id = (body.get("case_id") or "").strip()
    case_id = raw_case_id or str(uuid.uuid4())

    metadata = body.get("metadata") or {"source": "ui"}

    try:
        embed_texts(case_id, [text], [metadata])
        return {"status": "ok", "case_id": case_id, "ingested": 1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ROOT UI
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    ui_path = os.path.join(static_dir, "rag_console.html")
    if not os.path.exists(ui_path):
        return HTMLResponse(
            content="<h1>UI not found</h1><p>rag_console.html is missing.</p>",
            status_code=500,
        )
    with open(ui_path, "r", encoding="utf-8") as f:
        return f.read()


# SEARCH
@app.get("/search")
def search_get(case_id: str, q: str, top_k: int = 5):
    try:
        return semantic_search(case_id, q, top_k=top_k)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


class SearchRequest(BaseModel):
    case_id: str
    query: str
    top_k: int = 5


@app.post("/search")
def search_post(req: SearchRequest):
    try:
        return semantic_search(req.case_id, req.query, top_k=req.top_k)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# CASE VIEWER — LIST CASES
@app.get("/cases")
def list_cases():
    base = ARTIFACT_DIR
    if not os.path.isdir(base):
        # If artifacts dir doesn't exist yet, return empty list
        return {"cases": []}

    try:
        cases = []
        for cid in os.listdir(base):
            path = os.path.join(base, cid)
            if os.path.isdir(path):
                meta_file = os.path.join(path, "ingest.json")
                metadata: Dict[str, Any] = {}
                if os.path.exists(meta_file):
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            metadata = json.load(f)
                    except Exception:
                        metadata = {}
                cases.append({"case_id": cid, "metadata": metadata})
        return {"cases": cases}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# CASE VIEWER — CASE DETAILS
@app.get("/cases/{case_id}")
def get_case(case_id: str):
    # Sanitize case_id usage on filesystem
    case_dir = Path(ARTIFACT_DIR) / case_id
    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    def load_file(p: Path, default=None):
        if not p.exists():
            return default
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    details = {
        "case_id": case_id,
        "ingest": load_file(case_dir / "ingest.json"),
        "triage_findings": load_file(case_dir / "triage_findings.json"),
        "triage_topn": load_file(case_dir / "triage_topn.json"),
        "registry_summaries": [],
        "evtx_summaries": [],
        "playbook": "",
    }

    reg_path = case_dir / "registry_summaries.jsonl"
    if reg_path.exists():
        try:
            with reg_path.open("r", encoding="utf-8") as f:
                details["registry_summaries"] = f.read().splitlines()
        except Exception:
            details["registry_summaries"] = []

    evtx_path = case_dir / "evtx_summaries.jsonl"
    if evtx_path.exists():
        try:
            with evtx_path.open("r", encoding="utf-8") as f:
                details["evtx_summaries"] = f.read().splitlines()
        except Exception:
            details["evtx_summaries"] = []

    playbook_path = case_dir / "playbook.md"
    if playbook_path.exists():
        try:
            with playbook_path.open("r", encoding="utf-8") as f:
                details["playbook"] = f.read()
        except Exception:
            details["playbook"] = ""

    return details


# CASE VIEWER — DOWNLOAD (Hardened)
@app.get("/cases/{case_id}/download/{filename}")
def download_artifact(case_id: str, filename: str):
    base_dir = Path(ARTIFACT_DIR).resolve()
    case_dir = (base_dir / case_id).resolve()

    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    # Prevent path traversal: only allow basename
    safe_name = os.path.basename(filename)
    candidate = (case_dir / safe_name).resolve()

    # Ensure candidate is inside the case directory
    if not str(candidate).startswith(str(case_dir)):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})

    if not candidate.exists():
        return JSONResponse(status_code=404, content={"error": "File not found"})

    return FileResponse(str(candidate), filename=safe_name)

# CASE VIEWER — REINDEX (build semantic corpus from artifacts + EVTX)
@app.post("/cases/{case_id}/reindex")
def reindex_case(case_id: str):
    case_dir = Path(ARTIFACT_DIR) / case_id
    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    try:
        chunks = build_and_index_case_corpus(str(case_dir), case_id)
        return {"case_id": case_id, "indexed_chunks": chunks}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# CASE VIEWER — TIMELINE
@app.get("/cases/{case_id}/timeline")
def get_case_timeline(case_id: str):
    case_dir = Path(ARTIFACT_DIR) / case_id
    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    try:
        events = build_timeline(str(case_dir))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    return {"case_id": case_id, "events": events}




# ---------------------------------------------------
# AI Explain Case  (using Ollama)
@app.post("/explain_case")
def explain_case_ollama(body: Dict[str, Any] = Body(...)):
    case_id = body.get("case_id")
    if not case_id:
        return JSONResponse(status_code=400, content={"error": "Missing case_id"})

    case_path = Path(ARTIFACT_DIR) / case_id
    if not case_path.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    # Helper to read text files safely
    def read_text(name: str) -> str:
        p = case_path / name
        if not p.exists():
            return ""
        try:
            with p.open("r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    ingest = read_text("ingest.json")
    triage_findings = read_text("triage_findings.json")
    triage_topn = read_text("triage_topn.json")
    registry_summaries = read_text("registry_summaries.jsonl")
    evtx_summaries = read_text("evtx_summaries.jsonl")
    playbook = read_text("playbook.md")

    # Build DFIR prompt
    prompt = f"""
You are a senior DFIR (Digital Forensics and Incident Response) analyst.

Analyze the following forensic case and produce a structured, professional report.

CASE ID: {case_id}

### Ingest Information
{ingest or "(none)"}

### Triage Findings
{triage_findings or "(none)"}

### Top Suspicious Items
{triage_topn or "(none)"}

### Registry Summaries
{registry_summaries or "(none)"}

### EVTX Summaries
{evtx_summaries or "(none)"}

### Playbook Notes
{playbook or "(none)"}

Your report MUST include:
- Executive Summary
- Indicators of Compromise (IOCs)
- Key Evidence
- Likely MITRE ATT&CK Techniques (name + technique ID if you know it)
- Narrative Timeline of Activity
- Recommended Next Steps for Investigators
- Confidence Level and Any Data Gaps
"""

    # Call Ollama (from inside Docker using host.docker.internal)
    try:
        resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": "llama3.2:3b",  # adjust model name if you use a different local model
            "messages": [
                {"role": "system", "content": "You are a professional DFIR analyst."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
    },
    timeout=180,
)

        resp.raise_for_status()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Ollama request failed: {str(e)}"},
        )

    try:
        data = resp.json()
    except ValueError:
        return JSONResponse(
            status_code=500,
            content={"error": "Ollama returned non-JSON response"},
        )

    summary = (data.get("message") or {}).get("content", "") or ""
    summary = summary.strip()

    if not summary:
        return JSONResponse(
            status_code=500,
            content={"error": "Ollama did not return a summary"},
        )

    return {"case_id": case_id, "summary": summary}

# ---------------------------------------------------
# MITRE ATT&CK tagging using Ollama + existing summary
# ---------------------------------------------------
@app.post("/mitre_tags")
def mitre_tags(body: Dict[str, Any] = Body(...)):
    case_id = body.get("case_id")
    summary = (body.get("summary") or "").strip()

    if not case_id:
        return JSONResponse(status_code=400, content={"error": "Missing case_id"})
    if not summary:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing 'summary' in request body; run Explain Case first."},
        )

    prompt = f"""
You are a DFIR analyst who knows the MITRE ATT&CK framework.

Given the following incident summary, extract the MITRE ATT&CK techniques that are clearly evidenced.
Only include techniques that are reasonably supported by the summary.

CASE ID: {case_id}

INCIDENT SUMMARY:
\"\"\"{summary}\"\"\"


Return your answer as pure JSON ONLY, no commentary, in the following format:

[
  {{
    "technique_id": "TXXXX",
    "name": "Technique Name",
    "tactic": "Tactic name (e.g., Privilege Escalation, Persistence)",
    "justification": "1–3 sentences explaining why this technique applies."
  }},
  ...
]

If there are no clear techniques, return [].
"""

    try:
        resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": "llama3.2:3b",
            "messages": [
                {"role": "system", "content": "You are a professional DFIR analyst and MITRE ATT&CK expert."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
    },
    timeout=180,
)

        resp.raise_for_status()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Ollama MITRE request failed: {str(e)}"},
        )

    data = resp.json()
    text = (data.get("message", {}) or {}).get("content", "").strip()

    # Try to parse JSON; if model didn't obey strictly, return raw text
    tags: Any
    try:
        tags = json.loads(text)
    except Exception:
        tags = {"raw": text}

    return {"case_id": case_id, "tags": tags}

