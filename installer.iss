; Parts Manager — standalone Windows installer
; Requires Inno Setup 6.x for compilation.

#define MyAppName "Parts Manager"
#define MyAppVersion "12.6.0"
#define MyAppPublisher "Parts Manager"

[Setup]
AppId={{C7D5B7F5-5C7D-4D5A-8E18-0C3D6F8B2D10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Parts Manager
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no
OutputDir=installer_output
OutputBaseFilename=PartsManager-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\parts_manager.ico
SetupIconFile=parts_manager.ico
Uninstallable=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "parts_manager.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "run_windows.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "parts_manager.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "INSTALL_WINDOWS.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE-COMMERCIAL.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "parts_manager.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "parts_manager_icon.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "ICON_LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{userappdata}\Parts Manager"
Name: "{userappdata}\Parts Manager\data"

[Icons]
Name: "{group}\Parts Manager"; Filename: "{app}\run_windows.vbs"; WorkingDir: "{app}"; IconFilename: "{app}\parts_manager.ico"
Name: "{autodesktop}\Parts Manager"; Filename: "{app}\run_windows.vbs"; WorkingDir: "{app}"; IconFilename: "{app}\parts_manager.ico"

[Run]
Filename: "{app}\run_windows.vbs"; Description: "Запустить Parts Manager"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
; Пользовательские данные намеренно НЕ удаляются.
