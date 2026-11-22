; LONA — Inno Setup 6 script
; Build (from the repo root, on Windows, after PyInstaller):
;   ISCC installer\memo-ai.iss
;   or with a version override:  ISCC /DAppVersion=1.4.1 installer\memo-ai.iss
; Output: release\LONA-Setup-<version>.exe
;
; Per-user install to %LOCALAPPDATA%\Programs\LONA — no admin needed.
; The installer never deletes keys\: it only copies the program files, and
; on a fresh install it migrates any *.txt keys (except README.txt) from an
; old zip install at Downloads\memo-ai\keys\ (never overwriting keys that
; already exist in the new location).
;
; v1.6.1 rename: the program used to be "Memo AI" (folder
; %LOCALAPPDATA%\Programs\Memo AI, exe Memo AI.exe). It is now LONA:
; new AppId below, new folder, new exe name. An upgrade from a v1.6.0
; install migrates its data (settings, invite state, memory, keys)
; before the old install is removed - see MigrateOldInstallData and
; RemoveOldInstall in the Code section - so exactly one Add/Remove
; Programs entry (LONA) remains and nothing is lost.
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
; v1.6.2: nothing in PrepareToInstall or RemoveOldInstall may block
; setup forever any more - the WebView2 download has a 60s timeout
; and a non-empty-file check, and both the runtime installer and the
; old uninstaller run with a ~120s bounded wait, after which setup
; always continues (see the helpers above PrepareToInstall).

#ifndef AppVersion
  #define AppVersion "1.4.1"
#endif

#define AppName "LONA"
#define AppPublisher "Mohamed El Sayed"
#define AppURL "https://github.com/mohamed5946/memo-ai"

