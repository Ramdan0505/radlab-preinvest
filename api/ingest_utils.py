# api/ingest_utils.py

import os
from typing import List

from api.evtx_parser import generate_evtx_derivatives
from api.embedder import embed_texts

TEXT_EXTENSIONS = {".txt", ".log", ".json", ".csv", ".md"}

def build_and_index_case_corpus(case_dir: str, case_id: str) -> int:
    """
    Walk the case directory, convert EVTX → text, collect all text,
    and push it into Chroma via embed_texts().

    Returns number of text chunks indexed.
    """
    text_chunks: List[str] = []

    for root, _, files in os.walk(case_dir):
        for filename in files:
            path = os.path.join(root, filename)
            ext = os.path.splitext(filename)[1].lower()

            # 1) EVTX: parse and add summaries
            if ext == ".evtx":
                stats = generate_evtx_derivatives(path, case_dir)
                print(f"[EVTX] {filename}: {stats['events_count']} events parsed")

                with open(stats["txt_path"], "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            text_chunks.append(line)

            # 2) Normal text-like files
            elif ext in TEXT_EXTENSIONS:
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if content.strip():
                        text_chunks.append(content)
                except (UnicodeDecodeError, OSError):
                    continue

    if text_chunks:
        embed_texts(case_id, text_chunks)

    return len(text_chunks)
