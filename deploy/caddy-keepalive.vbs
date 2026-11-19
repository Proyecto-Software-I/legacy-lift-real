Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """F:\ServidorCasa\Caddy\caddy.exe"" run --config ""F:\Docs\uni\proyecto\legacy-lift-real\deploy\Caddyfile.example""", 0, False
