# api/main.py
import os
import shutil
import hashlib
import uuid
import subprocess
from typing import Any, Dict, Optional

from fastapi import FastAPI, UploadFile, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.embedder import semantic_search, embed_texts

# -----------------------------------------------------------------------------
# App (create FIRST), then middleware, then static mount
# -----------------------------------------------------------------------------
app = FastAPI(title="Pre-Investigation DFIR Agent")
# Serve static UI files
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The path exists because we COPY static/ in Dockerfile and bind-mount in compose


ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "/data/artifacts")

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
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
    """Run the worker inside this container to do extraction + triage."""
    subprocess.Popen(
        ["python", "/app/worker/extract_job.py", image_path, case_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

# -----------------------------------------------------------------------------
# Ingest – FILE (original) now on /ingest_file to avoid clashing with JSON /ingest
# -----------------------------------------------------------------------------
@app.post("/ingest_file")
async def ingest_image(file: UploadFile, background_tasks: BackgroundTasks):
    """
    Ingest a forensic bundle (e.g., zip with EVTX + SOFTWARE hive).
    Returns a case_id and kicks a background extraction task.
    """
    case_id = str(uuid.uuid4())
    dest_dir = os.path.join(ARTIFACT_DIR, case_id)
    os.makedirs(dest_dir, exist_ok=True)

    image_path = os.path.join(dest_dir, file.filename)
    save_upload(file, image_path)
    sha = hash_file(image_path)

    with open(os.path.join(dest_dir, "ingest.json"), "w", encoding="utf-8") as m:
        m.write(
            f'{{"case_id":"{case_id}","filename":"{file.filename}","sha256":"{sha}"}}'
        )

    background_tasks.add_task(kick_extract_task, image_path, case_id)
    return {"case_id": case_id, "filename": file.filename, "sha256": sha}

# -----------------------------------------------------------------------------
# Ingest – JSON (for the tiny UI)
# -----------------------------------------------------------------------------
class IngestTextRequest(BaseModel):
    text: str
    case_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

@app.post("/ingest")
def ingest_text(req: IngestTextRequest):
    """
    Ingest a plain text snippet into a case (semantic index).
    If case_id not provided, we generate one so the UI can re-use it for /search.
    """
    case_id = req.case_id or str(uuid.uuid4())
    try:
        embed_texts(case_id, [req.text], [req.metadata or {}])
        return {"status": "ok", "case_id": case_id, "ingested": 1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# -----------------------------------------------------------------------------
# Search – GET (original) and POST (UI-friendly)
# -----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open(os.path.join(static_dir, "rag_console.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/search")
def search_get(
    case_id: str = Query(..., description="Case ID"),
    q: str = Query(..., description="Natural-language search query"),
    top_k: int = Query(5, ge=1, le=50, description="Number of results to return"),
):
    try:
        return semantic_search(case_id, q, top_k=top_k)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

class SearchRequest(BaseModel):
    case_id: str
    query: str
    top_k: int = 5
    include_metadata: Optional[bool] = True  # harmless; embedder already returns metadata

@app.post("/search")
def search_post(req: SearchRequest):
    try:
        return semantic_search(req.case_id, req.query, top_k=req.top_k)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
