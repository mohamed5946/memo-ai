; LONA v2 — Inno Setup 6 script
; Build (from the repo root, on Windows, after PyInstaller):
;   ISCC installer\lona.iss
;   or with a version override:  ISCC /DAppVersion=2.0.0 installer\lona.iss
; Output: release\LONA-Setup-<version>.exe
;
; Per-user install to %LOCALAPPDATA%\Programs\LONA — no admin needed.
; v2 upgrades a v1.6.3 install IN PLACE: same AppId and same folder,
; so exactly one Add/Remove Programs entry (LONA) remains. The v1.6.3
; settings + invite state are migrated into the new data folder
; (%LOCALAPPDATA%\LONA) before the new files land — see
; MigrateV1Data / RestoreMigratedData in the Code section — so the
; upgrade never asks for the invite code again and never resets the
; settings. The API keys live in Windows Credential Manager and
; migrate inside the app on first launch (lona2/apikeys.py).
;
; v2.0.0: the Chrome extension + native-messaging host are NOT shipped
; (the extension re-wire is a Phase 2 item — the v2 bridge has no
; native-host entry point yet, so shipping the manifest would install
; a feature that cannot answer). They come back with Phase 2.
;
; v1.2.2: pywebview only gets transparent windows on the EdgeChromium
; backend, which needs a WebView2 Runtime >= 86.0.622.0. If it is missing,
; the installer downloads the official Evergreen Bootstrapper and installs
; it silently BEFORE the app files are copied. If that fails (offline,
; blocked), it only warns and continues — the app still runs, just
; without transparency until WebView2 is installed later.
; v1.6.1: the check used to look only at the 64-bit and per-user
; EdgeUpdate Clients keys, so a per-machine standalone install
; (registered in the 32-bit view) was missed - the installer downloaded
; the runtime for nothing and, when the download failed, wrongly told
; the user WebView2 was not installed. It now checks all four registry
; views plus the runtime folder, and the warning tells a failed download
; apart from a missing runtime.
; v1.6.2: nothing in PrepareToInstall may block setup forever any more -
; the WebView2 download has a 60s timeout and a non-empty-file check,
; and the runtime installer runs with a ~120s bounded wait, after which
; setup always continues (see the helpers above PrepareToInstall).

#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif

#define AppName "LONA"
#define AppPublisher "Mohamed El Sayed"
#define AppURL "https://github.com/mohamed5946/memo-ai"

; v2.0.0: the AppId below is the v1.6.3 LONA install's id — this setup
; upgrades that install in place (same folder, one Add/Remove entry).
; Do NOT "fix" it to {7BBF7DD9-42C6-41D0-BB76-99A5E19569A1}: that was
; the pre-rename "Memo AI" id (before v1.6.1). Using it would create a
; SECOND Add/Remove Programs entry next to the v1.6.3 one instead of
; upgrading it. (Verified against installer/memo-ai.iss on the branch:
; its [Setup] AppId is the one below, and 7BBF7DD9 appears there only
; as OldAppId for the v1.6.0 migration.)

