# api/ingest_utils.py

import os
from typing import List, Dict, Any

from api.evtx_parser import generate_evtx_derivatives
from api.registry_parser import generate_registry_derivatives
from api.embedder import embed_texts

TEXT_EXTENSIONS = {".txt", ".log", ".json", ".csv", ".md"}
REGISTRY_EXTENSIONS = {".dat", ".hiv", ".hive", ".reg"}
  # crude but effective


def build_and_index_case_corpus(case_dir: str, case_id: str) -> int:
    """
    Walk the case directory, convert EVTX + Registry → text, collect all text,
    write EVTX and Registry summaries to JSONL, and push everything
    into Chroma via embed_texts().

    Returns number of text chunks indexed.
    """
    text_chunks: List[str] = []
    metadata_list: List[Dict[str, Any]] = []

    evtx_summary_path = os.path.join(case_dir, "evtx_summaries.jsonl")
    reg_summary_path = os.path.join(case_dir, "registry_summaries.jsonl")
    evtx_summary_f = None
    reg_summary_f = None

    try:
        evtx_summary_f = open(evtx_summary_path, "w", encoding="utf-8")
        reg_summary_f = open(reg_summary_path, "w", encoding="utf-8")

        for root, _, files in os.walk(case_dir):
            for filename in files:
                path = os.path.join(root, filename)
                ext = os.path.splitext(filename)[1].lower()
                rel_path = os.path.relpath(path, case_dir)
                base_upper = os.path.basename(path).upper()

                # 1) EVTX files
                if ext == ".evtx":
                    stats = generate_evtx_derivatives(path, case_dir)
                    print(f"[EVTX] {filename}: {stats['events_count']} events parsed")

                    with open(stats["txt_path"], "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            text_chunks.append(line)
                            metadata_list.append(
                                {
                                    "source": "evtx",
                                    "case_id": case_id,
                                    "file": rel_path,
                                }
                            )
                            evtx_summary_f.write(line + "\n")

                # 2) Registry hives (NTUSER.DAT, SOFTWARE, SYSTEM, etc.)
                elif (
                    ext in REGISTRY_EXTENSIONS
                    or base_upper.startswith("NTUSER")
                    or base_upper.startswith("SOFTWARE")
                    or base_upper.startswith("SYSTEM")
                ):
                    print(f"[DEBUG] Registry candidate detected: {filename}")

                    try:
                        stats = generate_registry_derivatives(path, case_dir)
                        print(f"[REGISTRY] {filename}: {stats['events_count']} entries parsed")
                    except Exception as e:
                        print(f"[REGISTRY ERROR] Failed to parse {filename}: {e}")
                        continue

                    if stats["events_count"] > 0:
                        with open(stats["txt_path"], "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                text_chunks.append(line)
                                metadata_list.append(
                                    {
                                        "source": "registry",
                                        "case_id": case_id,
                                        "file": rel_path,
                                    }
                                )
                                reg_summary_f.write(line + "\n")


                # 3) Normal text-like files
                elif ext in TEXT_EXTENSIONS:
                    try:
                        with open(
                            path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
                            content = f.read()
                    except (UnicodeDecodeError, OSError):
                        continue

                    if content.strip():
                        text_chunks.append(content)
                        metadata_list.append(
                            {
                                "source": "file",
                                "case_id": case_id,
                                "file": rel_path,
                            }
                        )
    finally:
        if evtx_summary_f is not None:
            evtx_summary_f.close()
        if reg_summary_f is not None:
            reg_summary_f.close()

    if text_chunks:
        embed_texts(case_id, text_chunks, metadata_list)

    return len(text_chunks)
