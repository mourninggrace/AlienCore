Set oShell = CreateObject("WScript.Shell")
oShell.CurrentDirectory = "C:\aliencore"
oShell.Run """C:\Users\strin\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"" ""C:\aliencore\aliencore.py"" --no-elevate", 0, False
