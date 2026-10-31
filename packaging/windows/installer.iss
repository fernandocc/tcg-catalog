; Windows installer (Inno Setup 6). Built by .github/workflows/build.yml after PyInstaller:
;   iscc /DMyAppVersion=1.2.3 packaging\windows\installer.iss
; Installs for the current user only (no admin prompt), adds Start-menu + desktop shortcuts
; and an uninstaller. The collection lives in Documents\TCG Catalog and is never touched by
; installing, updating or uninstalling.
#define MyAppName "TCG Catalog"
#define MyAppExe "TCG Catalog.exe"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{6E0B7C1F-3D52-4B7E-9A2C-5F1E8D4C2A90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=TCG Catalog
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=TCG-Catalog-{#MyAppVersion}-Windows-Setup
SetupIconFile=..\..\build\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\TCG Catalog\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; drop files from an older version before copying the new one
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Open {#MyAppName} now"; Flags: nowait postinstall skipifsilent
