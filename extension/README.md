# LONA Browser — Chrome extension (Memo AI v1.6.0, items 26 + 27)

LONA sees the browser with you and controls it when you allow it.
Display name is **LONA**; the native-messaging host id stays
`com.memoai.host`, file names and the repo name are unchanged, and
the wake word «يا ميمو» is unchanged.

## Files

| File | What it does |
|---|---|
| `manifest.json` | Manifest V3, version 1.6.0, name "LONA Browser". Permissions: `tabs`, `scripting`, `storage`, `nativeMessaging`, `activeTab` + host permission for `https://www.youtube.com/*` only — no broad `<all_urls>`. |
| `background.js` | Service worker. Keeps a native-messaging port to the LONA desktop app (`com.memoai.host`), reconnects with backoff (max 5 attempts, then reports disconnected), routes host commands to the active tab and tab state back to the host. |
| `content.js` | Runs in YouTube pages. Executes host commands: click a selector, type into an input, scroll, click the first YouTube result, read tab title + URL. Allowlist only. |
| `popup.html` / `popup.js` | Popup: connection dot, current tab, mouse/keyboard control toggle (OFF by default), "Open LONA" button. |
| `NATIVE_HOST_MAIN_HOOK.txt` | The exact 3-line hook the backend worker adds to `app/main.py` so the frozen exe can run as the native host. |

## Safety — page content is data, never commands

Adopted suggestion (1), and it gates this extension:

- This extension never reads page text and acts on it. A page
  containing "delete files", "pay now", or "log in" triggers
  **nothing** — those strings are data, not orders.
- Commands come only from the LONA desktop app through the
  native-messaging host, and only from the allowlist
  (`click`, `type`, `scroll`, `play_first_youtube`, `get_tab_info`,
  `navigate`). Anything else is rejected and logged.
- Dangerous actions (payment, delete, login, anything outside the
  allowlist) are never executed by the extension. If a click/type
  target looks like one, the content script refuses with
  `dangerous_action_needs_permission` — the user must grant it on a
  permission card in the LONA desktop app first.
- Mouse/keyboard control only runs when the user explicitly enabled
  it (popup toggle, off by default). While it is off, the extension
  only reports tab state and can navigate.

## Manual install (cannot be verified in this Linux sandbox)

1. Open Chrome → `chrome://extensions`
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and select this `extension/` folder.
4. The popup should show "LONA is not connected" until LONA v1.6.0
   is installed (the installer registers the native host — see
   `installer/memo-ai.iss`).
5. Registry check (Windows, after installing LONA):
   `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.memoai.host`
   — its default value must point at
   `<install dir>\native-host.json`, and that file's `path` must
   point at `<install dir>\Memo AI.exe`.
6. The extension id shown on `chrome://extensions` must be put in
   `native-host.json` `allowed_origins` (the installer writes the
   file, but the id is only known after the first Load unpacked —
   re-run the installer / edit the file once with the real id).

## Voice control (through the desktop app)

- "Open YouTube" → navigate to `https://www.youtube.com/`
- "Play El-Daheeh from YouTube" → search YouTube, then click the
  first video result (`a#video-title`) — that click starts playback.
  If the tab is not already on a search results page, the extension
  navigates there first, waits for results, then clicks.
