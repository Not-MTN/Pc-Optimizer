; Keel — Inno Setup 6 script
;
;   iscc packaging\keel.iss
;
; Per-user install into %LOCALAPPDATA%\Programs\Keel:
;   * no UAC prompt, so the app installs on a locked-down or school laptop
;   * no admin rights means it can never touch another user's settings
;   * the uninstaller is a normal entry in Settings → Apps
;
; Two decisions worth stating, because they are the opposite of what most
; installers do:
;   1. A running Keel is closed *gracefully* before upgrade, not overwritten.
;      Overwriting the exe of a live process is how you corrupt a SQLite journal.
;   2. Uninstalling keeps %LOCALAPPDATA%\Keel (journal, settings) unless the user
;      ticks the box. Deleting someone's undo history on an uninstall is data
;      loss they did not ask for.

#define AppName "Keel"
#define AppVersion "0.5.0"
#define AppPublisher "Keel"
#define AppExe "Keel.exe"
#define AppUrl "https://github.com/Not-MTN/Pc-Optimizer"

[Setup]
AppId={{7C4A1E2B-9D3F-4A55-8B7E-2F6C1D0A9E31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=Keel-{#AppVersion}-Setup
SetupIconFile=..\assets\keel.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
; A 12 MB app should not take longer to install than it takes to double-click.
; (lzma2/max is still the compression, so the download is smaller than the exe.)
DisableWelcomePage=no
InfoBeforeFile=..\packaging\before-install.txt
ChangesEnvironment=no
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupitem"; Description: "Start Keel when I sign in (it stays idle at 0% CPU)"; \
    GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\README-first-run.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\keel.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "Keel"; ValueData: """{app}\{#AppExe}"""; \
    Flags: uninsdeletevalue; Tasks: startupitem

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only the install directory is ours to remove. Data is opt-in, below.
Type: filesandordirs; Name: "{app}"

[Code]
var
  DeleteData: Boolean;

function InitializeUninstall(): Boolean;
var
  DataDir: String;
begin
  DataDir := ExpandConstant('{localappdata}\Keel');
  DeleteData := False;
  if DirExists(DataDir) then
    DeleteData := MsgBox('Also delete your Keel journal and settings?' + #13#10 +
      'Keeping them means reinstalling picks up exactly where you left off, ' +
      'including the undo history.', mbConfirmation, MB_YESNO) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DeleteData then
    DelTree(ExpandConstant('{localappdata}\Keel'), True, True, True);
end;

; Close a running Keel before replacing its exe. Inno's CloseApplications would
; do this too, but not with a graceful window close, and Keel's journal wants one.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM {#AppExe} /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(600);
  Result := '';
end;
