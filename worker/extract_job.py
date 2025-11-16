#!/usr/bin/env python3
import sys
import os
import json
import hashlib
import shutil
import zipfile
import re
from pathlib import Path
from datetime import datetime

ARTIFACTS_SUBDIR = "files"

# ----------------------- helpers -----------------------


def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure(out_dir, rel_path):
    dst = os.path.join(out_dir, ARTIFACTS_SUBDIR, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    return dst


def record_meta(out_dir, rel_path, sha=None, extra=None):
    meta = {"path": rel_path}
    if sha:
        meta["sha256"] = sha
    if extra:
        meta.update(extra)
    with open(os.path.join(out_dir, "metadata.jsonl"), "a", encoding="utf-8") as m:
        m.write(json.dumps(meta) + "\n")


# ----------------------- ingest ------------------------


def unpack_zip(zip_path, out_dir):
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            rel = member.filename
            dst = ensure(out_dir, rel)
            with zf.open(member) as src, open(dst, "wb") as dstf:
                shutil.copyfileobj(src, dstf)
            record_meta(out_dir, rel, hash_file(dst), {"source": "zip"})


def copy_single(path, out_dir):
    base = os.path.basename(path)
    dst = ensure(out_dir, base)
    shutil.copy2(path, dst)
    record_meta(out_dir, base, hash_file(dst), {"source": "single"})


def walk_dir(root_path, out_dir):
    for root, _, files in os.walk(root_path):
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, root_path)
            dst = ensure(out_dir, rel)
            # Avoid SameFileError if re-processing files/
            if os.path.abspath(p) == os.path.abspath(dst):
                continue
            shutil.copy2(p, dst)
            record_meta(out_dir, rel, hash_file(dst), {"source": "dir"})


# ----------------------- EVTX --------------------------


def parse_evtx(out_dir):
    try:
        from Evtx.Evtx import Evtx
    except Exception:
        return

    files_root = os.path.join(out_dir, ARTIFACTS_SUBDIR)
    summaries_path = os.path.join(out_dir, "evtx_summaries.jsonl")
    total = 0

    for path in Path(files_root).rglob("*.evtx"):
        try:
            with Evtx(str(path)) as log:
                i = 0
                for rec in log.records():
                    if i >= 200:
                        break
                    try:
                        xml = rec.xml()
                    except Exception:
                        xml = None
                    out = {
                        "file": str(path.relative_to(files_root)),
                        "record_num": rec.record_num(),
                        "timestamp": rec.timestamp().isoformat()
                        if rec.timestamp()
                        else None,
                        "event_id": getattr(rec, "event_id", lambda: None)(),
                        "xml_snippet": (xml[:800] if xml else None),
                    }
                    with open(summaries_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(out) + "\n")
                    i += 1
                    total += 1
        except Exception as e:
            record_meta(
                out_dir,
                str(path.relative_to(files_root)),
                extra={"evtx_parse_error": str(e)},
            )

    with open(os.path.join(out_dir, "evtx_parse_stats.json"), "w", encoding="utf-8") as f:
        json.dump({"total_records_captured": total}, f)


# ----------------------- Registry ----------------------


def parse_registry(out_dir):
    """Parse SOFTWARE hive for autoruns and uninstall entries."""
    try:
        from regipy.registry import RegistryHive
    except Exception:
        return

    files_root = os.path.join(out_dir, ARTIFACTS_SUBDIR)
    out_jsonl = os.path.join(out_dir, "registry_summaries.jsonl")
    errors_log = os.path.join(out_dir, "registry_errors.log")

    def try_get_key(reg, paths):
        for p in paths:
            try:
                return reg.get_key(p)
            except Exception:
                continue
        return None

    hives = list(Path(files_root).rglob("SOFTWARE*"))
    for hive in hives:
        try:
            reg = RegistryHive(str(hive))

            run_key = try_get_key(
                reg,
                [
                    r"Microsoft\Windows\CurrentVersion\Run",
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                ],
            )
            runonce_key = try_get_key(
                reg,
                [
                    r"Microsoft\Windows\CurrentVersion\RunOnce",
                    r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
                ],
            )
            uninstall = try_get_key(
                reg,
                [
                    r"Microsoft\Windows\CurrentVersion\Uninstall",
                    r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                ],
            )

            for k, path_label in [
                (run_key, r"Microsoft\Windows\CurrentVersion\Run"),
                (runonce_key, r"Microsoft\Windows\CurrentVersion\RunOnce"),
            ]:
                if k:
                    for val in k.values:
                        rec = {
                            "hive": str(hive.relative_to(files_root)),
                            "kind": "autorun",
                            "path": path_label,
                            "value_name": val.name,
                            "value_data": val.value,
                        }
                        with open(out_jsonl, "a", encoding="utf-8") as f:
                            f.write(json.dumps(rec) + "\n")

            if uninstall:
                for sk in uninstall.iter_subkeys():
                    disp = (
                        sk.get_value("DisplayName").value
                        if sk.get_value("DisplayName")
                        else None
                    )
                    ver = (
                        sk.get_value("DisplayVersion").value
                        if sk.get_value("DisplayVersion")
                        else None
                    )
                    pub = (
                        sk.get_value("Publisher").value
                        if sk.get_value("Publisher")
                        else None
                    )
                    if any([disp, ver, pub]):
                        rec = {
                            "hive": str(hive.relative_to(files_root)),
                            "kind": "installed_app",
                            "subkey": sk.path,
                            "display_name": disp,
                            "version": ver,
                            "publisher": pub,
                        }
                        with open(out_jsonl, "a", encoding="utf-8") as f:
                            f.write(json.dumps(rec) + "\n")
        except Exception as e:
            with open(errors_log, "a", encoding="utf-8") as f:
                f.write(f"{hive}: {e}\n")


