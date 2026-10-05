; Inno Setup script; build.ps1 passes /DAppVersion=x.y.z
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define TypeLib "{{7858B9E0-5793-4BE4-9B53-661D922790D2}"

[Setup]
AppId={{5C7B0E4A-2F61-4C8B-9E3D-7A1F0C9B4A10}
AppName=Phonavigator
AppVersion={#AppVersion}
AppPublisher=Michiel Bruijn
AppPublisherURL=https://github.com/MichielBruijn/phonavigator
DefaultDirName={autopf}\Phonavigator
DefaultGroupName=Phonavigator
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile=build\phonavigator.ico
UninstallDisplayIcon={app}\Phonavigator.exe
OutputDir=build
OutputBaseFilename=Phonavigator-{#AppVersion}-setup
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: autostart; Description: "Start Phonavigator when I log in"

[Files]
Source: "build\dist\Phonavigator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Our navigation library, where 3D applications look for 3Dconnexion's (in use: replaced at reboot)
Source: "navlib\build\x64\TDxNavLib.dll"; DestDir: "{sys}"; Flags: ignoreversion restartreplace uninsrestartdelete 64bit
Source: "navlib\build\x86\TDxNavLib.dll"; DestDir: "{syswow64}"; Flags: ignoreversion restartreplace uninsrestartdelete 32bit

[Registry]
; Applications check these before they use the navigation library (both registry views).
Root: HKLM64; Subkey: "Software\3Dconnexion\3DxWare"; ValueType: string; ValueName: "Version"; ValueData: "17.8.15.20373"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKLM32; Subkey: "Software\3Dconnexion\3DxWare"; ValueType: string; ValueName: "Version"; ValueData: "17.8.15.20373"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKLM64; Subkey: "Software\Classes\TypeLib\{#TypeLib}\1.0"; ValueType: string; ValueData: "TDxInput 1.0 Type Library"; Flags: uninsdeletekey
Root: HKLM64; Subkey: "Software\Classes\TypeLib\{#TypeLib}\1.0\0\win64"; ValueType: string; ValueData: "{commonpf64}\3Dconnexion\3DxWare\3DxWinCore64\win64\TDxInput.dll"
Root: HKLM64; Subkey: "Software\Classes\TypeLib\{#TypeLib}\1.0\0\win32"; ValueType: string; ValueData: "{commonpf64}\3Dconnexion\3DxWare\3DxWinCore32\win32\TDxInput.dll"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Phonavigator"; ValueData: """{app}\Phonavigator.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Icons]
Name: "{autoprograms}\Phonavigator"; Filename: "{app}\Phonavigator.exe"; Parameters: "--settings"

[Run]
Filename: "{app}\Phonavigator.exe"; Parameters: "--settings"; Description: "Start Phonavigator"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im Phonavigator.exe"; Flags: runhidden; RunOnceId: "StopTray"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  { 3DxWare owns TDxNavLib.dll; ours would replace it. }
  if DirExists(ExpandConstant('{commonpf64}\3Dconnexion\3DxWare')) then
  begin
    MsgBox('3Dconnexion 3DxWare is installed. Phonavigator replaces its navigation library ' +
           'and cannot work next to it. Uninstall 3DxWare first.', mbError, MB_OK);
    Result := False;
  end;
end;
