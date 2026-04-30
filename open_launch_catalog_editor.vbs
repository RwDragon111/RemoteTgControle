Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectRoot = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwExe = projectRoot & "\.venv\Scripts\pythonw.exe"
scriptPath = projectRoot & "\launch_catalog_editor.py"
shell.Run Chr(34) & pythonwExe & Chr(34) & " " & Chr(34) & scriptPath & Chr(34), 0, False
