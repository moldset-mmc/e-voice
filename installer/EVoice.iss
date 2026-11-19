#ifndef AppVersion
  #error Supply AppVersion from the VERSION file
#endif
[Setup]
AppId={{65E8EF72-F3A8-48DB-9F34-FD4AD3EE6E95}
AppName=E Voice
AppVersion={#AppVersion}
AppPublisher=E Voice
AppPublisherURL=https://github.com/moldset-mmc/e-voice
AppSupportURL=https://github.com/moldset-mmc/e-voice/issues
VersionInfoVersion={#AppVersion}.0
VersionInfoDescription=E Voice Offline Setup
DefaultDirName={localappdata}\Programs\E Voice
DefaultGroupName=E Voice
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
DisableFinishedPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=..\assets\evoice.ico
UninstallDisplayIcon={app}\EVoice.exe
OutputDir=..\dist
OutputBaseFilename=E-Voice-Setup-x64
Compression=lzma2/normal
SolidCompression=yes
AppMutex=Local\LocalVoiceRussianOffline
CloseApplications=no
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\EVoice\EVoice.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\EVoice\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\EVoice\models\gigaam-v3\*"; DestDir: "{app}\models\gigaam-v3"; Flags: ignoreversion
Source: "..\dist\EVoice\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\EVoice\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\EVoice\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\E Voice"; Filename: "{app}\EVoice.exe"

[Run]
Filename: "{app}\EVoice.exe"; Flags: nowait skipifsilent
