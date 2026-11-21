// LONA Browser — popup. Shows connection state + current tab and
// owns the mouse/keyboard control toggle (OFF by default; only the
// user's explicit tap here — or a permission grant from the LONA
// desktop app — turns it on).

function refresh() {
  chrome.runtime.sendMessage({ type: "popup_get_state" }, (state) => {
    if (!state) return;
    const dot = document.getElementById("dot");
    const status = document.getElementById("statusText");
    dot.className = "dot " + (state.connected ? "on" : "off");
    status.textContent = state.connected
      ? "LONA is connected"
      : "LONA is not connected";
    document.getElementById("tabTitle").textContent = state.title || "—";
    document.getElementById("tabTitle").title = state.url || "";
    document.getElementById("controlToggle").checked = state.control_enabled === true;
  });
}

document.getElementById("controlToggle").addEventListener("change", (e) => {
  chrome.runtime.sendMessage(
    { type: "popup_set_control", enabled: e.target.checked },
    () => refresh(),
  );
});

document.getElementById("openLona").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "popup_open_lona" }, () => {
    document.getElementById("openHint").textContent =
      "LONA was notified. If it does not come forward, open it from the Start menu or the system tray.";
  });
});

document.getElementById("retry").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "popup_retry_connect" }, () => {
    setTimeout(refresh, 800);
  });
});

refresh();
