import os
import shutil
import hashlib
import uuid
import subprocess
import json
from typing import Any, Dict, Optional

from fastapi import Body
from fastapi import FastAPI, UploadFile, BackgroundTasks, Query
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.embedder import semantic_search, embed_texts

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

# HELPERS
def save_upload(file: UploadFile, target_path: str):
    with open(target_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def kick_extract_task(image_path: str, case_id: str):
    subprocess.Popen(
        ["python", "/app/worker/extract_job.py", image_path, case_id],
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

    with open(os.path.join(dest_dir, "ingest.json"), "w", encoding="utf-8") as m:
        m.write(json.dumps({
            "case_id": case_id,
            "filename": file.filename,
            "sha256": sha
        }))

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

    case_id = (body.get("case_id") or str(uuid.uuid4())).strip() or str(uuid.uuid4())
    metadata = body.get("metadata") or {"source": "ui"}

    try:
        embed_texts(case_id, [text], [metadata])
        return {"status": "ok", "case_id": case_id, "ingested": 1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# SEARCH
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open(os.path.join(static_dir, "rag_console.html"), "r", encoding="utf-8") as f:
        return f.read()

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
    try:
        cases = []
        for cid in os.listdir(base):
            path = os.path.join(base, cid)
            if os.path.isdir(path):
                meta_file = os.path.join(path, "ingest.json")
                metadata = {}
                if os.path.exists(meta_file):
                    with open(meta_file, "r") as f:
                        metadata = json.load(f)
                cases.append({"case_id": cid, "metadata": metadata})
        return {"cases": cases}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# CASE VIEWER — CASE DETAILS
@app.get("/cases/{case_id}")
def get_case(case_id: str):
    path = os.path.join(ARTIFACT_DIR, case_id)
    if not os.path.isdir(path):
        return JSONResponse(status_code=404, content={"error": "Case not found"})

    def load_file(p, default=None):
        return json.load(open(p)) if os.path.exists(p) else default

    details = {
        "case_id": case_id,
        "ingest": load_file(os.path.join(path, "ingest.json")),
        "triage_findings": load_file(os.path.join(path, "triage_findings.json")),
        "triage_topn": load_file(os.path.join(path, "triage_topn.json")),
        "registry_summaries": [],
        "evtx_summaries": [],
        "playbook": "",
    }

    reg_path = os.path.join(path, "registry_summaries.jsonl")
    if os.path.exists(reg_path):
        with open(reg_path, "r") as f:
            details["registry_summaries"] = f.read().splitlines()

    evtx_path = os.path.join(path, "evtx_summaries.jsonl")
    if os.path.exists(evtx_path):
        with open(evtx_path, "r") as f:
            details["evtx_summaries"] = f.read().splitlines()

    playbook_path = os.path.join(path, "playbook.md")
    if os.path.exists(playbook_path):
        with open(playbook_path, "r") as f:
            details["playbook"] = f.read()

    return details


# CASE VIEWER — DOWNLOAD
@app.get("/cases/{case_id}/download/{filename}")
def download_artifact(case_id: str, filename: str):
    path = os.path.join(ARTIFACT_DIR, case_id, filename)
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return FileResponse(path, filename=filename)
