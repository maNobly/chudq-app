; chudQ's installer: a per-user install (no admin), a Start menu entry, starting with Windows in
; the tray, and an uninstaller. build.py passes Version, Dist (PyInstaller's folder) and Icon.
; The login and settings are the user's, so uninstalling leaves %LOCALAPPDATA%\ChudQ.

#define Name "chudQ"
#define Exe "chudQ.exe"

[Setup]
AppId={{9C3E5A71-2F4B-4D8E-B6A1-7E0D2C4F8B13}
AppName={#Name}
AppVersion={#Version}
AppPublisher=chudq.lol
AppPublisherURL=https://chudq.lol
DefaultDirName={localappdata}\Programs\chudQ
DefaultGroupName={#Name}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=ChudQ-Setup-{#Version}
SetupIconFile={#Icon}
UninstallDisplayIcon={app}\{#Exe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Files]
Source: "{#Dist}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\{#Name}"; Filename: "{app}\{#Exe}"
Name: "{userdesktop}\{#Name}"; Filename: "{app}\{#Exe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "A desktop shortcut"; Flags: unchecked

[Registry]
; Start in the tray at logon (the app's window can turn it off; it keeps this value itself)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ChudQ"; ValueData: """{app}\{#Exe}"" --hidden"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#Exe}"; Description: "Open {#Name}"; Flags: nowait postinstall skipifsilent
; An update from inside the app runs silently: the new version opens again by itself
Filename: "{app}\{#Exe}"; Parameters: "--updated"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im {#Exe}"; Flags: runhidden; RunOnceId: "StopApp"
