Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectRoot = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = projectRoot & "\.venv\Scripts\python.exe"
scriptPath = projectRoot & "\telegram_pc_bot.py"
shell.Run Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & scriptPath & Chr(34), 0, False
