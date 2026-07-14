; SRAN Desktop Installer — Inno Setup 6.x
; Prereq: publish first ->
;   dotnet publish src/SranDesktop.App/SranDesktop.App.csproj -c Release -p:PublishProfile=win-x64
; Build:  ISCC.exe build\SranDesktop.iss

#define MyAppName "SRAN Desktop"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SRAN"
#define MyAppURL "https://sran.ai"
#define MyAppExeName "SranDesktop.exe"

[Setup]
AppId={{B8A2E5F3-7C41-4D8B-9F6A-2E3D4C5B6A7B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\installer
OutputBaseFilename=SranDesktop-{#MyAppVersion}-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Per-user install (no admin needed); user may elevate for all-users via the dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=..\src\SranDesktop.App\Assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "Start with Windows"; GroupDescription: "Additional options:"

[Files]
; Single-file self-contained publish output.
Source: "..\publish\win-x64\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Any loose companions the single-file host emits (native libs, config).
Source: "..\publish\win-x64\*.dll"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\publish\win-x64\*.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Registry]
; Auto-start (per-user) when the "Start with Windows" task is selected.
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#MyAppName}"; \
  ValueData: """{app}\{#MyAppExeName}"" --minimized"; \
  Tasks: startupicon; Flags: uninsdeletevalue

; sran:// URI scheme (Phase 7 C1) — per-user, no elevation. The OAuth
; broker's callback page bounces the browser to sran://oauth/callback,
; which Windows routes to this exe (the running instance receives it
; over the single-instance pipe). Removed on uninstall.
Root: HKCU; Subkey: "Software\Classes\sran"; \
  ValueType: string; ValueData: "URL:SRAN Desktop Protocol"; \
  Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\sran"; \
  ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\sran\DefaultIcon"; \
  ValueType: string; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCU; Subkey: "Software\Classes\sran\shell\open\command"; \
  ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[UninstallDelete]
Type: dirifempty; Name: "{app}"

[Code]
// Terminate any running instance before install/uninstall so files aren't locked.
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec('taskkill.exe', '/F /IM {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
