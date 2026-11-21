// LONA Browser — background service worker (Manifest V3).
//
// One job: keep a native-messaging port to the LONA desktop app
// (host id "com.memoai.host" — the id is NOT renamed, only the
// display name changed) and route messages both ways:
//   desktop app -> host -> this worker -> content script (active tab)
//   content script -> this worker -> host -> desktop app
//
// Mouse/keyboard control is OFF by default. It only turns on when the
// user taps the toggle in the popup, or grants it from the desktop
// app. While it is off, this worker only reports tab state and
// navigates — it never clicks, types, or plays anything.

const HOST_NAME = "com.memoai.host";
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_BASE_MS = 1000;

let port = null;          // chrome.runtime.Port to the native host
let reconnectAttempts = 0;
let reconnectTimer = null;
let hostConnected = false;
let controlEnabled = false;
let lastTabInfo = { title: "", url: "" };

function setConnected(value) {
  hostConnected = value;
  chrome.storage.local.set({
    lona_host_connected: value,
    lona_last_tab: lastTabInfo,
  });
}

function loadControlState() {
  chrome.storage.local.get(
    ["lona_browser_control_enabled"],
    (res) => { controlEnabled = res.lona_browser_control_enabled === true; },
  );
}

function connectNative() {
  if (port) return;
  try {
    port = chrome.runtime.connectNative(HOST_NAME);
  } catch (err) {
    // connectNative throws synchronously when the host is not
    // registered (e.g. LONA was installed before v1.6.0).
    port = null;
    onDisconnect(err);
    return;
  }
  port.onMessage.addListener(onHostMessage);
  port.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError;
    port = null;
    onDisconnect(err);
  });
  setConnected(true);
  reconnectAttempts = 0;
  // Ask the tab for its current state as soon as we are up.
  refreshTabInfo();
}

function onDisconnect(err) {
  setConnected(false);
  if (err) {
    console.warn("LONA native host disconnected:", err.message || err);
  }
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    // Give up quietly and say so in storage; the popup shows
    // "disconnected" and the user can retry by reopening the popup.
    chrome.storage.local.set({ lona_host_error: "disconnected" });
    return;
  }
  reconnectAttempts += 1;
  const delay = RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts - 1);
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connectNative, delay);
}

