#ifndef AppVersion
  #define AppVersion "0.1.3"
#endif

[Setup]
AppId={{B07FD976-AEA4-42A2-B6CC-64EA0EA06A30}
AppName=簡譜工房 Jianpu Studio
AppVersion={#AppVersion}
AppPublisher=Jianpu Studio contributors
AppPublisherURL=https://github.com/miles990/jianpu-studio
AppSupportURL=https://github.com/miles990/jianpu-studio/issues
DefaultDirName={autopf}\JianpuStudio
DefaultGroupName=簡譜工房
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
DisableDirPage=no
AllowUNCPath=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.18362
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
Name: "traditionalchinese"; MessagesFile: "ChineseTraditional.isl"
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
Root: HKA; Subkey: "Software\Classes\.jps\OpenWithProgids"; ValueType: string; ValueName: "JianpuStudio.Project"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\JianpuStudio.Project"; ValueType: string; ValueName: ""; ValueData: "簡譜工房專案"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\JianpuStudio.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\JianpuStudio.exe,0"
Root: HKA; Subkey: "Software\Classes\JianpuStudio.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\JianpuStudio.exe"" ""%1"""

[Run]
Filename: "{app}\JianpuStudio.exe"; Description: "開啟簡譜工房"; Flags: nowait postinstall skipifsilent
Filename: "{app}\JianpuStudio.exe"; Flags: nowait runasoriginaluser; Check: UpdateRestartRequested

[Code]
function UpdateRestartRequested: Boolean;
begin
  Result := ExpandConstant('{param:JIANPURESTART|0}') = '1';
end;

function ProtectedTarget: Boolean;
var
  Target: String;
begin
  Target := Lowercase(AddBackslash(ExpandFileName(WizardDirValue)));
  Result := (Pos(Lowercase(AddBackslash(ExpandConstant('{commonpf}'))), Target) = 1) or
            (Pos(Lowercase(AddBackslash(ExpandConstant('{commonpf32}'))), Target) = 1) or
            (Pos(Lowercase(AddBackslash(ExpandConstant('{win}'))), Target) = 1);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and (not IsAdminInstallMode) and ProtectedTarget then
  begin
    if WizardSilent then
    begin
      Log('Protected installation path requires /ALLUSERS.');
      Result := False;
      Exit;
    end;
    if MsgBox('此目錄需要系統管理員權限。要改用建議的個人安裝資料夾嗎？' + #13#10 + #13#10 +
      '若要安裝到 Program Files，請取消並重新啟動安裝程式，選擇「為所有使用者安裝」。',
      mbConfirmation, MB_YESNO) = IDYES then
      WizardForm.DirEdit.Text := ExpandConstant('{userpf}\JianpuStudio')
    else
      Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if (not IsAdminInstallMode) and ProtectedTarget then
    Result := '此目錄需要系統管理員權限。請使用個人安裝資料夾，或重新選擇「為所有使用者安裝」。';
end;
