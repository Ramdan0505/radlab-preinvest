import os
import sys
import json
import uuid
import shutil
import hashlib
import subprocess
from pathlib import Path
from typing import Any, Dict

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
client = OpenAI(api_key=OPENAI_API_KEY)

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
# HELPER FUNCTIONS
# ------------------------------------------------------------------------------------


def save_upload(file: UploadFile, target_path: str) -> None:
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
    subprocess.Popen(
        [sys.executable, "/app/worker/extract_job.py", image_path, case_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def read_text_file(base: Path, name: str) -> str:
    p = base / name
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""

# ------------------------------------------------------------------------------------
# INGEST FILE ENDPOINT
# ------------------------------------------------------------------------------------


@app.post("/ingest_file")
async def ingest_image(file: UploadFile, background_tasks: BackgroundTasks):
    case_id = str(uuid.uuid4())
    dest_dir = os.path.join(ARTIFACT_DIR, case_id)
    os.makedirs(dest_dir, exist_ok=True)

    image_path = os.path.join(dest_dir, file.filename)
    save_upload(file, image_path)

    sha = hash_file(image_path)
    ingest_meta = {"case_id": case_id, "filename": file.filename, "sha256": sha}

    with open(os.path.join(dest_dir, "ingest.json"), "w", encoding="utf-8") as f:
        json.dump(ingest_meta, f, indent=2, ensure_ascii=False)

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

    # Make text-ingest create a real case folder like file ingest
    dest_dir = os.path.join(ARTIFACT_DIR, case_id)
    os.makedirs(dest_dir, exist_ok=True)

    ingest_meta = {
        "case_id": case_id,
        "source": "text",
        "created_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "text_preview": text[:200],
        "metadata": metadata,
    }

    # Write ingest.json so /cases can discover it
    with open(os.path.join(dest_dir, "ingest.json"), "w", encoding="utf-8") as f:
        json.dump(ingest_meta, f, indent=2, ensure_ascii=False)

    try:
        embed_texts(case_id, [text], [metadata])
        return {"status": "ok", "case_id": case_id, "ingested": 1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ------------------------------------------------------------------------------------
# UI ROOT
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

# ------------------------------------------------------------------------------------
# SEARCH ENDPOINTS
# ------------------------------------------------------------------------------------


class SearchRequest(BaseModel):
    case_id: str
    query: str
    top_k: int = 5
    include_metadata: bool = True


@app.get("/search")
def search_get(case_id: str, q: str, top_k: int = 5):
    try:
        return semantic_search(case_id, q, top_k)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


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
                    metadata = json.loads(meta_file.read_text())
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
            return json.loads(path.read_text())
        except Exception:
            return None

    details = {
        "case_id": case_id,
        "ingest": load_json(case_dir / "ingest.json"),
        "triage_findings": load_json(case_dir / "triage_findings.json"),
        "triage_topn": load_json(case_dir / "triage_topn.json"),
        "registry_summaries": read_text_file(case_dir, "registry_summaries.jsonl").splitlines(),
        "evtx_summaries": read_text_file(case_dir, "evtx_summaries.jsonl").splitlines(),
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
# REINDEX CASE
# ------------------------------------------------------------------------------------


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

# ------------------------------------------------------------------------------------
# TIMELINE
# ------------------------------------------------------------------------------------


@app.get("/cases/{case_id}/timeline")
def get_case_timeline(case_id: str):
    case_dir = Path(ARTIFACT_DIR) / case_id

    if not case_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    try:
        events = build_timeline(str(case_dir))
        return {"case_id": case_id, "events": events}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ------------------------------------------------------------------------------------
# EXPLAIN CASE (OpenAI GPT-5.1) + ANALYST NOTES
# ------------------------------------------------------------------------------------


@app.post("/explain_case")
def explain_case_openai(body: Dict[str, Any] = Body(...)):
    case_id = body.get("case_id")
    if not case_id:
        return JSONResponse(status_code=400, content={"error": "Missing case_id"})

    case_path = Path(ARTIFACT_DIR) / case_id
    if not case_path.is_dir():
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    # Helper to read text files safely, from case root or files/
    def read_text(name: str) -> str:
        candidates = [
            case_path / name,
            case_path / "files" / name,
        ]
        for p in candidates:
            if p.exists():
                try:
                    with p.open("r", encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    continue
        return ""

    # Core artifacts
    ingest = read_text("ingest.json")
    triage_findings = read_text("triage_findings.json")
    triage_topn = read_text("triage_topn.json")
    registry_summaries = read_text("registry_summaries.jsonl")
    evtx_summaries = read_text("evtx_summaries.jsonl")
    playbook = read_text("playbook.md")

    # Analyst notes from the bundle (e.g. Notes/operator_notes.txt)
    analyst_notes = read_text("Notes/operator_notes.txt")

    # DFIR prompt
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

### Analyst Notes (operator notes from the bundle)
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
        return JSONResponse(
            status_code=500,
            content={"error": f"OpenAI request failed: {str(e)}"},
        )

    if not summary:
        return JSONResponse(
            status_code=500,
            content={"error": "OpenAI did not return a summary"},
        )

    return {"case_id": case_id, "summary": summary}

# ------------------------------------------------------------------------------------
# WORKER CALLBACK
# ------------------------------------------------------------------------------------


@app.post("/worker_done")
def worker_done(body: dict = Body(...)):
    case_id = body.get("case_id")
    print(f"[API] Worker reports extraction complete for case {case_id}")
    return {"status": "ok", "case_id": case_id}

# ------------------------------------------------------------------------------------
# MITRE ATT&CK TAGGING (OpenAI GPT-5.1)
# ------------------------------------------------------------------------------------


@app.post("/mitre_tags")
def mitre_tags_openai(body: Dict[str, Any] = Body(...)):
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
        return JSONResponse(
            status_code=500,
            content={"error": f"OpenAI request failed: {str(e)}"},
        )

    return {"case_id": case_id, "tags": tags}

# ------------------------------------------------------------------------------------
# OPENAI TEST ENDPOINT
# ------------------------------------------------------------------------------------


@app.get("/test_openai")
def test_openai():
    try:
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[{"role": "user", "content": "Hello"}],
        )
        return {"status": "ok", "reply": response.choices[0].message.content}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# ------------------------------------------------------------------------------------
# END OF FILE
# ------------------------------------------------------------------------------------
