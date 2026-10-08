; Inno Setup script – builds PlanWinAIPro-<ver>-win64-setup.exe from dist\PlanWinAIPro
#define AppName "PlanWin AI Pro"
#ifndef AppVersion
  #define AppVersion "1.0.1"
#endif
#define AppPublisher "Computer Help"
#define AppURL "https://www.buildingsoftware.in"
#define AppExe "PlanWinAIPro.exe"

[Setup]
AppId={{6E4C2A57-3F1B-4F7E-9C0E-5B9A1D2C7E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\PlanWin AI Pro
DefaultGroupName=PlanWin AI Pro
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=PlanWinAIPro-{#AppVersion}-win64-setup
SetupIconFile=..\assets\planwin_ai.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoc"; Description: "Open .pwai and legacy .plw files with PlanWin AI Pro"; GroupDescription: "File associations:"

[Files]
Source: "..\dist\PlanWinAIPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PlanWin AI Pro"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall PlanWin AI Pro"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PlanWin AI Pro"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.pwai"; ValueType: string; ValueName: ""; ValueData: "PlanWinAIPro.Project"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\PlanWinAIPro.Project"; ValueType: string; ValueName: ""; ValueData: "PlanWin AI Pro project"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\PlanWinAIPro.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\PlanWinAIPro.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.plw\OpenWithProgids"; ValueType: string; ValueName: "PlanWinAIPro.Project"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
