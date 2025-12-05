# api/registry_parser.py

import os
import json
from typing import Dict, Any, List, Optional

from regipy.registry import RegistryHive
from regipy.exceptions import RegistryKeyNotFoundException


# ---------------------------
# CONFIG: high-value DFIR areas
# ---------------------------

REGISTRY_TARGETS: Dict[str, List[Dict[str, str]]] = {
    "NTUSER.DAT": [
        # User persistence
        {"key": r"Software\Microsoft\Windows\CurrentVersion\Run", "category": "user_persistence_run"},
        {"key": r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "category": "user_persistence_runonce"},

        # User activity
        {"key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\TypedPaths", "category": "typed_paths"},
        {"key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU", "category": "run_mru"},
        {"key": r"Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs", "category": "recent_docs"},
    ],

    "SOFTWARE": [
        # OS metadata
        {"key": r"Microsoft\Windows NT\CurrentVersion", "category": "os_metadata"},

        # System persistence
        {"key": r"Microsoft\Windows\CurrentVersion\Run", "category": "system_persistence_run"},
        {"key": r"Microsoft\Windows\CurrentVersion\RunOnce", "category": "system_persistence_runonce"},

        # Installed software
        {"key": r"Microsoft\Windows\CurrentVersion\Uninstall", "category": "installed_software"},
    ],

    "SYSTEM": [
        # Services (persistence & execution)
        {"key": r"ControlSet001\Services", "category": "services"},

        # Session environment (can be abused)
        {"key": r"ControlSet001\Control\Session Manager\Environment", "category": "session_env"},
    ],
}


# ---------------------------
# Helpers for regipy-based hive parsing
# ---------------------------

def detect_hive_name(hive_path: str) -> str:
    """Approximate hive type from filename."""
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

    Tries both "Key\\Path" and "\\Key\\Path" forms to handle regipy path quirks.
    """
    candidates = [key_path]
    if not key_path.startswith("\\"):
        candidates.append("\\" + key_path)

    key: Optional[Any] = None
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

    # Try common timestamp attributes
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

    On newer Windows builds, hives saved with `reg save` may yield 0 events;
    in that case prefer exporting as a .reg and using parse_reg_file().
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
    r"""
    Parse a REG file exported with `reg export`.

    This parser:
      - Tracks the current key path [HKEY_...]
      - Extracts value name + raw string
      - KEEPS ONLY high-value DFIR paths:
          * HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion          (OS metadata)
          * HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run         (system persistence)
          * HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce     (system persistence)
          * HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall   (installed software)
    """
    events: List[Dict[str, Any]] = []
    current_key: Optional[str] = None

    # Normalized prefixes (lowercased) for matching
    os_meta_prefix = r"hkey_local_machine\software\microsoft\windows nt\currentversion"
    run_prefix = r"hkey_local_machine\software\microsoft\windows\currentversion\run"
    runonce_prefix = r"hkey_local_machine\software\microsoft\windows\currentversion\runonce"
    uninstall_prefix = r"hkey_local_machine\software\microsoft\windows\currentversion\uninstall"

    allowed_prefixes = [
        os_meta_prefix,
        run_prefix,
        runonce_prefix,
        uninstall_prefix,
    ]

    # For uninstall keys, only keep these value names
    uninstall_value_whitelist = {
        "displayname",
        "displayicon",
        "installlocation",
        "publisher",
        "installdate",
        "uninstallstring",
        "quietuninstallstring",
        "displayversion",
    }

    # reg export uses UTF-16 LE with BOM on modern Windows
    with open(reg_path, "r", encoding="utf-16", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(";"):
                continue

            # Section header: [HKEY_LOCAL_MACHINE\SOFTWARE\...]
            if line.startswith("[") and line.endswith("]"):
                current_key = line[1:-1]
                continue

            if "=" in line and current_key:
                name_part, value_part = line.split("=", 1)
                name_part = name_part.strip()
                value_part = value_part.strip()

                # Normalize key path for prefix matching
                key_lower = current_key.lower()

                # Filter on allowed root prefixes only
                if not any(key_lower.startswith(p) for p in allowed_prefixes):
                    continue

                # Determine value name
                if name_part == "@":
                    value_name = "(Default)"
                elif name_part.startswith('"') and name_part.endswith('"'):
                    value_name = name_part[1:-1]
                else:
                    value_name = name_part

                value = value_part  # raw registry value string

                # Additional filter for Uninstall keys: only keep important fields
                if key_lower.startswith(uninstall_prefix):
                    if value_name.lower() not in uninstall_value_whitelist:
                        continue

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

    # Choose parser based on extension
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
