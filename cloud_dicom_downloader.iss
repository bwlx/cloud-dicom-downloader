#define MyAppName "Cloud DICOM Downloader"
#define MyAppVersion "0.1.0"
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename "Cloud-DICOM-Downloader-Setup"
#endif
#ifndef VCRedistPath
  #define VCRedistPath ""
#endif
#ifndef MyAppSourceDir
  #define MyAppSourceDir "dist\\Cloud DICOM Downloader"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "dist"
#endif

[Setup]
AppId={{A4E4D1F5-1F9E-4A8C-9E3D-2D950D3E1A25}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBaseFilename}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\Cloud DICOM Downloader.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#VCRedistPath}"; DestDir: "{tmp}"; DestName: "vc_redist.x64.exe"; Flags: deleteafterinstall; Check: ShouldInstallVCRedist

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\Cloud DICOM Downloader.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\Cloud DICOM Downloader.exe"; Tasks: desktopicon

[Run]
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Microsoft Visual C++ Runtime..."; Flags: waituntilterminated; Check: ShouldInstallVCRedist
Filename: "{app}\Cloud DICOM Downloader.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function HasBundledVCRedist(): Boolean;
begin
  Result := '{#VCRedistPath}' <> '';
end;

function IsVCRedistInstalled(): Boolean;
var
  Installed: Cardinal;
begin
  Result :=
    RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) and
    (Installed = 1);
end;

function ShouldInstallVCRedist(): Boolean;
begin
  Result := HasBundledVCRedist() and (not IsVCRedistInstalled());
end;
