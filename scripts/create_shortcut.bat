@echo off
chcp 65001 >nul
powershell -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$sc = $ws.CreateShortcut('%USERPROFILE%\Desktop\lingShu.lnk');" ^
  "$sc.TargetPath = 'C:\Users\zwq\agent-harness\dist\lingShu\lingShu.exe';" ^
  "$sc.WorkingDirectory = 'C:\Users\zwq\agent-harness\dist\lingShu';" ^
  "$sc.Description = 'LingShu Agent';" ^
  "$sc.IconLocation = 'C:\Users\zwq\agent-harness\scripts\icon.ico';" ^
  "$sc.Save();"
echo Desktop shortcut created: %%USERPROFILE%%\Desktop\lingShu.lnk
pause