function postToHost(msg) {
  if (!port) return false;
  try {
    port.postMessage(msg);
    return true;
  } catch (err) {
    console.warn("LONA: could not post to native host:", err);
    return false;
  }
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function refreshTabInfo() {
  const tab = await getActiveTab();
  if (tab) {
    lastTabInfo = { title: tab.title || "", url: tab.url || "" };
    chrome.storage.local.set({ lona_last_tab: lastTabInfo });
    postToHost({ type: "tab_state", title: lastTabInfo.title, url: lastTabInfo.url });
  }
}

function waitForTabComplete(tabId, timeoutMs = 15000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(false);
    }, timeoutMs);
    function listener(updatedId, info) {
      if (updatedId === tabId && info.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(true);
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

// Commands that touch the page (click / type / play) need the control
// toggle ON. Navigation and tab-state reads do not.
const CONTROL_COMMANDS = new Set(["click", "type", "scroll", "play_first_youtube"]);

// Dangerous actions (adopted suggestion 1): payment, delete, login,
// anything outside the command allowlist. They are NEVER executed
// here — they need an explicit permission card in the LONA desktop
// app first. Page content is data, never commands: this worker never
// parses a tab title or URL as an instruction.
const DANGEROUS_COMMANDS = new Set([
  "pay", "payment", "delete", "remove", "login", "log_in", "sign_in",
  "purchase", "buy", "checkout", "transfer", "withdraw", "send_money",
  "run", "eval", "exec", "download", "upload", "install",
]);

async function sendToActiveTab(msg, timeoutMs = 10000) {
  const tab = await getActiveTab();
  if (!tab || tab.id == null) return { ok: false, error: "no_active_tab" };
  try {
    const reply = await Promise.race([
      chrome.tabs.sendMessage(tab.id, msg),
      new Promise((resolve) =>
        setTimeout(() => resolve({ ok: false, error: "content_timeout" }), timeoutMs)),
    ]);
    return reply || { ok: false, error: "no_content_reply" };
  } catch (err) {
    return { ok: false, error: "content_unavailable", detail: String(err && err.message || err) };
  }
}

function youtubeSearchUrl(query) {
  return "https://www.youtube.com/results?search_query=" + encodeURIComponent(query);
}

async function handlePlayFirstYoutube(msg) {
  const query = (msg.query || "").trim();
  if (!query) return { ok: false, error: "missing_query" };
  const tab = await getActiveTab();
  if (!tab || tab.id == null) return { ok: false, error: "no_active_tab" };
  const searchUrl = youtubeSearchUrl(query);
  const onSearchPage = (tab.url || "").startsWith("https://www.youtube.com/results");
  if (!onSearchPage) {
    // Plain URL open only lands on search results; auto-play needs the
    // extension, so navigate first, wait for results, then click.
    await chrome.tabs.update(tab.id, { url: searchUrl });
    await waitForTabComplete(tab.id);
    // Give the results list a moment to render.
    await new Promise((r) => setTimeout(r, 1200));
  }
  const result = await sendToActiveTab({ cmd: "play_first_youtube", query });
  await refreshTabInfo();
  return result;
}

async function onHostMessage(msg) {
  if (!msg || typeof msg !== "object") return;
  // Messages from the desktop app carry a "cmd". Anything else is
  // ignored here (tab-state echoes come from the content script).
  const cmd = msg.cmd;
  if (!cmd) {
    if (msg.type === "set_control_enabled") {
      controlEnabled = msg.enabled === true;
      chrome.storage.local.set({ lona_browser_control_enabled: controlEnabled });
      postToHost({ type: "control_state", enabled: controlEnabled, id: msg.id });
    }
    return;
  }
  const id = msg.id || null;
  let reply;
  try {
    if (DANGEROUS_COMMANDS.has(cmd)) {
      reply = { ok: false, error: "dangerous_action_needs_permission", cmd,
                detail: "Payment, delete, login and similar actions need an explicit permission card in the LONA desktop app first." };
    } else if (CONTROL_COMMANDS.has(cmd) && !controlEnabled) {
      reply = { ok: false, error: "control_disabled",
                detail: "Browser control is off — turn it on in the LONA popup." };
    } else if (cmd === "navigate") {
      const tab = await getActiveTab();
      if (!tab || tab.id == null) reply = { ok: false, error: "no_active_tab" };
      else {
        await chrome.tabs.update(tab.id, { url: msg.url });
        reply = { ok: true, cmd, url: msg.url };
        await refreshTabInfo();
      }
    } else if (cmd === "play_first_youtube") {
      reply = await handlePlayFirstYoutube(msg);
      reply.cmd = "play_first_youtube";
    } else if (cmd === "get_tab_info") {
      await refreshTabInfo();
      reply = { ok: true, cmd, title: lastTabInfo.title, url: lastTabInfo.url };
    } else if (cmd === "ping") {
      reply = { ok: true, cmd, control_enabled: controlEnabled };
    } else if (cmd === "click" || cmd === "type" || cmd === "scroll") {
      reply = await sendToActiveTab(msg);
      reply.cmd = cmd;
    } else {
      reply = { ok: false, error: "unknown_command", cmd };
    }
  } catch (err) {
    reply = { ok: false, error: "host_error", detail: String(err && err.message || err) };
  }
  reply.id = id;
  postToHost(reply);
}

// Messages from the content script (tab state) and the popup.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg !== "object") return false;
  if (msg.type === "tab_state" && sender.tab) {
    lastTabInfo = { title: msg.title || "", url: msg.url || "" };
    chrome.storage.local.set({ lona_last_tab: lastTabInfo });
    postToHost({ type: "tab_state", title: lastTabInfo.title, url: lastTabInfo.url });
    return false;
  }
  if (msg.type === "popup_get_state") {
    getActiveTab().then((tab) => {
      if (tab) lastTabInfo = { title: tab.title || "", url: tab.url || "" };
      sendResponse({
        connected: hostConnected,
        control_enabled: controlEnabled,
        title: lastTabInfo.title,
        url: lastTabInfo.url,
      });
    });
    return true; // async sendResponse
  }
  if (msg.type === "popup_set_control") {
    controlEnabled = msg.enabled === true;
    chrome.storage.local.set({ lona_browser_control_enabled: controlEnabled });
    postToHost({ type: "control_state", enabled: controlEnabled });
    sendResponse({ ok: true, control_enabled: controlEnabled });
    return false;
  }
  if (msg.type === "popup_open_lona") {
    // The extension cannot launch the desktop app by itself; it tells
    // the host, which logs the request, and the popup tells the user
    // to open LONA from the tray if it does not come forward.
    postToHost({ type: "open_app_request" });
    sendResponse({ ok: true });
    return false;
  }
  if (msg.type === "popup_retry_connect") {
    reconnectAttempts = 0;
    clearTimeout(reconnectTimer);
    port = null;
    connectNative();
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

chrome.tabs.onActivated.addListener(refreshTabInfo);
chrome.tabs.onUpdated.addListener((_tabId, info, tab) => {
  if (tab.active && info.status === "complete") refreshTabInfo();
});

loadControlState();
connectNative();
