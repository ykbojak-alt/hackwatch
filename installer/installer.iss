; Hackathon Watch — Inno Setup 6 installer (REL-1)
; Testnet-only build: no NEAR keys, no secrets shipped.
; Source exe: D:/hackwatch/dist/HackathonWatch.exe
; Output:     D:/hackwatch/installer/HackathonWatch-Setup-0.2.0.exe

#define MyAppName "Hackathon Watch"
#define MyAppVersion "0.2.2"
#define MyAppPublisher "NEXUS AiOS"
#define MyAppExeName "HackathonWatch.exe"
#define MyAppURL "https://near.org"

[Setup]
; NOTE: AppId is a fixed GUID for this app — do not change on minor updates
AppId={{9F2C6A51-3E8B-4B2F-B6D4-1D7A55C0E211}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\Hackathon Watch
DefaultGroupName={#MyAppName}
DisableDirPage=auto
OutputDir=D:\hackwatch\installer
OutputBaseFilename=HackathonWatch-Setup-{#MyAppVersion}
SetupIconFile=D:\hackwatch\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; per-user (no admin) install
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0
; exe starts a local server on a random free port — no firewall prompt needed (loopback only)

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Portable exe: serves ui.html + reads hackwatch*.json from %APPDATA%\Hackwatch
Source: "D:\hackwatch\dist\HackathonWatch.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Hackathon Watch"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Hackathon Watch"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; leave user data in %APPDATA%\Hackwatch on uninstall — data belongs to the user
Type: filesandordirs; Name: "{app}"
