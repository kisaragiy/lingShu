@echo off
chcp 65001 >nul
powershell -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$sc = $ws.CreateShortcut('%USERPROFILE%\Desktop\lingShu.lnk');" ^
  "$sc.TargetPath = 'C:\Users\zwq\agent-harness\dist\lingShu\lingShu.exe';" ^
  "$sc.WorkingDirectory = 'C:\Users\zwq\agent-harness\dist\lingShu';" ^
  "$sc.Description = '灵枢 — AI 调研助手｜64秒出竞品报告';" ^
  "$sc.IconLocation = 'C:\Users\zwq\agent-harness\scripts\icon.ico';" ^
  "$sc.Save();"
echo Desktop shortcut created: %%USERPROFILE%%\Desktop\lingShu.lnk
pause
