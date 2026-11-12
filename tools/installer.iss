; 安装包脚本（Inno Setup 6）。由 tools/build_installer.py 调用，版本号从 core/version.py 传进来。
; 手动编译：ISCC.exe /DMyAppVersion=5.0.37 /DMyAppVersionNumeric=5.0.37.0 tools\installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MyAppVersionNumeric
  #define MyAppVersionNumeric "0.0.0.0"
#endif

#define MyAppName "锋芒直播录屏"
#define MyAppExeName "锋芒直播录屏.exe"
#define MyAppMutex "FengmangLiveScreen.SingleInstance.v1"

[Setup]
AppId={{DD6B0590-26D9-47BA-865F-E86DD3EC28EC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AppMutex={#MyAppMutex}
SetupIconFile=..\ui\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
OutputDir=..\dist
OutputBaseFilename={#MyAppName}-{#MyAppVersion}-安装包
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
VersionInfoVersion={#MyAppVersionNumeric}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} 安装程序

[Languages]
Name: "zh"; MessagesFile: "{#SourcePath}\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："

[Files]
Source: "..\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
