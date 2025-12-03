# test_evtx.py
import os
from api.evtx_parser import generate_evtx_derivatives

def main():
    # UPDATE THIS PATH to your real .evtx file
    evtx_path = r"C:\temp\test_security.evtx"

    # This is where we'll store the parsed output
    case_dir = r"C:\temp\test_case_evtx"

    os.makedirs(case_dir, exist_ok=True)

    print(f"[+] Parsing EVTX: {evtx_path}")
    stats = generate_evtx_derivatives(evtx_path, case_dir)

    print("[+] Done.")
    print(f"Events parsed : {stats['events_count']}")
    print(f"JSONL output  : {stats['jsonl_path']}")
    print(f"Text output   : {stats['txt_path']}")

if __name__ == "__main__":
    main()
