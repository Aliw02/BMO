' Run NOVA in background silently (no console window)
' Double-click this to start, it will run until 9 AM then exit

Dim shell, scriptPath
scriptPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\nova_9am_auto.py"
Set shell = CreateObject("WScript.Shell")
shell.Run "pythonw """ & scriptPath & """", 0, False
Set shell = Nothing
