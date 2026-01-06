import os
import sys
import json
import uuid
import shutil
import hashlib
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, UploadFile, BackgroundTasks, Body, Query
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dotenv import load_dotenv
from datetime import datetime
from openai import OpenAI

from api.timeline import build_timeline
from api.embedder import semantic_search, embed_texts
from api.ingest_utils import build_and_index_case_corpus

load_dotenv()

# ------------------------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------------------------

ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "/data/artifacts")
os.makedirs(ARTIFACT_DIR, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI(title="Pre-Investigation DFIR Agent")

# Static UI
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------------------------

DEFAULT_MAX_CHARS = int(os.getenv("EXPLAIN_MAX_CHARS", "12000"))
DETAIL_LINES_LIMIT = int(os.getenv("CASE_DETAILS_LINES_LIMIT", "200"))

def read_limited_text(path: Path, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Read up to max_chars from a text file safely."""
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)
    except Exception:
        return ""


def read_limited_lines(path: Path, max_lines: int = DETAIL_LINES_LIMIT, max_chars: int = 200_000) -> List[str]:
    """
    Read up to max_chars then split into lines and cap to max_lines.
    Prevents returning megabytes of JSONL to the UI.
    """
    txt = read_limited_text(path, max_chars=max_chars)
    if not txt:
        return []
    lines = txt.splitlines()
    return lines[:max_lines]


def save_upload(file: UploadFile, target_path: str) -> None:
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    try:
        file.file.seek(0)
    except Exception:
        pass

    with open(target_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    if os.path.getsize(target_path) == 0:
        raise RuntimeError(f"Saved upload is 0 bytes: {target_path}")


def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def kick_extract_task(image_path: str, case_id: str) -> None:
    # Runs inside the API container process space; spawns a worker job script.
    subprocess.Popen([sys.executable, "/app/worker/extract_job.py", image_path, case_id])


def read_text_file(base: Path, name: str) -> str:
    p = base / name
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


@app.get("/", response_class=HTMLResponse)
def ui_root():
    """
    Convenience: serve the UI at / so you don't see {"detail":"Not Found"}.
    """
    index_path = Path(static_dir) / "rag_console.html"
    if not index_path.exists():
        return HTMLResponse("<h3>UI not found. Open /static/rag_console.html</h3>", status_code=200)
    return HTMLResponse(index_path.read_text(encoding="utf-8", errors="ignore"))


# ------------------------------------------------------------------------------------
# INGEST FILE ENDPOINT
# ------------------------------------------------------------------------------------

@app.post("/ingest_file")
async def ingest_image(file: UploadFile, background_tasks: BackgroundTasks):
    case_id = str(uuid.uuid4())
    dest_dir = os.path.join(ARTIFACT_DIR, case_id)
    os.makedirs(dest_dir, exist_ok=True)

    safe_name = os.path.basename(file.filename)
    image_path = os.path.join(dest_dir, safe_name)

    save_upload(file, image_path)

    if (not os.path.exists(image_path)) or (os.path.getsize(image_path) == 0):
        return JSONResponse(status_code=500, content={"error": "Upload save failed", "path": image_path})

    sha = hash_file(image_path)
    ingest_meta = {"case_id": case_id, "filename": safe_name, "sha256": sha}

    with open(os.path.join(dest_dir, "ingest.json"), "w", encoding="utf-8") as f:
        json.dump(ingest_meta, f, indent=2, ensure_ascii=False)

    # Run extraction async
    background_tasks.add_task(kick_extract_task, image_path, case_id)
    return ingest_meta


# ------------------------------------------------------------------------------------
# INGEST RAW TEXT
# ------------------------------------------------------------------------------------

@app.post("/ingest")
def ingest_text(body: Dict[str, Any] = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"error": "Missing text"})

    case_id = (body.get("case_id") or "").strip() or str(uuid.uuid4())
    metadata = body.get("metadata") or {"source": "ui"}

    dest_dir = os.path.join(ARTIFACT_DIR, case_id)
    os.makedirs(dest_dir, exist_ok=True)

    ingest_meta = {
        "case_id": case_id,
        "source": "text",
        "created_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "text_preview": text[:200],
        "metadata": metadata,
    }

    with open(os.path.join(dest_dir, "ingest.json"), "w", encoding="utf-8") as f:
        json.dump(ingest_meta, f, indent=2, ensure_ascii=False)

    try:
        embed_texts(case_id, [text], [metadata])
        return {"status": "ok", "case_id": case_id, "ingested": 1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ------------------------------------------------------------------------------------
# SEARCH
# ------------------------------------------------------------------------------------

@app.get("/search")
def search_get(
    case_id: str,
    q: str,
    top_k: int = 5,
    include_metadata: bool = Query(True),
):
    try:
        out = semantic_search(case_id, q, top_k)
        if not include_metadata:
            for r in out.get("results", []):
                r.pop("metadata", None)
        return out
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


class SearchRequest(BaseModel):
    case_id: str
    query: str
    top_k: int = 5
    include_metadata: bool = True


@app.post("/search")
def search_post(req: SearchRequest):
    try:
        out = semantic_search(req.case_id, req.query, req.top_k)
        if not req.include_metadata:
            for r in out.get("results", []):
                r.pop("metadata", None)
        return out
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ------------------------------------------------------------------------------------
# CASE LISTING
# ------------------------------------------------------------------------------------

@app.get("/cases")
def list_cases():
    base = Path(ARTIFACT_DIR)
    if not base.exists():
        return {"cases": []}

    cases = []
    for cid in os.listdir(base):
        path = base / cid
        if path.is_dir():
            meta_file = path / "ingest.json"
            metadata = {}
            if meta_file.exists():
                try:
                    metadata = json.loads(meta_file.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    metadata = {}
            cases.append({"case_id": cid, "metadata": metadata})
    return {"cases": cases}


# ------------------------------------------------------------------------------------
# CASE DETAILS
# ------------------------------------------------------------------------------------

@app.get("/cases/{case_id}")
def get_case(case_id: str):
    case_dir = Path(ARTIFACT_DIR) / case_id
    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    def load_json(path: Path):
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return None

    evtx_path = case_dir / "evtx_summaries.jsonl"
    reg_path = case_dir / "registry_summaries.jsonl"

    details = {
        "case_id": case_id,
        "ingest": load_json(case_dir / "ingest.json"),
        "triage_findings": load_json(case_dir / "triage_findings.json"),
        "triage_topn": load_json(case_dir / "triage_topn.json"),
        # UI-safe: return limited lines, plus quick sizes
        "registry_summaries": read_limited_lines(reg_path),
        "evtx_summaries": read_limited_lines(evtx_path),
        "registry_summaries_bytes": reg_path.stat().st_size if reg_path.exists() else 0,
        "evtx_summaries_bytes": evtx_path.stat().st_size if evtx_path.exists() else 0,
        "playbook": read_text_file(case_dir, "playbook.md"),
    }

    return details


# ------------------------------------------------------------------------------------
# ARTIFACT DOWNLOAD (SAFE)
# ------------------------------------------------------------------------------------

@app.get("/cases/{case_id}/download/{filename}")
def download_artifact(case_id: str, filename: str):
    safe_name = os.path.basename(filename)
    case_dir = (Path(ARTIFACT_DIR) / case_id).resolve()

    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    candidate = (case_dir / safe_name).resolve()
    if not str(candidate).startswith(str(case_dir)):
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})

    if not candidate.exists():
        return JSONResponse(status_code=404, content={"error": "File not found"})

    return FileResponse(str(candidate), filename=safe_name)


# ------------------------------------------------------------------------------------
# REINDEX CASE (SCHEDULED)
# ------------------------------------------------------------------------------------

@app.post("/cases/{case_id}/reindex")
def reindex_case(case_id: str, background_tasks: BackgroundTasks):
    case_dir = Path(ARTIFACT_DIR) / case_id
    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    # Schedule to avoid long HTTP requests/timeouts
    background_tasks.add_task(build_and_index_case_corpus, str(case_dir), case_id)
    return {"case_id": case_id, "indexed_chunks": "scheduled"}


# ------------------------------------------------------------------------------------
# TIMELINE
# ------------------------------------------------------------------------------------

@app.get("/cases/{case_id}/timeline")
def get_case_timeline(case_id: str, limit: int = 200, descending: bool = True):
    case_dir = Path(ARTIFACT_DIR) / case_id
    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    try:
        events = build_timeline(str(case_dir), limit=limit, descending=descending)  # requires updated timeline.py signature
        return {"case_id": case_id, "events": events}
    except TypeError:
        # Backward compatibility if build_timeline(case_dir) signature is old
        events = build_timeline(str(case_dir))
        return {"case_id": case_id, "events": events}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ------------------------------------------------------------------------------------
# EXPLAIN CASE
# ------------------------------------------------------------------------------------

@app.post("/explain_case")
def explain_case_openai(body: Dict[str, Any] = Body(...)):
    if client is None:
        return JSONResponse(status_code=500, content={"error": "OPENAI_API_KEY not set"})

    case_id = body.get("case_id")
    if not case_id:
        return JSONResponse(status_code=400, content={"error": "Missing case_id"})

    case_path = Path(ARTIFACT_DIR) / case_id
    if not case_path.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    def read_text(name: str, limit_chars: Optional[int] = None) -> str:
        candidates = [case_path / name, case_path / "files" / name]
        for p in candidates:
            if p.exists():
                if limit_chars is None:
                    try:
                        return p.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                return read_limited_text(p, max_chars=limit_chars)
        return ""

    ingest = read_text("ingest.json", limit_chars=4000)
    triage_findings = read_text("triage_findings.json", limit_chars=12000)
    triage_topn = read_text("triage_topn.json", limit_chars=12000)

    # KEY FIX: limit BOTH summaries
    registry_summaries = read_text("registry_summaries.jsonl", limit_chars=DEFAULT_MAX_CHARS)
    evtx_summaries = read_text("evtx_summaries.jsonl", limit_chars=DEFAULT_MAX_CHARS)

    playbook = read_text("playbook.md", limit_chars=12000)
    analyst_notes = read_text("Notes/operator_notes.txt", limit_chars=12000)

    prompt = f"""
You are a senior DFIR (Digital Forensics and Incident Response) analyst.

Analyze the following forensic case and produce a structured, professional report.
If some artifacts are missing (no triage findings, no EVTX summaries, etc.),
be explicit about those data gaps and base your conclusions only on available evidence.

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

### Analyst Notes
{analyst_notes or "(none)"}

Your report MUST include:
- Executive Summary
- Indicators of Compromise (IOCs)
- Key Evidence
- Likely MITRE ATT&CK Techniques (ID + name) with brief justification
- Narrative Timeline of Activity
- Recommended Next Steps for Investigators
- Confidence Level and Any Data Gaps
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": "You are a professional DFIR analyst."},
                {"role": "user", "content": prompt},
            ],
        )
        summary = response.choices[0].message.content.strip()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"OpenAI request failed: {str(e)}"})

    if not summary:
        return JSONResponse(status_code=500, content={"error": "OpenAI did not return a summary"})

    return {"case_id": case_id, "summary": summary}


