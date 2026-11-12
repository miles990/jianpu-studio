#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{B07FD976-AEA4-42A2-B6CC-64EA0EA06A30}
AppName=簡譜工房 Jianpu Studio
AppVersion={#AppVersion}
AppPublisher=Jianpu Studio contributors
AppPublisherURL=https://github.com/miles990/jianpu-studio
AppSupportURL=https://github.com/miles990/jianpu-studio/issues
DefaultDirName={localappdata}\Programs\JianpuStudio
DefaultGroupName=簡譜工房
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=JianpuStudio-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src\jianpu_studio\assets\icon.ico
UninstallDisplayIcon={app}\JianpuStudio.exe
LicenseFile=..\LICENSE
InfoBeforeFile=install-info.txt
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "traditionalchinese"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; Flags: checkedonce

[Files]
Source: "..\dist\JianpuStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\簡譜工房"; Filename: "{app}\JianpuStudio.exe"
Name: "{autodesktop}\簡譜工房"; Filename: "{app}\JianpuStudio.exe"; Tasks: desktopicon
Name: "{group}\使用說明"; Filename: "{app}\docs\user-guide.html"

[Registry]
Root: HKCU; Subkey: "Software\Classes\.jps\OpenWithProgids"; ValueType: string; ValueName: "JianpuStudio.Project"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\JianpuStudio.Project"; ValueType: string; ValueName: ""; ValueData: "簡譜工房專案"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\JianpuStudio.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\JianpuStudio.exe,0"
Root: HKCU; Subkey: "Software\Classes\JianpuStudio.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\JianpuStudio.exe"" ""%1"""

[Run]
Filename: "{app}\JianpuStudio.exe"; Description: "開啟簡譜工房"; Flags: nowait postinstall skipifsilent
