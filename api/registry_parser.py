# api/registry_parser.py

import os
import json
from typing import Dict, Any, List

from regipy.registry import RegistryHive
from regipy.exceptions import RegistryKeyNotFoundException


# Which hives and keys we care about
REGISTRY_TARGETS: Dict[str, List[Dict[str, str]]] = {
    "NTUSER.DAT": [
        {
            "key": r"Software\Microsoft\Windows\CurrentVersion\Run",
            "category": "persistence_run",
        },
        {
            "key": r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
            "category": "persistence_run_once",
        },
        {
            "key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\TypedPaths",
            "category": "typed_paths",
        },
        {
            "key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs",
            "category": "recent_docs",
        },
    ],
    "SOFTWARE": [
        {
            "key": r"Microsoft\Windows\CurrentVersion\Run",
            "category": "persistence_run",
        },
        {
            "key": r"Microsoft\Windows\CurrentVersion\RunOnce",
            "category": "persistence_run_once",
        },
        {
            "key": r"Microsoft\Windows\CurrentVersion\Uninstall",
            "category": "installed_software",
        },
    ],
    "SYSTEM": [
        {
            "key": r"ControlSet001\Services",
            "category": "services",
        },
    ],
}


def detect_hive_name(hive_path: str) -> str:
    """Rough classification based on filename."""
    base = os.path.basename(hive_path).upper()
    # Common export names
    if base.startswith("NTUSER"):
        return "NTUSER.DAT"
    if base.startswith("SOFTWARE"):
        return "SOFTWARE"
    if base.startswith("SYSTEM"):
        return "SYSTEM"
    return base  # fallback


def _extract_key_values(
    hive: RegistryHive, hive_name: str, key_path: str, category: str
) -> List[Dict[str, Any]]:
    """Extract values from a specific key into normalized dicts."""
    try:
        key = hive.get_key(key_path)
    except RegistryKeyNotFoundException:
        return []
    except Exception:
        return []

    # Try a couple of common timestamp attributes
    ts = None
    for attr in ("last_written_timestamp", "timestamp", "header.last_modified"):
        try:
            if "." in attr:
                obj, field = attr.split(".", 1)
                ts = getattr(getattr(key, obj), field, None)
            else:
                ts = getattr(key, attr, None)
            if ts:
                break
        except Exception:
            continue

    result: List[Dict[str, Any]] = []
    for val in getattr(key, "values", []):
        try:
            name = val.name or "(Default)"
            value = val.value
            value_type = getattr(val, "value_type", None)
        except Exception:
            continue

        result.append(
            {
                "hive": hive_name,
                "key_path": key_path,
                "category": category,
                "value_name": name,
                "value": value,
                "value_type": value_type,
                "last_write": str(ts) if ts is not None else None,
            }
        )
    return result


def iter_registry_events(hive_path: str) -> List[Dict[str, Any]]:
    """Extract DFIR-relevant entries from a hive into a list of dicts."""
    hive_name = detect_hive_name(hive_path)
    targets = REGISTRY_TARGETS.get(hive_name, [])
    if not targets:
        # Unknown hive type; you can expand this later.
        return []

    try:
        hive = RegistryHive(hive_path)
    except Exception:
        return []

    events: List[Dict[str, Any]] = []
    for t in targets:
        key_path = t["key"]
        category = t["category"]
        events.extend(_extract_key_values(hive, hive_name, key_path, category))
    return events


def format_registry_event(evt: Dict[str, Any]) -> str:
    """Single-line text representation for semantic indexing."""
    ts = evt.get("last_write") or "UNKNOWN_TIME"
    hive = evt.get("hive") or "UNKNOWN_HIVE"
    cat = evt.get("category") or "unknown"
    key = evt.get("key_path") or ""
    name = evt.get("value_name") or ""
    value = str(evt.get("value", "")).replace("\n", " ").replace("\r", " ")
    return f"[{ts}] HIVE={hive} Category={cat} Key={key} ValueName={name} Value={value}"


def generate_registry_derivatives(hive_path: str, case_dir: str) -> Dict[str, Any]:
    """
    Parse a registry hive and write:
      - artifacts/registry/<basename>.jsonl : structured events
      - artifacts/registry/<basename>.txt  : text summaries
    Returns basic stats.
    """
    events = iter_registry_events(hive_path)
    os.makedirs(case_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(hive_path))[0]

    reg_out_dir = os.path.join(case_dir, "artifacts", "registry")
    os.makedirs(reg_out_dir, exist_ok=True)

    jsonl_path = os.path.join(reg_out_dir, f"{base}.jsonl")
    txt_path = os.path.join(reg_out_dir, f"{base}.txt")

    count = 0
    with open(jsonl_path, "w", encoding="utf-8") as jf, open(
        txt_path, "w", encoding="utf-8"
    ) as tf:
        for evt in events:
            count += 1
            jf.write(json.dumps(evt, ensure_ascii=False) + "\n")
            tf.write(format_registry_event(evt) + "\n")

    return {
        "events_count": count,
        "jsonl_path": jsonl_path,
        "txt_path": txt_path,
    }