# ------------------------------------------------------------------------------------
# WORKER CALLBACK
# ------------------------------------------------------------------------------------

@app.post("/worker_done")
def worker_done(body: dict = Body(...), background_tasks: BackgroundTasks = None):
    case_id = body.get("case_id")
    print(f"[API] Worker reports extraction complete for case {case_id}")

    if not case_id:
        return JSONResponse(status_code=400, content={"error": "Missing case_id"})

    case_dir = Path(ARTIFACT_DIR) / case_id
    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case folder not found"})

    # Schedule indexing; return fast so worker doesn't time out
    if background_tasks is not None:
        background_tasks.add_task(build_and_index_case_corpus, str(case_dir), case_id)

    return {"status": "ok", "case_id": case_id}


# ------------------------------------------------------------------------------------
# MITRE ATT&CK TAGGING
# ------------------------------------------------------------------------------------

@app.post("/mitre_tags")
def mitre_tags_openai(body: Dict[str, Any] = Body(...)):
    if client is None:
        return JSONResponse(status_code=500, content={"error": "OPENAI_API_KEY not set"})

    case_id = body.get("case_id")
    summary = (body.get("summary") or "").strip()

    if not case_id:
        return JSONResponse(status_code=400, content={"error": "Missing case_id"})
    if not summary:
        return JSONResponse(status_code=400, content={"error": "Missing summary"})

    prompt = f"""
Extract all MITRE ATT&CK techniques that are clearly evidenced in this DFIR incident summary.

Return ONLY JSON in this exact format:

[
  {{
    "technique_id": "Txxxx",
    "name": "Technique Name",
    "tactic": "Tactic",
    "justification": "Explain why this technique applies."
  }}
]

If none apply, return [].

INCIDENT SUMMARY:
\"\"\"{summary}\"\"\"
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": "MITRE ATT&CK expert."},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.choices[0].message.content.strip()
        try:
            tags = json.loads(text)
        except Exception:
            tags = {"raw": text}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"OpenAI request failed: {str(e)}"})

    return {"case_id": case_id, "tags": tags}


# ------------------------------------------------------------------------------------
# OPENAI TEST
# ------------------------------------------------------------------------------------

@app.get("/test_openai")
def test_openai():
    if client is None:
        return {"status": "error", "error": "OPENAI_API_KEY not set"}
    try:
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[{"role": "user", "content": "Hello"}],
        )
        return {"status": "ok", "reply": response.choices[0].message.content}
    except Exception as e:
        return {"status": "error", "error": str(e)}
