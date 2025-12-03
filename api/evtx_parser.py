# api/evtx_parser.py

import os
import json
import xml.etree.ElementTree as ET
from typing import Dict, Any, Generator, List, Optional

from Evtx.Evtx import Evtx


# Core DFIR-relevant Event IDs to keep the noise down
INTERESTING_EVENT_IDS = {
    4624,  # Logon
    4625,  # Failed logon
    4634,  # Logoff
    4648,  # Logon with explicit credentials
    4672,  # Special privileges assigned to new logon
    4688,  # Process creation
    4689,  # Process exit
    4720,  # User account created
    4722,  # User account enabled
    4725,  # User account disabled
    4728,  # User added to global security-enabled group
    4732,  # User added to local security-enabled group
    4735,  # Security-enabled local group changed
    4740,  # Account locked out
    4768,  # Kerberos TGT requested
    4769,  # Kerberos service ticket requested
    4776,  # NTLM authentication
    7045,  # Service installed
    4103,  # PowerShell operational
    4104,  # PowerShell script block
}


def _get_nsmap(root: ET.Element) -> Dict[str, str]:
    """
    Build namespace mapping for the EVTX XML document.
    """
    nsmap: Dict[str, str] = {}
    if root.tag.startswith("{"):
        uri = root.tag.split("}")[0].strip("{")
        nsmap["e"] = uri
    return nsmap


def _get_child(parent: ET.Element, tag: str, ns: Dict[str, str]) -> Optional[ET.Element]:
    """
    Helper to find a child element, with or without namespace.
    """
    if ns:
        el = parent.find(f"e:{tag}", ns)
        if el is not None:
            return el
    return parent.find(tag)


def _get_children(parent: ET.Element, tag: str, ns: Dict[str, str]) -> List[ET.Element]:
    """
    Helper to find all children elements, with or without namespace.
    """
    if ns:
        els = parent.findall(f"e:{tag}", ns)
        if els:
            return els
    return parent.findall(tag)


def iter_evtx_events(evtx_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Iterate over *filtered* events in an EVTX file and yield normalized dicts.

    Only returns DFIR-relevant Event IDs listed in INTERESTING_EVENT_IDS.
    """
    with Evtx(evtx_path) as log:
        for record in log.records():
            try:
                xml_str = record.xml()
                root = ET.fromstring(xml_str)
            except Exception:
                # Corrupt record / parsing error: just skip
                continue

            ns = _get_nsmap(root)

            system = _get_child(root, "System", ns)
            if system is None:
                continue

            event_id_el = _get_child(system, "EventID", ns)
            if event_id_el is None or not event_id_el.text:
                continue

            try:
                event_id = int(event_id_el.text.strip())
            except ValueError:
                continue

            # Filter to DFIR-relevant events only
            if event_id not in INTERESTING_EVENT_IDS:
                continue

            time_el = _get_child(system, "TimeCreated", ns)
            timestamp = time_el.get("SystemTime") if time_el is not None else None

            computer_el = _get_child(system, "Computer", ns)
            computer = computer_el.text.strip() if computer_el is not None and computer_el.text else None

            channel_el = _get_child(system, "Channel", ns)
            channel = channel_el.text.strip() if channel_el is not None and channel_el.text else None

            # EventData → {Name: value}
            data: Dict[str, Any] = {}
            event_data_el = _get_child(root, "EventData", ns)
            if event_data_el is not None:
                for d in _get_children(event_data_el, "Data", ns):
                    name = d.get("Name") or "data"
                    value = d.text.strip() if d.text else ""
                    data[name] = value

            yield {
                "record_number": record.record_number(),
                "event_id": event_id,
                "timestamp": timestamp,
                "computer": computer,
                "channel": channel,
                "data": data,
            }


def format_event_for_text(event: Dict[str, Any]) -> str:
    """
    Convert a parsed event into a single-line summary suitable for semantic indexing.
    """
    ts = event.get("timestamp") or "UNKNOWN_TIME"
    eid = event.get("event_id")
    rec = event.get("record_number")
    comp = event.get("computer") or ""
    channel = event.get("channel") or ""
    data = event.get("data") or {}

    clean_data = {}
    for k, v in data.items():
        if v:
            s = str(v).replace("\n", " ").replace("\r", " ")
            clean_data[k] = s

    kv_pairs = " ".join(f"{k}={v}" for k, v in clean_data.items())

    return f"[{ts}] EventID={eid} Record={rec} Computer={comp} Channel={channel} {kv_pairs}".strip()



def generate_evtx_derivatives(evtx_path: str, case_dir: str) -> Dict[str, Any]:
    """
    Parse an EVTX file and write two outputs into the case directory:

    1) JSONL: structured events for later programmatic analysis
    2) TXT: one-line summaries for semantic search & LLM input

    Returns basic stats (events_count, output paths).
    """
    os.makedirs(case_dir, exist_ok=True)

    base = os.path.splitext(os.path.basename(evtx_path))[0]
    evtx_out_dir = os.path.join(case_dir, "artifacts", "evtx")
    os.makedirs(evtx_out_dir, exist_ok=True)

    jsonl_path = os.path.join(evtx_out_dir, f"{base}.jsonl")
    txt_path = os.path.join(evtx_out_dir, f"{base}.txt")

    events_count = 0

    with open(jsonl_path, "w", encoding="utf-8") as jf, \
         open(txt_path, "w", encoding="utf-8") as tf:

        for event in iter_evtx_events(evtx_path):
            events_count += 1
            jf.write(json.dumps(event, ensure_ascii=False) + "\n")
            tf.write(format_event_for_text(event) + "\n")

    return {
        "events_count": events_count,
        "jsonl_path": jsonl_path,
        "txt_path": txt_path,
    }
