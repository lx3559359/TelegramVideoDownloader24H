#define AppVersion "0.3.10"
#ifndef AppBuildSource
  #define AppBuildSource "..\.tmp\windows-dist\TelegramVideoDownloader"
#endif
[Setup]
#ifdef SmokeTest
AppId=TelegramVideoDownloaderIsolatedSmoke
Uninstallable=no
#else
AppId={{96C5A076-4681-44F4-B8C9-6A5C8E9A2E93}
#endif
AppName=Telegram 视频自动下载器
AppVersion={#AppVersion}
AppPublisher=Telegram 视频自动下载器
AppPublisherURL=https://www.cqtcshequ.com/
AppSupportURL=https://www.cqtcshequ.com/#guide
AppUpdatesURL=https://www.cqtcshequ.com/#download
DefaultDirName={localappdata}\Programs\TelegramVideoDownloader
DefaultGroupName=Telegram 视频自动下载器
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\.tmp\public-releases
OutputBaseFilename=TelegramVideoDownloader-v{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src\tg_video_downloader\assets\app.ico
UninstallDisplayIcon={app}\TelegramVideoDownloader.exe
AppMutex=TelegramVideoDownloader.Running
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
InfoBeforeFile=windows-readme.txt

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#AppBuildSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\scripts\run-supervisor.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "windows-readme.txt"; DestDir: "{app}"; DestName: "使用说明.txt"; Flags: ignoreversion
Source: "LICENSE-Python.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion

#ifndef SmokeTest
[Icons]
Name: "{group}\Telegram 视频自动下载器"; Filename: "{app}\TelegramVideoDownloader.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Telegram 视频自动下载器"; Filename: "{app}\TelegramVideoDownloader.exe"; WorkingDir: "{app}"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\TelegramVideoDownloader.exe"; Description: "启动 Telegram 视频自动下载器"; Flags: nowait postinstall skipifsilent
