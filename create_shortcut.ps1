$s = New-Object -ComObject WScript.Shell
$link = $s.CreateShortcut("$env:USERPROFILE\Desktop\LingShu.lnk")
$link.TargetPath = "$env:USERPROFILE\agent-harness\start_lingshu.bat"
$link.WorkingDirectory = "$env:USERPROFILE\agent-harness"
$link.Description = "LingShu Agent - AI Research Assistant"
$link.Save()
Write-Host "Shortcut created on Desktop"
