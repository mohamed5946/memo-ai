; Memo AI — Inno Setup 6 script
; Build (from the repo root, on Windows, after PyInstaller):
;   ISCC installer\memo-ai.iss
;   or with a version override:  ISCC /DAppVersion=1.4.1 installer\memo-ai.iss
; Output: release\MemoAI-Setup-<version>.exe
;
; Per-user install to %LOCALAPPDATA%\Programs\Memo AI — no admin needed.
; The installer never deletes keys\: it only copies the program files, and
; on a fresh install it migrates any *.txt keys (except README.txt) from an
; old zip install at Downloads\memo-ai\keys\ (never overwriting keys that
; already exist in the new location).
;
; v1.2.2: pywebview only gets transparent windows on the EdgeChromium
; backend, which needs a WebView2 Runtime >= 86.0.622.0. If it is missing,
; the installer downloads the official Evergreen Bootstrapper and installs
; it silently BEFORE the app files are copied. If that fails (offline,
; blocked), it only warns and continues — the app still runs, just
; without transparency until WebView2 is installed later.

#ifndef AppVersion
  #define AppVersion "1.4.1"
#endif

#define AppName "Memo AI"
#define AppPublisher "Mohamed El Sayed"
#define AppURL "https://github.com/mohamed5946/memo-ai"

; v1.3.2: NEVER install over a running Memo AI again. v1.3.1 was
; installed over a running v1.3.0 and the install mixed old/new files
; (first launch hung: "Not Responding"). AppMutex + CloseApplications
; make Setup stop and ask the user to close Memo AI first (the app's
; own Quit lives in Settings). The in-app updater is unaffected: it
; quits the app itself and waits 3s before running the silent setup
; (see app/updater.py install_setup), so the mutex is gone by then.

[Setup]
AppId={{7BBF7DD9-42C6-41D0-BB76-99A5E19569A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
AppMutex=Local\MemoAI-SingleInstance
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
DefaultDirName={localappdata}\Programs\Memo AI
DefaultGroupName=Memo AI
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\release
OutputBaseFilename=MemoAI-Setup-{#AppVersion}
SetupIconFile=memo-ai.ico
UninstallDisplayIcon={app}\Memo AI.exe
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
Source: "..\dist\Memo AI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; keys/ lives next to the exe (not inside _internal) so users can see and
; edit their key files; only the instructions file ships with the installer.
Source: "..\keys\README.txt"; DestDir: "{app}\keys"; Flags: ignoreversion
; v1.6.0 (LONA Browser extension): the Chrome native-host manifest.
; It is copied here and then REWRITTEN at install time by
; WriteNativeHostManifest() with the real install path (see [Code]).
Source: "native-host.json"; DestDir: "{app}"; Flags: ignoreversion
; v1.6.0 (LONA Browser extension): the extension source itself, so the
; user can load it unpacked from the install folder
; (chrome://extensions -> Developer mode -> Load unpacked -> {app}\extension).
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Memo AI"; Filename: "{app}\Memo AI.exe"
Name: "{userdesktop}\Memo AI"; Filename: "{app}\Memo AI.exe"; Tasks: desktopicon

[Run]
; After install, offer to run the app (v1.4.1: it starts with the main window directly - the island is gone).
; Skipped on silent installs (the updater relaunches the app itself).
Filename: "{app}\Memo AI.exe"; Description: "{cm:LaunchProgram,Memo AI}"; Flags: nowait postinstall skipifsilent

[Registry]
; v1.6.0 (LONA Browser extension): register the native-messaging host
; per-user (HKCU, no admin). Chrome reads this key when the extension
; connects; the value points at the manifest shipped next to the exe.
; The host id (com.memoai.host) is NOT renamed — only the display
; name changed to LONA. Uninstall deletes the key (uninsdeletekey +
; the explicit cleanup in CurUninstallStepChanged below).
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.memoai.host"; ValueType: string; ValueName: ""; ValueData: "{app}\native-host.json"; Flags: uninsdeletekey

[Code]
{ ---- WebView2 Runtime check (v1.2.2) ---- }
{ pywebview only gets transparency on the EdgeChromium backend, which
  needs .NET 4.6.2+ AND a WebView2 Runtime >= 86.0.622.0 registered
  under EdgeUpdate Clients (GUID is in GetWebView2Version below).
  Without it pywebview falls back to MSHTML, which cannot do transparency
  and paints the window background as an opaque dark rectangle. }

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
  { The Evergreen runtime registers under EdgeUpdate Clients in both the
    64-bit machine view and the per-user view. }
  if RegQueryStringValue(HKLM64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) then
    Result := V
  else if RegQueryStringValue(HKCU64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) then
    Result := V;
end;

function WebView2IsPresent(): Boolean;
begin
  Result := WebView2VersionAtLeast(GetWebView2Version());
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
{ Runs BEFORE any app file is installed. Returning a non-empty string
  aborts the setup — this function always returns '' (continue). A
  failed WebView2 install only shows a warning; Memo AI still installs
  and runs, just without transparency until WebView2 is installed later. }
var
  SetupPath: string;
  PsCmd: string;
  ResultCode: Integer;
begin
  Result := '';
  NeedsRestart := False;
  if WebView2IsPresent() then
    Exit;
  SetupPath := ExpandConstant('{tmp}\MicrosoftEdgeWebView2Setup.exe');
  PsCmd := '-NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri ''https://go.microsoft.com/fwlink/p/?LinkId=2124703'' -OutFile ''' + SetupPath + ''' -UseBasicParsing; exit 0 } catch { exit 1 }"';
  if Exec('powershell.exe', PsCmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
     and (ResultCode = 0) and FileExists(SetupPath) then
  begin
    if Exec(SetupPath, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
       and (ResultCode = 0) and WebView2IsPresent() then
      Exit;
  end;
  MsgBox('Microsoft WebView2 Runtime مش متثبت على الجهاز ده (أو تعذّر تنزيله دلوقتي).' + #13#10#13#10 +
         'هيكمل تثبيت Memo AI عادي، بس بعض المميزات ممكن متظهرش صح لحد ما WebView2 يتثبّت.' + #13#10 +
         'ممكن تثبّته لاحقاً من الرابط الرسمي: https://go.microsoft.com/fwlink/p/?LinkId=2124703',
         mbInformation, MB_OK);
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
  {app}\native-host.json at install time with the real install dir.
  The exe itself runs the host stdio loop when launched with
  --native-host (or with a chrome-extension:// origin argument — see
  extension/NATIVE_HOST_MAIN_HOOK.txt). allowed_origins carries a
  placeholder: the extension id is only known after the extension is
  first loaded in Chrome (chrome://extensions), so it must be filled
  in with the real id once — the file is plain JSON next to the exe. }

procedure WriteNativeHostManifest();
var
  ManifestPath, ExePath, Json: string;
begin
  ManifestPath := ExpandConstant('{app}\native-host.json');
  ExePath := ExpandConstant('{app}\Memo AI.exe');
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
  if CurStep = ssPostInstall then
  begin
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
