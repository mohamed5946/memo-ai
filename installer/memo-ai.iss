; Memo AI — Inno Setup 6 script
; Build (from the repo root, on Windows, after PyInstaller):
;   ISCC installer\memo-ai.iss
;   or with a version override:  ISCC /DAppVersion=1.2.1 installer\memo-ai.iss
; Output: release\MemoAI-Setup-<version>.exe
;
; Per-user install to %LOCALAPPDATA%\Programs\Memo AI — no admin needed.
; The installer never deletes keys\: it only copies the program files, and
; on a fresh install it migrates any *.txt keys (except README.txt) from an
; old zip install at Downloads\memo-ai\keys\ (never overwriting keys that
; already exist in the new location).

#ifndef AppVersion
  #define AppVersion "1.2.1"
#endif

#define AppName "Memo AI"
#define AppPublisher "Mohamed El Sayed"
#define AppURL "https://github.com/mohamed5946/memo-ai"

[Setup]
AppId={{7BBF7DD9-42C6-41D0-BB76-99A5E19569A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
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

[Icons]
Name: "{group}\Memo AI"; Filename: "{app}\Memo AI.exe"
Name: "{userdesktop}\Memo AI"; Filename: "{app}\Memo AI.exe"; Tasks: desktopicon

[Run]
; After install, offer to run the app (it starts as the Dynamic Island only).
; Skipped on silent installs (the updater relaunches the app itself).
Filename: "{app}\Memo AI.exe"; Description: "{cm:LaunchProgram,Memo AI}"; Flags: nowait postinstall skipifsilent

[Code]
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

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    MigrateKeysFromZipInstall();
end;
