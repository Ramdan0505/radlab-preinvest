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

app = FastAPI(title="Pre-Investigation DFIR Agent")

# STATIC UI MOUNT (fixed: only one mount)
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


# ------------ Helpers --------------
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


# ------------ Ingest FILE --------------
@app.post("/ingest_file")
async def ingest_image(file: UploadFile, background_tasks: BackgroundTasks):
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


# ------------ Ingest TEXT --------------
class IngestTextRequest(BaseModel):
    text: str
    case_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

@app.post("/ingest")
def ingest_text(req: IngestTextRequest):
    case_id = req.case_id or str(uuid.uuid4())
    try:
        embed_texts(case_id, [req.text], [req.metadata or {}])
        return {"status": "ok", "case_id": case_id, "ingested": 1}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ------------ Search --------------
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
        return s