; v1.3.2: NEVER install over a running LONA again. v1.3.1 was
; installed over a running v1.3.0 and the install mixed old/new files
; (first launch hung: "Not Responding"). AppMutex + CloseApplications
; make Setup stop and ask the user to close LONA first (the app's
; own Quit lives in Settings). The in-app updater is unaffected: it
; quits the app itself and waits 3s before running the silent setup
; (see app/updater.py install_setup), so the mutex is gone by then.
;
; v1.6.1 rename: new AppId (the old "Memo AI" install had
; 7BBF7DD9-42C6-41D0-BB76-99A5E19569A1). Keeping the old AppId would
; make Setup reuse the old install folder, defeating the rename - so
; the new install is a new program, and RemoveOldInstall in the Code
; section removes the old one (folder + Add/Remove Programs entry)
; after its data has been staged for migration.

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
; The whole PyInstaller one-folder output (exe + _internal + app\).
Source: "..\dist\LONA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; keys/ lives next to the exe (not inside _internal) so users can see and
; edit their key files; only the instructions file ships with the installer.
Source: "..\keys\README.txt"; DestDir: "{app}\keys"; Flags: ignoreversion
; v1.6.4-rc.2: third-party notices (incl. the full MPL-2.0 text for
; the embedded LibreHardwareMonitor library) ship with the install.
Source: "..\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
; v1.6.0 (LONA Browser extension): the Chrome native-host manifest.
; It is copied here and then REWRITTEN at install time by
; WriteNativeHostManifest() with the real install path (see [Code]).
Source: "native-host.json"; DestDir: "{app}"; Flags: ignoreversion
; v1.6.0 (LONA Browser extension): the extension source itself, so the
; user can load it unpacked from the install folder
; (chrome://extensions -> Developer mode -> Load unpacked -> {app}\extension).
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LONA"; Filename: "{app}\LONA.exe"
Name: "{userdesktop}\LONA"; Filename: "{app}\LONA.exe"; Tasks: desktopicon

[Run]
; After install, offer to run the app (v1.4.1: it starts with the main window directly - the island is gone).
; Skipped on silent installs (the updater relaunches the app itself).
Filename: "{app}\LONA.exe"; Description: "{cm:LaunchProgram,LONA}"; Flags: nowait postinstall skipifsilent

[Registry]
; v1.6.0 (LONA Browser extension): register the native-messaging host
; per-user (HKCU, no admin). Chrome reads this key when the extension
; connects; the value points at the manifest shipped next to the exe.
; The host id (com.memoai.host) is NOT renamed — only the display
; name changed to LONA. Uninstall deletes the key (uninsdeletekey +
; the explicit cleanup in CurUninstallStepChanged below).
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.memoai.host"; ValueType: string; ValueName: ""; ValueData: "{app}\native-host.json"; Flags: uninsdeletekey

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
  the app falls back gracefully without WebView2, and the old
  install's leftovers are removed by hand below. }

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

{ ---- v1.6.1 rename: migrate data from the old Memo AI install ---- }
{ The program is now LONA: new AppId, new install folder, new exe
  name. An upgrade from a v1.6.0 Memo AI install must carry its data
  over - settings, invite state, memory, keys - or the app would ask
  for the invite code again and the user would lose his keys. The
  keys themselves live in Windows Credential Manager and survive the
  move on their own, but settings.json and the data folder must be
  copied. Exactly one Add/Remove Programs entry must remain (LONA).
  Two phases, because the install itself would clobber the migration:
  ssInstall stages the old data into the temp folder, then removes
  the old install; ssPostInstall restores the staged files OVER the
  freshly installed ones. The restore must run after the files step,
  because the bundle ships default rules.json / qa.json / update.json
  and the file step always overwrites - staging first and restoring
  after is the only order where the user files survive. The
  Python fallback in app/migrate.py does the same job at first launch
  if this step is ever skipped. }

const
  OldAppDirName = 'Memo AI';
  OldAppId = '{7BBF7DD9-42C6-41D0-BB76-99A5E19569A1}';

function OldInstallDir(): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\' + OldAppDirName);
end;

procedure CopyMissingFiles(const SrcDir, DstDir: string; var Copied: Integer);
var
  Rec: TFindRec;
  Found: Boolean;
  SrcPath, DstPath: string;
begin
  if not DirExists(SrcDir) then
    Exit;
  if not DirExists(DstDir) then
    ForceDirectories(DstDir);
  Found := FindFirst(SrcDir + '\*', Rec);
  if Found then
  try
    repeat
      if (Rec.Name <> '.') and (Rec.Name <> '..') then
      begin
        SrcPath := SrcDir + '\' + Rec.Name;
        DstPath := DstDir + '\' + Rec.Name;
        { 16 is FILE_ATTRIBUTE_DIRECTORY: the entry is a subfolder. }
        if (Rec.Attributes and 16) <> 0 then
          CopyMissingFiles(SrcPath, DstPath, Copied)
        else if not FileExists(DstPath) then
          if FileCopy(SrcPath, DstPath, False) then
            Copied := Copied + 1;
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

procedure CopyFilesOver(const SrcDir, DstDir: string);
{ Copy every file from SrcDir into DstDir, OVERWRITING files that
  already exist there (the staged user files win over the bundled
  defaults). Used by the ssPostInstall restore only. }
var
  Rec: TFindRec;
  Found: Boolean;
  SrcPath, DstPath: string;
begin
  if not DirExists(SrcDir) then
    Exit;
  if not DirExists(DstDir) then
    ForceDirectories(DstDir);
  Found := FindFirst(SrcDir + '\*', Rec);
  if Found then
  try
    repeat
      if (Rec.Name <> '.') and (Rec.Name <> '..') then
      begin
        SrcPath := SrcDir + '\' + Rec.Name;
        DstPath := DstDir + '\' + Rec.Name;
        if (Rec.Attributes and 16) <> 0 then
          CopyFilesOver(SrcPath, DstPath)
        else
          FileCopy(SrcPath, DstPath, False);
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

procedure MigrateOldInstallData();
var
  OldDir, OldDataDir, StageDir, OldKeysDir, StageKeysDir: string;
  Copied: Integer;
  Rec: TFindRec;
  Found: Boolean;
begin
  OldDir := OldInstallDir();
  if (CompareText(OldDir, ExpandConstant('{app}')) = 0) or
     (not DirExists(OldDir)) then
    Exit;
  StageDir := ExpandConstant('{tmp}\lona-migration');
  if DirExists(StageDir) then
    DelTree(StageDir, True, True, True);
  Copied := 0;
  { Frozen installs keep data under _internal\app\data; a zip-style
    install keeps it under app\data - stage whichever exists. }
  OldDataDir := OldDir + '\_internal\app\data';
  if not DirExists(OldDataDir) then
    OldDataDir := OldDir + '\app\data';
  CopyMissingFiles(OldDataDir, StageDir + '\data', Copied);
  { Legacy txt keys (v1.6.0 keys live in Credential Manager and need
    no move, but an old keys folder must still come over). }
  OldKeysDir := OldDir + '\keys';
  if DirExists(OldKeysDir) then
  begin
    StageKeysDir := StageDir + '\keys';
    ForceDirectories(StageKeysDir);
    Found := FindFirst(OldKeysDir + '\*.txt', Rec);
    if Found then
    try
      repeat
        if (CompareText(Rec.Name, 'README.txt') <> 0) and
           (not FileExists(StageKeysDir + '\' + Rec.Name)) then
          if FileCopy(OldKeysDir + '\' + Rec.Name,
                      StageKeysDir + '\' + Rec.Name, False) then
            Copied := Copied + 1;
      until not FindNext(Rec);
    finally
      FindClose(Rec);
    end;
  end;
end;

procedure RestoreMigratedData();
var
  StageDir, NewDataDir, StageKeysDir, NewKeysDir: string;
begin
  StageDir := ExpandConstant('{tmp}\lona-migration');
  if not DirExists(StageDir) then
    Exit;
  NewDataDir := ExpandConstant('{app}\_internal\app\data');
  CopyFilesOver(StageDir + '\data', NewDataDir);
  StageKeysDir := StageDir + '\keys';
  if DirExists(StageKeysDir) then
  begin
    NewKeysDir := ExpandConstant('{app}\keys');
    CopyFilesOver(StageKeysDir, NewKeysDir);
  end;
  DelTree(StageDir, True, True, True);
end;

procedure RemoveOldInstall();
var
  OldDir, UninstallString, UninstExe: string;
  ResultCode: Integer;
begin
  OldDir := OldInstallDir();
  if (CompareText(OldDir, ExpandConstant('{app}')) = 0) or
     (not DirExists(OldDir)) then
    Exit;
  { The old uninstaller removes the old folder AND the old
    Add/Remove Programs entry. Its path comes from the old
    uninstall registry key; fall back to unins000.exe in the old
    folder. A running old app is closed by that uninstaller itself. }
  UninstExe := '';
  if RegQueryStringValue(HKCU,
       'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
       OldAppId + '_is1', 'UninstallString', UninstallString) then
    UninstExe := RemoveQuotes(UninstallString);
  if (UninstExe = '') or (not FileExists(UninstExe)) then
    UninstExe := OldDir + '\unins000.exe';
  if FileExists(UninstExe) then
    { v1.6.2: bounded wait (~120s) instead of waiting forever -
      a stuck old uninstaller used to hang setup forever. Whatever
      happens, setup carries on and the leftovers are removed by
      hand just below. }
    ExecWithTimeout(UninstExe, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', 120);
  { No uninstaller, or it left files behind: remove the leftovers
    by hand, so exactly one entry - LONA - remains. The native-host
    registry key is re-written by this install afterwards, so
    deleting the old one here is safe. }
  if DirExists(OldDir) then
    DelTree(OldDir, True, True, True);
  RegDeleteKeyIncludingSubkeys(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
    OldAppId + '_is1');
  DelTree(ExpandConstant('{userstartmenu}\Programs\' + OldAppDirName),
          True, True, True);
  RegDeleteValue(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Run', OldAppDirName);
end;

procedure MigrateKeysFromZipInstall();
var
  SrcDir, DstDir: string;
  Found: Boolean;
  Rec: TFindRec;
begin
  { Old zip installs live in Downloads\memo-ai\keys\. The default
    Downloads location is used; if it was moved, nothing is migrated
    (the user can copy the key files by hand into the install's keys\). }
  SrcDir := ExpandConstant('{userdocs}\Downloads\memo-ai\keys');
  DstDir := ExpandConstant('{app}\keys');
  if (not DirExists(SrcDir)) or (not DirExists(DstDir)) then
    Exit;
  Found := FindFirst(SrcDir + '\*.txt', Rec);
  if Found then
  try
    repeat
      if (CompareText(Rec.Name, 'README.txt') <> 0) and
         (not FileExists(DstDir + '\' + Rec.Name)) then
      begin
        FileCopy(SrcDir + '\' + Rec.Name, DstDir + '\' + Rec.Name, False);
      end;
    until not FindNext(Rec);
  finally
    FindClose(Rec);
  end;
end;

{ ---- v1.6.0: LONA Browser native host (Chrome) ---- }
{ The manifest's "path" must be an ABSOLUTE path to an executable, so
  the template shipped in [Files] cannot carry it — this rewrites
  native-host.json in the install folder at install time with the
  real install dir. The exe itself runs the host stdio loop when
  launched with --native-host (or with a chrome-extension:// origin
  argument — see extension/NATIVE_HOST_MAIN_HOOK.txt).
  allowed_origins carries a placeholder: the extension id is only
  known after the extension is first loaded in Chrome
  (chrome://extensions), so it must be filled in with the real id
  once — the file is plain JSON next to the exe. }

procedure WriteNativeHostManifest();
var
  ManifestPath, ExePath, Json: string;
begin
  ManifestPath := ExpandConstant('{app}\native-host.json');
  ExePath := ExpandConstant('{app}\LONA.exe');
  StringChangeEx(ExePath, '\', '\\', True);
  Json := '{' + #13#10 +
    '  "name": "com.memoai.host",' + #13#10 +
    '  "description": "LONA browser bridge",' + #13#10 +
    '  "path": "' + ExePath + '",' + #13#10 +
    '  "type": "stdio",' + #13#10 +
    '  "allowed_origins": ["chrome-extension://REPLACE_WITH_EXTENSION_ID/"]' + #13#10 +
    '}';
  if not SaveStringToFile(ManifestPath, Json, False) then
    MsgBox('تعذّر كتابة ملف native-host.json — إضافة المتصفح مش هتتصل بـ LONA لحد ما يتكتب يدوياً.',
      mbInformation, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    { Stage the old data first, THEN remove the old install - the
      old uninstaller deletes the old folder, so the order matters. }
    MigrateOldInstallData();
    RemoveOldInstall();
  end
  else if CurStep = ssPostInstall then
  begin
    RestoreMigratedData();
    MigrateKeysFromZipInstall();
    WriteNativeHostManifest();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteKeyIncludingSubkeys(HKCU,
      'Software\Google\Chrome\NativeMessagingHosts\com.memoai.host');
end;
