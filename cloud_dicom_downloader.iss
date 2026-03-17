#define MyAppName "Cloud DICOM Downloader"
#define MyAppVersion "0.1.0"
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename "Cloud-DICOM-Downloader-Setup"
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
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\Cloud DICOM Downloader.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\Cloud DICOM Downloader.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\Cloud DICOM Downloader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Cloud DICOM Downloader.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
