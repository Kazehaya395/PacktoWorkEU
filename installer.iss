;
; PacktoWorkEU Inno Setup Installer Script
; https://jrsoftware.org/isinfo.php
;
; Production-ready per-user installer:
; - stable AppId under per-user LOCALAPPDATA\Programs\PacktoWorkEU
; - no duplicate uninstall registry entry
; - path-scoped running-instance check (never force-kills processes by name)
; - safe per-user data cleanup via --purge-user-data on uninstall
;

#define MyAppName      "PacktoWorkEU"
#ifndef MyAppVersion
  #define MyAppVersion "0.3.0"
#endif
#ifndef InstallerSourceDir
  #define InstallerSourceDir ".build\windows\installer-src"
#endif
#define MyAppPublisher "PacktoWorkEU"
#define MyAppURL       "https://github.com/Kazehaya395/PacktoWorkEU"
#define MyAppExeName   "PacktoWorkEU.exe"
#define MyAppIcoName   "packtoworkeu.ico"
#define MyAppDataDirName "PacktoWorkEU"

[Setup]
AppId=PacktoWorkEU
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=auto
LicenseFile=LICENSE
OutputDir=dist
OutputBaseFilename=packtoworkeu-{#MyAppVersion}-setup
SetupIconFile=packtoworkeu\data\packtoworkeu.ico
WizardImageFile=packtoworkeu\data\packtoworkeu.png
WizardSmallImageFile=packtoworkeu\data\packtoworkeu.png
UninstallDisplayIcon={app}\{#MyAppIcoName}
UninstallDisplayName={#MyAppName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
Compression=lzma2/ultra64
SolidCompression=yes
ShowTasksTreeLines=yes
DisableFinishedPage=no
CloseApplications=no
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
; With --onedir PyInstaller build, the verified staged payload is packaged cleanly.
Source: "{#InstallerSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcoName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcoName}"; Tasks: desktopicon

; No global PLAYWRIGHT_BROWSERS_PATH or personal-browser registry changes.

[Tasks]
Name: "desktopicon"; Description: "Create Desktop Shortcut"; GroupDescription: "Additional Icons"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; Inno removes tracked installed files only; never recursively erase {app}.

[Code]
function InstalledAppRunning(): Boolean;
var
  WbemLocator, WbemServices, Processes, ProcessItem: Variant;
  TargetPath, ProcessPath: String;
  I: Integer;
begin
  Result := False;
  TargetPath := ExpandConstant('{app}\{#MyAppExeName}');
  if not FileExists(TargetPath) then
    Exit;
  try
    WbemLocator := CreateOleObject('WbemScripting.SWbemLocator');
    WbemServices := WbemLocator.ConnectServer('', 'root\CIMV2');
    Processes := WbemServices.ExecQuery('SELECT ExecutablePath FROM Win32_Process WHERE Name = ''{#MyAppExeName}''');
    for I := 0 to Processes.Count - 1 do
    begin
      ProcessItem := Processes.ItemIndex(I);
      if VarIsNull(ProcessItem.ExecutablePath) or VarIsEmpty(ProcessItem.ExecutablePath) then
      begin
        Result := True;
        Exit;
      end;
      ProcessPath := String(ProcessItem.ExecutablePath);
      if CompareText(ProcessPath, ExpandConstant('{app}\{#MyAppExeName}')) = 0 then
      begin
        Result := True;
        Exit;
      end;
    end;
  except
    Result := True;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := False;
  if RegKeyExists(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\PacktoWorkEU_is1') or
     RegKeyExists(HKLM32, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\PacktoWorkEU_is1') then
  begin
    MsgBox('An older machine-wide PacktoWorkEU installation exists. This per-user installer cannot upgrade it safely. Back up your PacktoWorkEU data and custom paths before removing the old version, then install and restore your backup. Installation cancelled; no user data was changed.', mbInformation, MB_OK);
    Exit;
  end;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if InstalledAppRunning() then
    Result := 'Close the installed PacktoWorkEU application before upgrading or reinstalling, then click Retry.';
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  // usUninstall is AFTER confirmation, BEFORE installed files are removed.
  // Exceptions at this step abort uninstall, keeping the helper for retry.
  if CurUninstallStep <> usUninstall then
    Exit;
  if InstalledAppRunning() then
    RaiseException('Close the installed PacktoWorkEU application before uninstalling, then retry.');
  if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--purge-user-data', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    RaiseException('Could not run PacktoWorkEU data cleanup. Uninstall cancelled. Restore the installed application and retry.');
  if ResultCode <> 0 then
    RaiseException('PacktoWorkEU data cleanup was incomplete. Uninstall cancelled. Close active processes, review data permissions and cleanup-errors.json in the PacktoWorkEU profile folder if present, then retry.');
end;