# ----------------------- Triage ------------------------


def triage_findings(out_dir):
    reg_path = os.path.join(out_dir, "registry_summaries.jsonl")
    evtx_path = os.path.join(out_dir, "evtx_summaries.jsonl")
    findings = []

    if os.path.exists(reg_path):
        with open(reg_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("kind") == "autorun":
                    findings.append(
                        {
                            "signal": "autorun_entry",
                            "detail": f"{rec.get('path')} -> {rec.get('value_name')} = {rec.get('value_data')}",
                            "score": 80,
                        }
                    )
                elif rec.get("kind") == "installed_app":
                    pub = (rec.get("publisher") or "").lower()
                    if pub and pub not in ["microsoft", "microsoft corporation"]:
                        findings.append(
                            {
                                "signal": "installed_app_non_ms",
                                "detail": f"{rec.get('display_name')} ({rec.get('publisher')})",
                                "score": 30,
                            }
                        )

    if os.path.exists(evtx_path):
        cnt = sum(1 for _ in open(evtx_path, "r", encoding="utf-8"))
        findings.append(
            {
                "signal": "evtx_sampled_records",
                "detail": f"Sampled records captured: {cnt}",
                "score": 15,
            }
        )

    findings = sorted(findings, key=lambda x: x.get("score", 0), reverse=True)[:20]
    with open(os.path.join(out_dir, "triage_findings.json"), "w", encoding="utf-8") as f:
        json.dump({"findings": findings}, f, indent=2)


# --------------- Ranking + playbook --------------------


SUSPICIOUS_PATTERNS = [
    (r"\\Users\\[^\\]+\\AppData\\", 30, "User-writable AppData path"),
    (r"\\Temp\\|%TEMP%|\\AppData\\Local\\Temp\\", 25, "Temp directory usage"),
    (r"rundll32\.exe", 40, "LOLBIN: rundll32"),
    (r"powershell(\.exe)?\b", 40, "LOLBIN: PowerShell"),
    (r"wscript\.exe|cscript\.exe", 35, "LOLBIN: Windows Script Host"),
    (r"mshta\.exe", 40, "LOLBIN: mshta"),
    (r"cmd\.exe", 20, "Command shell invocation"),
    (r"schtasks\.exe|\\Tasks\\|RunOnce", 25, "Persistence via scheduled tasks / RunOnce"),
    (r"http[s]?://", 20, "Outbound URL reference"),
    (r"\.dll\b|\.exe\b", 10, "Binary reference"),
]


def _score_text(s):
    score, reasons = 0, []
    for pat, pts, why in SUSPICIOUS_PATTERNS:
        if re.search(pat, s, flags=re.IGNORECASE):
            score += pts
            reasons.append(why)
    if len(s) > 200:
        score += 2
    return score, list(dict.fromkeys(reasons))


def _collect_candidates(out_dir):
    cands = []
    reg_path = os.path.join(out_dir, "registry_summaries.jsonl")
    if os.path.exists(reg_path):
        with open(reg_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("kind") == "autorun":
                    text = (
                        f"AUTORUN {rec.get('path')} {rec.get('value_name')} = {rec.get('value_data')}"
                    )
                    cands.append(
                        {"source": "registry", "kind": "autorun", "text": text, "raw": rec}
                    )
                elif rec.get("kind") == "installed_app":
                    disp = rec.get("display_name") or ""
                    pub = rec.get("publisher") or ""
                    text = f"INSTALLED_APP {disp} {pub}"
                    cands.append(
                        {
                            "source": "registry",
                            "kind": "installed_app",
                            "text": text,
                            "raw": rec,
                        }
                    )

    evtx_path = os.path.join(out_dir, "evtx_summaries.jsonl")
    if os.path.exists(evtx_path):
        with open(evtx_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i > 2000:
                    break
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                xml = rec.get("xml_snippet") or ""
                text = f"EVTX {rec.get('file')} #{rec.get('record_num')} {xml}"
                cands.append(
                    {"source": "evtx", "kind": "event", "text": text, "raw": rec}
                )
    return cands


def rank_text_and_write_playbook(out_dir):
    cands = _collect_candidates(out_dir)
    ranked = []
    for c in cands:
        sc, reasons = _score_text(c["text"])
        if c.get("kind") == "autorun":
            sc += 10
        ranked.append({**c, "score": sc, "reasons": reasons})

    ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = ranked[:15]

    with open(os.path.join(out_dir, "triage_topn.json"), "w", encoding="utf-8") as f:
        json.dump({"items": top}, f, indent=2)

    pb = []
    pb.append("# Pre-Investigation Playbook\n")
    pb.append(f"Generated: {datetime.utcnow().isoformat()}Z\n")
    pb.append("## Top Findings (ranked)\n")

    for i, it in enumerate(top, 1):
        raw = it.get("raw", {})
        if it["source"] == "registry" and it.get("kind") == "autorun":
            det = (
                f"{raw.get('path')} -> {raw.get('value_name')} = {raw.get('value_data')}"
            )
        elif it["source"] == "registry":
            det = f"{raw.get('display_name')} ({raw.get('publisher')})"
        else:
            rec = raw
            det = f"{rec.get('file')} record={rec.get('record_num')} time={rec.get('timestamp')}"
        reasons = "; ".join(it.get("reasons") or [])
        pb.append(
            f"{i}. **{it['source']}** [{it.get('kind','')}], score {it['score']}: {det}\n"
            f"   - Reasons: {reasons}\n"
        )

    pb.append("\n## Next Actions\n")
    pb.extend(
        [
            "- Verify autorun binaries on disk; hash & signature check.",
            "- Correlate EVTX hits with Security 4688/1 if available.",
            "- Build a short timeline (Prefetch/AmCache) around top items.",
            "- Export artifacts with SHA256; maintain chain-of-custody.",
        ]
    )

    with open(os.path.join(out_dir, "playbook.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(pb))


# ---------------- Embedding index ----------------------


from embedder import embed_texts  # noqa: E402


def build_embedding_index(out_dir, case_id):
    texts = []
    metas = []

    evtx_file = os.path.join(out_dir, "evtx_summaries.jsonl")
    if os.path.exists(evtx_file):
        with open(evtx_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                snippet = rec.get("xml_snippet") or ""
                texts.append(snippet)
                metas.append(
                    {
                        "type": "evtx",
                        "file": rec.get("file"),
                        "record_num": rec.get("record_num"),
                    }
                )

    reg_file = os.path.join(out_dir, "registry_summaries.jsonl")
    if os.path.exists(reg_file):
        with open(reg_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("kind") == "autorun":
                    text = (
                        f"autorun {rec.get('path')} {rec.get('value_name')} {rec.get('value_data')}"
                    )
                else:
                    text = f"installed_app {rec.get('display_name')} {rec.get('publisher')}"
                texts.append(text)
                metas.append({"type": "registry", "kind": rec.get("kind")})

    embed_texts(case_id, texts, metas)


# ----------------------- main --------------------------


def main():
    if len(sys.argv) < 3:
        print("Usage: extract_job.py <image_path> <case_id>")
        sys.exit(1)

    image_path, case_id = sys.argv[1], sys.argv[2]
    out_dir = f"/data/artifacts/{case_id}"
    os.makedirs(os.path.join(out_dir, ARTIFACTS_SUBDIR), exist_ok=True)

    files_dir = os.path.join(out_dir, ARTIFACTS_SUBDIR)

    if os.path.isfile(image_path) and image_path.lower().endswith(".zip"):
        unpack_zip(image_path, out_dir)
    elif os.path.isdir(image_path):
        if os.path.abspath(image_path).startswith(os.path.abspath(files_dir)):
            # already extracted – just parse
            pass
        else:
            walk_dir(image_path, out_dir)
    else:
        copy_single(image_path, out_dir)

    parse_evtx(out_dir)
    parse_registry(out_dir)
    triage_findings(out_dir)
    rank_text_and_write_playbook(out_dir)
    build_embedding_index(out_dir, case_id)
    print(f"[worker] Extraction complete for case: {case_id}")


if __name__ == "__main__":
    main()