[Setup]
AppId={{EEFC0A4A-4DA0-4477-B539-92CDE89093CC}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
AppMutex=Local\LONA-SingleInstance
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
DefaultDirName={localappdata}\Programs\LONA
DefaultGroupName=LONA
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\release
OutputBaseFilename=LONA-Setup-{#AppVersion}
SetupIconFile=lona.ico
UninstallDisplayIcon={app}\LONA.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Tasks are checked by default in Inno Setup (there is no "checked" flag —
; only "unchecked" exists to opt out), so the desktop shortcut is on
; unless the user clears it.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole PyInstaller one-folder output (exe + _internal + lona2\).
Source: "..\dist\LONA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; keys/ lives next to the exe (not inside _internal) so users can see
; the instructions file; real keys live in Credential Manager.
Source: "..\keys\README.txt"; DestDir: "{app}\keys"; Flags: ignoreversion

[Icons]
Name: "{group}\LONA"; Filename: "{app}\LONA.exe"
Name: "{userdesktop}\LONA"; Filename: "{app}\LONA.exe"; Tasks: desktopicon

[Run]
; After install, offer to run the app. Skipped on silent installs (the
; updater relaunches the app itself).
Filename: "{app}\LONA.exe"; Description: "{cm:LaunchProgram,LONA}"; Flags: nowait postinstall skipifsilent

[Code]
{ ---- WebView2 Runtime check (v1.2.2, detection fixed in v1.6.1) ---- }
{ pywebview only gets transparency on the EdgeChromium backend, which
  needs .NET 4.6.2+ AND a WebView2 Runtime >= 86.0.622.0 registered
  under EdgeUpdate Clients (GUID is in GetWebView2Version below).
  Without it pywebview falls back to MSHTML, which cannot do transparency
  and paints the window background as an opaque dark rectangle.
  v1.6.1: the old check looked only at the 64-bit machine view and the
  per-user view, so a per-machine standalone install was missed - the
  standalone installer itself said "already installed for the system"
  while Setup still tried to download the runtime. The runtime is now
  looked up in all four registry views AND in the runtime folder, so a
  download only starts when the runtime is really absent. }

function VersionPart(var S: string): Integer;
var
  P: Integer;
begin
  P := Pos('.', S);
  if P > 0 then
  begin
    Result := StrToIntDef(Copy(S, 1, P - 1), 0);
    Delete(S, 1, P);
  end
  else
  begin
    Result := StrToIntDef(S, 0);
    S := '';
  end;
end;

function WebView2VersionAtLeast(const V: string): Boolean;
{ True when V >= 86.0.622.0 (the minimum pywebview needs). }
var
  S: string;
  A, B, C: Integer;
begin
  S := V;
  A := VersionPart(S);
  B := VersionPart(S);
  C := VersionPart(S);
  Result := (A > 86) or ((A = 86) and ((B > 0) or ((B = 0) and (C >= 622))));
end;

function GetWebView2Version(): string;
var
  V: string;
begin
  Result := '';
  { The Evergreen runtime registers under EdgeUpdate Clients. The
    per-machine install lives in the 64-bit view, the standalone
    installer registers the 32-bit runtime in the WOW6432Node view,
    and a per-user install registers under the current user - check
    all four views before concluding anything is missing. }
  if RegQueryStringValue(HKLM64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) then
    Result := V
  else if RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) then
    Result := V
  else if RegQueryStringValue(HKCU64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) then
    Result := V
  else if RegQueryStringValue(HKCU32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) then
    Result := V;
end;

function WebView2FolderVersion(const Folder: string): string;
{ The version of a WebView2 runtime found in a folder, or ''.
  The Evergreen runtime lives under an Application folder with one
  subfolder per installed version holding msedgewebview2.exe; a
  subfolder only counts when its version is new enough. }
var
  Rec: TFindRec;
  Found: Boolean;
begin
  Result := '';
  if not DirExists(Folder) then
    Exit;
  Found := FindFirst(Folder + '\*', Rec);
  if Found then
  try
    repeat
      if (Rec.Name <> '.') and (Rec.Name <> '..') and
         FileExists(Folder + '\' + Rec.Name + '\msedgewebview2.exe') and
         WebView2VersionAtLeast(Rec.Name) then
        Result := Rec.Name;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

function WebView2IsPresent(): Boolean;
var
  V: string;
begin
  Result := WebView2VersionAtLeast(GetWebView2Version());
  if Result then
    Exit;
  { The registry says nothing, but the runtime folder may still be
    there: per-machine under Program Files (x86), per-user under the
    local app data folder. }
  V := WebView2FolderVersion(ExpandConstant('{pf32}\Microsoft\EdgeWebView\Application'));
  if V <> '' then
  begin
    Result := True;
    Exit;
  end;
  V := WebView2FolderVersion(ExpandConstant('{localappdata}\Microsoft\EdgeWebView\Application'));
  Result := V <> '';
end;

{ ---- v1.6.2: bounded waits - setup must never hang forever ---- }
{ The v1.6.0 installer hung on "Preparing to install" on a slow
  connection: the WebView2 download had no timeout and the runtime
  installer was waited on forever, so a dead download or a stuck
  installer blocked setup indefinitely. Every external program
  started below runs with ewNoWait plus a wait loop that gives up
  after a fixed timeout, and setup ALWAYS continues afterwards -
  the app falls back gracefully without WebView2. }

function FileSizeOf(const FileName: string): Int64;
{ Size of FileName in bytes, or -1 when it cannot be read. A
  download that "succeeded" but left a 0-byte file counts as failed. }
var
  Stream: TFileStream;
begin
  Result := -1;
  if not FileExists(FileName) then
    Exit;
  Stream := TFileStream.Create(FileName, fmOpenRead or fmShareDenyNone);
  try
    Result := Stream.Size;
  finally
    Stream.Free;
  end;
end;

function WaitForProcessExit(const ImageName: string; TimeoutSeconds: Integer): Boolean;
{ Polls tasklist once a second until no process named ImageName is
  running, or TimeoutSeconds have passed. Returns True when the
  process is gone, False on timeout (the caller continues anyway).
  A tasklist failure counts as "gone" - never block on the check
  itself. }
var
  ListPath: string;
  Lines: TStringList;
  I, Elapsed: Integer;
  ResultCode: Integer;
  Found: Boolean;
begin
  Result := False;
  ListPath := ExpandConstant('{tmp}\lona-procwait.txt');
  Elapsed := 0;
  while Elapsed <= TimeoutSeconds do
  begin
    Found := False;
    if Exec('cmd.exe', '/c tasklist.exe /FI "IMAGENAME eq ' + ImageName +
       '" /FO CSV /NH > "' + ListPath + '"', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode) and FileExists(ListPath) then
    begin
      Lines := TStringList.Create;
      try
        Lines.LoadFromFile(ListPath);
        for I := 0 to Lines.Count - 1 do
          if Pos('"' + ImageName + '"', Lines[I]) > 0 then
          begin
            Found := True;
            Break;
          end;
      finally
        Lines.Free;
      end;
    end;
    if not Found then
    begin
      Result := True;
      Exit;
    end;
    Sleep(1000);
    Elapsed := Elapsed + 1;
  end;
end;

function ExecWithTimeout(const FileName, Params: string; TimeoutSeconds: Integer): Boolean;
{ Starts FileName with ewNoWait, then waits at most TimeoutSeconds
  for its process to exit. Returns True only when the process exited
  in time; False means "still running when the time ran out" - the
  caller must carry on regardless, never wait again. }
var
  ResultCode: Integer;
begin
  Result := False;
  if not Exec(FileName, Params, '', SW_HIDE, ewNoWait, ResultCode) then
    Exit;
  Result := WaitForProcessExit(ExtractFileName(FileName), TimeoutSeconds);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
{ Runs BEFORE any app file is installed. Returning a non-empty string
  aborts the setup — this function always returns '' (continue). A
  failed WebView2 install only shows a warning; LONA still installs
  and runs, just without transparency until WebView2 is installed later.
  v1.6.1: the warning tells a failed DOWNLOAD apart from a missing
  runtime - a download failure must never be reported as "not
  installed", because the runtime may already be there (the detection
  above is the only thing that decides "missing").
  v1.6.2: nothing here may block setup forever. The download has a
  60-second timeout and the downloaded file must be non-empty; the
  runtime installer runs with ewNoWait and is waited on for at most
  ~120 seconds. On any failure setup continues - the app falls back
  gracefully without WebView2. }
var
  SetupPath: string;
  PsCmd: string;
  ResultCode: Integer;
  DownloadFailed: Boolean;
begin
  Result := '';
  NeedsRestart := False;
  if WebView2IsPresent() then
    Exit;
  DownloadFailed := False;
  SetupPath := ExpandConstant('{tmp}\MicrosoftEdgeWebView2Setup.exe');
  PsCmd := '-NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri ''https://go.microsoft.com/fwlink/p/?LinkId=2124703'' -OutFile ''' + SetupPath + ''' -UseBasicParsing -TimeoutSec 60; exit 0 } catch { exit 1 }"';
  if Exec('powershell.exe', PsCmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
     and (ResultCode = 0) and FileExists(SetupPath) and (FileSizeOf(SetupPath) > 0) then
  begin
    { v1.6.2: bounded wait (~120s) instead of waiting forever -
      a stuck runtime installer used to hang setup forever. Success
      is judged by the runtime actually being present afterwards. }
    if ExecWithTimeout(SetupPath, '/silent /install', 120) and WebView2IsPresent() then
      Exit;
  end
  else
    DownloadFailed := True;
  if DownloadFailed then
    MsgBox('مقدرناش ننزّل Microsoft WebView2 Runtime دلوقتي — ممكن مفيش اتصال بالإنترنت.' + #13#10#13#10 +
           'هيكمل تثبيت LONA عادي، وبعض المميزات ممكن متظهرش صح لحد ما WebView2 يتثبّت من الرابط الرسمي: https://go.microsoft.com/fwlink/p/?LinkId=2124703',
           mbInformation, MB_OK)
  else
    MsgBox('حاولنا نثبّت Microsoft WebView2 Runtime بس التثبيت مكملش — ممكن يكون متثبت أصلاً أو التثبيت اتوقف.' + #13#10#13#10 +
           'هيكمل تثبيت LONA عادي، وبعض المميزات ممكن متظهرش صح لحد ما WebView2 يتثبّت من الرابط الرسمي: https://go.microsoft.com/fwlink/p/?LinkId=2124703',
           mbInformation, MB_OK);
end;

{ ---- v2.0.0: migrate v1.6.3 data into the new data folder ---- }
{ v2 keeps its state in %LOCALAPPDATA%\LONA (settings.json,
  activated.json, tasks.json, memories.json, logs) instead of the
  install folder. v1.6.3 kept settings.json + activated.json under
  {app}\_internal\app\data (frozen install) or app\data (zip install).
  Without this step an upgrade would ask for the invite code again
  and reset every setting — the exact loss the v1.6.1 rename migration
  was built to prevent. The API keys live in Windows Credential
  Manager and migrate inside the app on first launch (see
  lona2/apikeys.py _migrate_v1_keys); only the two files move here,
  and only when the new location does not already have them.
  Staging happens at ssInstall (before any file is overwritten) and
  the restore at ssPostInstall (after the new files are in place). }

function V2DataDir(): string;
begin
  Result := ExpandConstant('{localappdata}\LONA');
end;

procedure StageV1File(const SrcPath, StageDir: string);
var
  DstPath: string;
begin
  if not FileExists(SrcPath) then
    Exit;
  if not DirExists(StageDir) then
    ForceDirectories(StageDir);
  DstPath := StageDir + '\' + ExtractFileName(SrcPath);
  if not FileExists(DstPath) then
    FileCopy(SrcPath, DstPath, False);
end;

procedure MigrateV1Data();
var
  OldDir, StageDir: string;
begin
  StageDir := ExpandConstant('{tmp}\lona-v2-migration');
  if DirExists(StageDir) then
    DelTree(StageDir, True, True, True);
  { Frozen v1.6.3 install: the data dir is under the install folder
    itself (this setup installs into the same folder). }
  OldDir := ExpandConstant('{app}\_internal\app\data');
  StageV1File(OldDir + '\settings.json', StageDir);
  StageV1File(OldDir + '\activated.json', StageDir);
  { Zip-style v1.6.3 install (data next to the app folder). }
  OldDir := ExpandConstant('{app}\app\data');
  StageV1File(OldDir + '\settings.json', StageDir);
  StageV1File(OldDir + '\activated.json', StageDir);
  { Old zip installs live in Downloads\memo-ai\app\data. }
  OldDir := ExpandConstant('{userdocs}\Downloads\memo-ai\app\data');
  StageV1File(OldDir + '\settings.json', StageDir);
  StageV1File(OldDir + '\activated.json', StageDir);
end;

procedure RestoreMigratedData();
var
  StageDir, NewDataDir: string;
  Rec: TFindRec;
  Found: Boolean;
begin
  StageDir := ExpandConstant('{tmp}\lona-v2-migration');
  if not DirExists(StageDir) then
    Exit;
  NewDataDir := V2DataDir();
  if not DirExists(NewDataDir) then
    ForceDirectories(NewDataDir);
  Found := FindFirst(StageDir + '\*', Rec);
  if Found then
  try
    repeat
      if (Rec.Name <> '.') and (Rec.Name <> '..') and
         (not FileExists(NewDataDir + '\' + Rec.Name)) then
        FileCopy(StageDir + '\' + Rec.Name,
                 NewDataDir + '\' + Rec.Name, False);
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
  DelTree(StageDir, True, True, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    MigrateV1Data()
  else if CurStep = ssPostInstall then
    RestoreMigratedData();
end;
