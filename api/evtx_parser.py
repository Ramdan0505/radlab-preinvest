# api/evtx_parser.py
import os
import json
import xml.etree.ElementTree as ET
from typing import Dict, Any, Generator, List, Optional

from Evtx.Evtx import Evtx

# -----------------------------
# Event IDs worth indexing
# -----------------------------
INTERESTING_EVENT_IDS = {
    # Authentication / logon
    4624, 4625, 4634, 4648, 4672, 4768, 4769, 4776, 4740,

    # Process lifecycle
    4688, 4689,

    # Account & group changes
    4720, 4722, 4725, 4728, 4732, 4735,

    # Services
    7000, 7001, 7009, 7011, 7022, 7023, 7024, 7026, 7031, 7034, 7035, 7036, 7040, 7045,

    # Boot / shutdown / system
    12, 13,
    6005, 6006, 6008, 6009, 6011, 6013,

    # Setup
    2004, 2005,

    # PowerShell
    4100, 4103, 4104,
}

# -----------------------------
# XML helpers
# -----------------------------
def _get_nsmap(root: ET.Element) -> Dict[str, str]:
    if root.tag.startswith("{"):
        uri = root.tag.split("}")[0].strip("{")
        return {"e": uri}
    return {}


def _get_child(parent: ET.Element, tag: str, ns: Dict[str, str]) -> Optional[ET.Element]:
    if ns:
        el = parent.find(f"e:{tag}", ns)
        if el is not None:
            return el
    return parent.find(tag)


def _get_children(parent: ET.Element, tag: str, ns: Dict[str, str]) -> List[ET.Element]:
    if ns:
        els = parent.findall(f"e:{tag}", ns)
        if els:
            return els
    return parent.findall(tag)

# -----------------------------
# EVTX iteration
# -----------------------------
def iter_evtx_events(evtx_path: str) -> Generator[Dict[str, Any], None, None]:
    with Evtx(evtx_path) as log:
        for record in log.records():
            try:
                root = ET.fromstring(record.xml())
            except Exception:
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
            except Exception:
                continue

            if event_id not in INTERESTING_EVENT_IDS:
                continue

            time_el = _get_child(system, "TimeCreated", ns)
            timestamp = time_el.get("SystemTime") if time_el is not None else None

            computer_el = _get_child(system, "Computer", ns)
            computer = computer_el.text.strip() if computer_el is not None and computer_el.text else None

            channel_el = _get_child(system, "Channel", ns)
            channel = channel_el.text.strip() if channel_el is not None and channel_el.text else None

            data: Dict[str, Any] = {}
            event_data_el = _get_child(root, "EventData", ns)
            if event_data_el is not None:
                for d in _get_children(event_data_el, "Data", ns):
                    name = d.get("Name") or "data"
                    value = (d.text or "").strip()
                    data[name] = value

            try:
                rec_no = record.record_num()
            except Exception:
                rec_no = None

            yield {
                "record_number": rec_no,
                "event_id": event_id,
                "timestamp": timestamp,
                "computer": computer,
                "channel": channel,
                "data": data,
            }

# -----------------------------
# Text formatting for embeddings
# -----------------------------
def format_event_for_text(event: Dict[str, Any]) -> str:
    ts = event.get("timestamp") or "UNKNOWN_TIME"
    eid = event.get("event_id")
    rec = event.get("record_number")
    comp = event.get("computer") or ""
    channel = (event.get("channel") or "").lower()
    data = event.get("data") or {}

    tags = []

    if "security" in channel:
        tags.append("ChannelTag=security Category=authentication")
    if "system" in channel:
        tags.append("ChannelTag=system Category=system")
    if "setup" in channel:
        tags.append("ChannelTag=setup Category=setup")
    if "powershell" in channel:
        tags.append("ChannelTag=powershell Category=scripting")

    if eid in {7040, 7045}:
        tags.append("Category=service")
    if eid in {4625}:
        tags.append("Category=failed_logon")
    if eid in {4624}:
        tags.append("Category=successful_logon")

    clean = []
    for k, v in data.items():
        if v:
            s = str(v).replace("\n", " ").replace("\r", " ")
            clean.append(f"{k}={s}")

    kv = " ".join(clean[:12])
    tag_str = " ".join(tags)

    return f"[{ts}] EventID={eid} Record={rec} Computer={comp} Channel={channel} {tag_str} {kv}".strip()

# -----------------------------
# Derivative writer
# -----------------------------
def generate_evtx_derivatives(evtx_path: str, case_dir: str) -> Dict[str, Any]:
    os.makedirs(case_dir, exist_ok=True)

    base = os.path.splitext(os.path.basename(evtx_path))[0]
    evtx_out_dir = os.path.join(case_dir, "artifacts", "evtx")
    os.makedirs(evtx_out_dir, exist_ok=True)

    jsonl_path = os.path.join(evtx_out_dir, f"{base}.jsonl")
    txt_path = os.path.join(evtx_out_dir, f"{base}.txt")

    events_count = 0

    with open(jsonl_path, "w", encoding="utf-8") as jf, open(txt_path, "w", encoding="utf-8") as tf:
        for event in iter_evtx_events(evtx_path):
            events_count += 1
            jf.write(json.dumps(event, ensure_ascii=False) + "\n")
            tf.write(format_event_for_text(event) + "\n")

    return {
        "events_count": events_count,
        "jsonl_path": jsonl_path,
        "txt_path": txt_path,
    }
