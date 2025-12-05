# api/registry_parser.py

import os
import json
from typing import Dict, Any, List

from regipy.registry import RegistryHive
from regipy.exceptions import RegistryKeyNotFoundException


# ---------------------------
# CONFIG: which hive types + keys to look at
# ---------------------------

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
    ],
    "SOFTWARE": [
        {
            # OS version & build info
            "key": r"Microsoft\Windows NT\CurrentVersion",
            "category": "os_version",
        },
        {
            # May or may not be populated, but good DFIR target
            "key": r"Microsoft\Windows\CurrentVersion\Run",
            "category": "persistence_run",
        },
        {
            "key": r"Microsoft\Windows\CurrentVersion\RunOnce",
            "category": "persistence_run_once",
        },
    ],
    "SYSTEM": [
        {
            "key": r"ControlSet001\Services",
            "category": "services",
        },
    ],
}


# ---------------------------
# Helpers for regipy-based hive parsing
# ---------------------------

def detect_hive_name(hive_path: str) -> str:
    """Rough classification based on filename."""
    base = os.path.basename(hive_path).upper()
    if base.startswith("NTUSER"):
        return "NTUSER.DAT"
    if base.startswith("SOFTWARE"):
        return "SOFTWARE"
    if base.startswith("SYSTEM"):
        return "SYSTEM"
    return base


def _extract_key_values(
    hive: RegistryHive, hive_name: str, key_path: str, category: str
) -> List[Dict[str, Any]]:
    """
    Extract values from a specific key into normalized dicts.

    We try both "Key\Path" and "\Key\Path" forms to handle regipy path quirks.
    """
    candidates = [key_path]
    if not key_path.startswith("\\"):
        candidates.append("\\" + key_path)

    key = None
    for kp in candidates:
        try:
            key = hive.get_key(kp)
            break
        except RegistryKeyNotFoundException:
            continue
        except Exception:
            return []

    if key is None:
        return []

    # Try a couple of common timestamp attributes
    ts = None
    for attr in ("last_written_timestamp", "timestamp"):
        try:
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
    """
    Extract DFIR-relevant entries from a binary hive into a list of dicts.

    NOTE: On some newer Windows builds, regipy may return empty results
    for hives saved with `reg save`. In that case, prefer exporting as
    a .reg file and using parse_reg_file().
    """
    hive_name = detect_hive_name(hive_path)
    targets = REGISTRY_TARGETS.get(hive_name, [])
    if not targets:
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


# ---------------------------
# Simple .reg export parser
# ---------------------------

def parse_reg_file(reg_path: str) -> List[Dict[str, Any]]:
    """
    Parse a REG file exported with `reg export`.
    This is NOT a full REG parser; it just extracts:
      - key path (e.g. HKEY_LOCAL_MACHINE\SOFTWARE\...)
      - value name (@ for default)
      - raw value string

    It works well enough to feed semantic search and LLMs.
    """
    events: List[Dict[str, Any]] = []
    current_key: str | None = None

    # reg export on modern Windows uses UTF-16 LE with BOM
    with open(reg_path, "r", encoding="utf-16", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(";"):
                # comment
                continue

            # Section header: [HKEY_LOCAL_MACHINE\SOFTWARE\...]
            if line.startswith("[") and line.endswith("]"):
                current_key = line[1:-1]
                continue

            if "=" in line and current_key:
                name_part, value_part = line.split("=", 1)
                name_part = name_part.strip()
                value_part = value_part.strip()

                if name_part == "@":
                    value_name = "(Default)"
                elif name_part.startswith('"') and name_part.endswith('"'):
                    value_name = name_part[1:-1]
                else:
                    value_name = name_part

                # Keep raw value string (e.g., "SomeValue", dword:00000001, hex:...)
                value = value_part

                events.append(
                    {
                        "hive": "SOFTWARE.REG",
                        "key_path": current_key,
                        "category": "reg_export",
                        "value_name": value_name,
                        "value": value,
                        "value_type": "raw",
                        "last_write": None,
                    }
                )

    return events


# ---------------------------
# Formatting + derivative generation
# ---------------------------

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
    Parse a registry hive or REG export and write:
      - artifacts/registry/<basename>.jsonl : structured events
      - artifacts/registry/<basename>.txt  : text summaries

    Returns basic stats.
    """
    os.makedirs(case_dir, exist_ok=True)

    base = os.path.splitext(os.path.basename(hive_path))[0]
    reg_out_dir = os.path.join(case_dir, "artifacts", "registry")
    os.makedirs(reg_out_dir, exist_ok=True)

    jsonl_path = os.path.join(reg_out_dir, f"{base}.jsonl")
    txt_path = os.path.join(reg_out_dir, f"{base}.txt")

    # Decide which parser to use
    if hive_path.lower().endswith(".reg"):
        events = parse_reg_file(hive_path)
    else:
        events = iter_registry_events(hive_path)

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
