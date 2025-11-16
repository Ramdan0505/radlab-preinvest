import os
import shutil
import hashlib
import uuid
import subprocess

from fastapi import FastAPI, UploadFile, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from embedder import semantic_search  # make sure api/embedder.py exists

ARTIFACT_DIR = os.environ.get("ARTIFACT_DIR", "/data/artifacts")

app = FastAPI(title="Pre-Investigation DFIR Agent")

app.mount("/static", StaticFiles(directory="static"), name="static")


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
    """
    Run the worker inside this same container to do extraction + triage.
    """
    subprocess.Popen(
        ["python", "/app/worker/extract_job.py", image_path, case_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@app.post("/ingest")
async def ingest_image(file: UploadFile, background_tasks: BackgroundTasks):
    """
    Ingest a forensic bundle (e.g. zip with EVTX + SOFTWARE hive).
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


@app.get("/search")
def search_case(
    case_id: str = Query(..., description="Case ID returned by /ingest"),
    q: str = Query(..., description="Natural-language search query"),
    top_k: int = Query(5, ge=1, le=50, description="Number of results to return"),
):
    """
    Semantic search over EVTX + registry artifacts for a given case.
    """
    try:
        results = semantic_search(case_id, q, top_k=top_k)
        return results
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
