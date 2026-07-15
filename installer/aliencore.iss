; AlienCore Inno Setup script.
;
; Compile from project root with:
;     iscc installer\aliencore.iss
;
; Or via the build wrapper which runs PyInstaller first:
;     python installer/build.py
;
; Output:
;     dist/AlienCore-{version}-Setup.exe

#define MyAppName        "AlienCore"
#define MyAppVersion     "1.1.0"
#define MyAppPublisher   "George Kyle Yeroshefsky"
#define MyAppURL         "https://github.com/mourninggrace/AlienCore"
#define MyAppExeName     "AlienCore.exe"
#define ProjectRoot      "..\"
#define DistRoot         "..\dist\AlienCore"


[Setup]
; AppId is a stable GUID — keep this exactly the same across releases so
; new installer versions UPGRADE the existing install instead of
; producing a parallel "AlienCore (1)" entry in Programs and Features.
; Generated 2026-05-02 specifically for AlienCore.  Never reuse for
; another product; never change for AlienCore.
AppId={{B12E4F8C-7A3D-4E1B-9C5A-2F08AB7C9E14}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile={#ProjectRoot}LICENSE
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ProjectRoot}dist
OutputBaseFilename=AlienCore-{#MyAppVersion}-Setup
SetupIconFile={#ProjectRoot}assets\icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
MinVersion=10.0.19041
CloseApplications=yes


[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"


[Messages]
; Welcome page copy — explains the unsigned-installer SmartScreen experience.
WelcomeLabel2=This will install [name/ver] on your computer.%n%n%nNOTE about Windows SmartScreen:%n%nAlienCore is currently distributed unsigned. Windows may have shown a "Windows protected your PC" warning when you ran this installer — that's expected for unsigned software from a small publisher. AlienCore is safe; the installer's SHA-256 hash is published in the GitHub release notes if you want to verify it independently.%n%n%nIt is recommended that you close all other applications before continuing.


[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce


[Files]
; PyInstaller onedir output (AlienCore.exe + _internal/) — the bulk.
Source: "{#DistRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; lhm_bridge .NET subprocess — the sensor backend.  Must land at
; {app}\tools\lhm_bridge\dist\ because core/lhm_manager.py resolves it
; via BASE_DIR-relative path.
Source: "{#ProjectRoot}tools\lhm_bridge\dist\*"; DestDir: "{app}\tools\lhm_bridge\dist"; Flags: ignoreversion recursesubdirs

; Good-citizen reference docs in the install dir
Source: "{#ProjectRoot}LICENSE";       DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}CHANGELOG.md";  DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}README.md";     DestDir: "{app}"; Flags: ignoreversion


[Icons]
; Start Menu — main entry
Name: "{group}\{#MyAppName}";          Filename: "{app}\{#MyAppExeName}"
; Uninstall entry in the Start Menu group
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Optional desktop shortcut, controlled by the Tasks checkbox
Name: "{autodesktop}\{#MyAppName}";    Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon


[Run]
; Final-page checkbox: launch AlienCore now (default checked).  postinstall
; means "show as a checkbox at the end of the wizard"; nowait so the
; installer process exits immediately after spawning AlienCore.exe.
;
; runascurrentuser keeps Inno's elevated token for the spawned process —
; AlienCore.exe carries requireAdministrator in its manifest, and without
; this flag CreateProcess fails with code 740 ("requires elevation") because
; Inno's [Run] step otherwise drops back to the original user's
; non-elevated identity.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent runascurrentuser


[UninstallRun]
; AlienCore registers an elevated logon task (AlienCoreElevatedStartup) the
; first time the user enables "Start with Windows" so the app can come up
; with admin rights without a UAC prompt at every login.  The Inno [Files]
; section can't see it (it's a Task Scheduler entry, not a file), so the
; default uninstall leaves the task pointing at a now-missing AlienCore.exe.
; Wipe it on uninstall.  runhidden so no console window flashes;
; skipifdoesntexist tolerates the case where the user never enabled
; auto-start.
Filename: "schtasks.exe"; Parameters: "/Delete /TN AlienCoreElevatedStartup /F"; Flags: runhidden skipifdoesntexist; RunOnceId: "DelElevatedTask"


[UninstallDelete]
; User state (config.json, logs/, hardware_profile.json, update_state.json)
; lives in %LOCALAPPDATA%\AlienCore\, which uninstall does NOT touch — a
; reinstall picks up the existing config and logs.  Everything below is
; bundled program data and is safe to remove.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\docs"
Type: files;          Name: "{app}\AlienCore.exe"
Type: files;          Name: "{app}\LICENSE"
Type: files;          Name: "{app}\CHANGELOG.md"
Type: files;          Name: "{app}\README.md"
