Option Explicit
Dim sh, fso, appDir, pythonw, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = appDir & "\runtime\pythonw.exe"

If Not fso.FileExists(pythonw) Then
  MsgBox "Не найден встроенный Python Runtime Parts Manager." & vbCrLf & vbCrLf & _
         "Переустановите программу или обратитесь в поддержку.", 16, "Parts Manager"
  WScript.Quit 2
End If

sh.CurrentDirectory = appDir
cmd = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & appDir & "\parts_manager.py" & Chr(34)
sh.Run cmd, 0, False
