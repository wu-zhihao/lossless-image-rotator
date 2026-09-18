Set fso = CreateObject("Scripting.FileSystemObject")
scriptPath = WScript.ScriptFullName
scriptDir = fso.GetParentFolderName(scriptPath)
pyScript = scriptDir & "\tiff_rotator.py"
pythonw = "pythonw"

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = scriptDir
shell.Run """" & pythonw & """ """ & pyScript & """", 0, False
