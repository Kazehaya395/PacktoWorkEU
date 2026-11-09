;
; SponsorScout Inno Setup Installer Script
; https://jrsoftware.org/isinfo.php
;
; Production-ready installer:
; - stable AppId
; - no duplicate uninstall registry entry
; - force-close running app during uninstall/install
; - remove AppData user-data folder on uninstall
; - UninstallRun kills SponsorScout.exe before file deletion
;

#define MyAppName      "SponsorScout"
#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif
#define MyAppPublisher "SponsorScout"
#define MyAppURL       "https://github.com/Kazehaya395/SponsorScout"
#define MyAppExeName   "SponsorScout.exe"
#define MyAppIcoName   "sponsorscout.ico"
#define MyAppDataDirName "SponsorScout"

[Setup]
AppId=SponsorScout
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
OutputBaseFilename=sponsorscout-{#MyAppVersion}-setup
SetupIconFile=sponsorscout\data\sponsorscout.ico
WizardImageFile=sponsorscout\data\sponsorscout.png
WizardSmallImageFile=sponsorscout\data\sponsorscout.png
UninstallDisplayIcon={app}\{#MyAppIcoName}
UninstallDisplayName={#MyAppName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
Compression=lzma2/ultra64
SolidCompression=yes
ShowTasksTreeLines=yes
DisableFinishedPage=no

; Detect a running instance before installing over it.
;
; NOTE: there is deliberately NO AppMutex= entry. Inno's CloseApplications
; detects the app through its top-level window, which is enough here, and an
; AppMutex naming a mutex the app never creates (it only sets an AppUserModelID
; via SetCurrentProcessExplicitAppUserModelID) would advertise a guarantee the
; app does not provide. If a real single-instance mutex is ever added to
; main.py, add the matching AppMutex line here.
;
; Force-close the app during both install and uninstall
CloseApplications=force
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
; With --onedir PyInstaller build, the entire build directory is copied.
; Recurse into subdirs and create all subdirectories at install time.
Source: "dist\SponsorScout-InstallerFiles\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcoName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIcoName}"; Tasks: desktopicon

; No global PLAYWRIGHT_BROWSERS_PATH or personal-browser registry changes.

[Tasks]
Name: "desktopicon"; Description: "Create Desktop Shortcut"; GroupDescription: "Additional Icons"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

; Kill SponsorScout.exe BEFORE uninstall file deletion begins.
; This ensures the main exe is not locked and can be fully removed.
; Inno removes tracked installed files only; never recursively erase {app}.

[Code]
function IsUserAnAdmin: Integer;
  external 'IsUserAnAdmin@shell32.dll stdcall';

function InitializeSetup(): Boolean;
begin
  Result := False;
  if IsUserAnAdmin <> 0 then
  begin
    MsgBox('Run this per-user installer normally, not as administrator.', mbError, MB_OK);
    Exit;
  end;
  if RegKeyExists(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\SponsorScout_is1') or
     RegKeyExists(HKLM32, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\SponsorScout_is1') then
  begin
    MsgBox('An older machine-wide SponsorScout installation exists. This per-user installer cannot upgrade it safely. Back up your SponsorScout data and custom paths before removing the old version, then install and restore your backup. Installation cancelled; no user data was changed.', mbInformation, MB_OK);
    Exit;
  end;
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  Result := IsUserAnAdmin = 0;
  if not Result then
    MsgBox('Run this per-user uninstaller normally, not as administrator. Custom user paths must not be processed with elevated privileges.', mbError, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  // usUninstall is AFTER confirmation, BEFORE installed files are removed.
  // Exceptions at this step abort uninstall, keeping the helper for retry.
  if CurUninstallStep <> usUninstall then
    Exit;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im SponsorScout.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--purge-user-data', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    RaiseException('Could not run SponsorScout data cleanup. Uninstall cancelled. Restore the installed application and retry.');
  if ResultCode <> 0 then
    RaiseException('SponsorScout data cleanup was incomplete. Uninstall cancelled. Close active processes, review data permissions and cleanup-errors.json in the SponsorScout profile folder if present, then retry.');
end;
