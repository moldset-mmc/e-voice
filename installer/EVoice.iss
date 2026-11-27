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
DisableFinishedPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
LicenseFile=windows-runtime-terms.txt
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

[Messages]
russian.FinishedLabel=E Voice запущен. Дважды щёлкните по ярлыку на рабочем столе, чтобы открыть настройки.%n%nВключите «Вызывать тройным щелчком». Затем трижды быстро щёлкните левой кнопкой мыши в одном месте вне E Voice. Плавающий микрофон появится рядом с курсором.
english.FinishedLabel=E Voice is running. Double-click the desktop shortcut to open settings.%n%nTurn on “Вызывать тройным щелчком” (triple-click). Then quickly left-click three times in one spot outside E Voice. The floating microphone will appear beside the pointer.

[Files]
Source: "windows-runtime-terms.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "..\dist\EVoice\EVoice.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\EVoice\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\EVoice\models\gigaam-v3\*"; DestDir: "{app}\models\gigaam-v3"; Flags: ignoreversion
Source: "..\dist\EVoice\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\EVoice\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\EVoice\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\E Voice"; Filename: "{app}\EVoice.exe"
Name: "{userdesktop}\E Voice"; Filename: "{app}\EVoice.exe"; Parameters: "--show-settings"

[Run]
Filename: "{app}\EVoice.exe"; Flags: nowait skipifsilent

[InstallDelete]
Type: files; Name: "{app}\_internal\_sounddevice_data\portaudio-binaries\libportaudio64bit-asio.dll"
Type: files; Name: "{app}\_internal\ucrtbase.dll"
Type: files; Name: "{app}\_internal\api-ms-win-*.dll"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := not WizardSilent or (ExpandConstant('{param:ACCEPTRUNTIMETERMS|0}') = '1');
  if not Result then
    Log('Unattended installation requires /ACCEPTRUNTIMETERMS=1 after reviewing the Microsoft runtime terms.');
end;
