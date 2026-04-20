Set oShell = CreateObject("WScript.Shell")
oShell.CurrentDirectory = "C:\aliencore"
oShell.Run """pythonw.exe"" ""C:\aliencore\aliencore.py""", 0, False
