Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "wsl.exe -d Ubuntu-24.04 -u root -- sleep infinity", 0, False
