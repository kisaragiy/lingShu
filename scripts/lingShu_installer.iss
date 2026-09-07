; 灵枢 (LingShu) — Inno Setup 安装脚本
; 编译: ISCC.exe scripts/lingShu_installer.iss
; 依赖: dist/lingShu/ (PyInstaller 输出目录)

#define MyAppName "灵枢 AI"
#define MyAppNameEn "LingShu AI"
#define MyAppVersion "0.75.2"
#define MyAppPublisher "kisaragiy"
#define MyAppExeName "lingShu.exe"
#define MyAppAssocName "灵枢 会话"
#define MyAppAssocExt ".lss"
#define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt

[Setup]
AppId={{8B4E5C2A-6D3F-4A91-9E2B-4C0A55E62026}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 免 UAC per-user 安装，默认目录用 {localappdata}\Programs（VS Code 模式）
DefaultDirName={localappdata}\Programs\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; pyproject.toml 声明 MIT，但仓库无 LICENSE 文件——跳过许可页，避免编译失败
; LicenseFile=..\LICENSE.txt
OutputDir=..\dist
OutputBaseFilename=LingShu-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 免 UAC：per-user 安装（VS Code/Discord 模式），大众用户双击即装
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 中文界面
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=no
; 卸载时删除数据
UninstallDisplayIcon={app}\{#MyAppExeName}
; 图标（从 exe 提取，若无则用默认）
SetupIconFile=icon.ico

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl,compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller 输出全部文件
Source: "..\dist\lingShu\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 确保 exe 在根
Source: "..\dist\lingShu\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 文件关联（可选保留，先注释避免误伤）
; Root: HKA; Subkey: "Software\Classes\{#MyAppAssocExt}\OpenWithProgids"; ValueType: string; ValueName: "{#MyAppAssocKey}"; ValueData: ""; Flags: uninsdeletevalue
; Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppAssocName}"; Flags: uninsdeletekey
; Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
; Root: HKA; Subkey: "Software\Classes\{#MyAppAssocKey}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
; 安装完成后启动（勾选）
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 清理运行时生成的数据目录（窗口配置等），保留用户数据
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\webview_data"
Type: filesandordirs; Name: "{app}\window_config.json"
