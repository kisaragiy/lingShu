$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut("$env:USERPROFILE\Desktop\灵枢.lnk")
$sc.TargetPath = "C:\Users\zwq\agent-harness\dist\lingShu\lingShu.exe"
$sc.WorkingDirectory = "C:\Users\zwq\agent-harness\dist\lingShu"
$sc.Description = "灵枢 — LingShu Agent"
$sc.IconLocation = "C:\Users\zwq\agent-harness\scripts\icon.ico"
$sc.Save()
Write-Host "✅ 桌面快捷方式已创建"
