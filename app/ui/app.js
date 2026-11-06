// Memo AI v1 - main window logic
// Talks to Python through window.pywebview.api (pywebview bridge).
// If opened in a normal browser (no bridge), the UI still renders and
// shows a notice - this is only for testing the layout.
//
// v1.2.4 FIX: the bridge is NEVER captured once. On the MSHTML fallback
// engine (used while WebView2 is missing) window.pywebview is injected
// AFTER DOMContentLoaded, so a single capture at init stayed null forever
// and every button silently did nothing. getApi() resolves the bridge AT
// CALL TIME, so late injection just works. Any handler that finds no
// bridge shows a visible message instead of failing silently.

const I18N = {
  ar: {
    subtitle: "محلي • آمن • أوفلاين",
    input_placeholder: "اكتب رسالتك هنا...",
    mic_hint: "اضغط على المايك وتكلم 5 ثواني",
    settings_title: "الإعدادات",
    sec_general: "عام",
    ai_name: "اسم المساعد",
    user_name: "المساعد يناديك بإيه؟",
    language: "لغة الواجهة",
    greeting_toggle: "التحية الصباحية عند التشغيل (مع الطقس)",
    pet_toggle: "إظهار الحيوان الأليف على الشاشة",
    sec_voice: "الصوت — 4 أصوات",
    v_boy1: "ولد 1", v_boy1_style: "هادي ورزين",
    v_boy2: "ولد 2", v_boy2_style: "مرح وخفيف",
    v_girl1: "بنت 1", v_girl1_style: "دافية وودودة",
    v_girl2: "بنت 2", v_girl2_style: "عملية وسريعة",
    voice_hint: "الأصوات الأونلاين من Gemini، ولو مفيش نت الصوت الاحتياطي Piper (صوت واحد عربي).",
    sec_avatar: "الأفاتار",
    av_cat: "قطة", av_dog: "كلب", av_boy: "ولد", av_girl: "بنت",
    soon: "قريباً", soon2: "قريباً", soon3: "قريباً",
    sec_owner: "وضع المالك",
    owner_code: "الكود السري (بتكتبه في الشات عشان تفتح وضع المالك)",
    owner_hint: "الكود بيتخزن على جهازك بس، ومش بيظهر في أي سجل. سيبه فاضي لو مش عايز تغيّره.",
    sec_logs: "سجل التشخيص",
    logs_refresh: "تحديث", logs_copy: "نسخ السجل",
    sec_updates: "التحديثات",
    update_idle: "اضغط «التحقق من التحديثات» عشان تشوف لو فيه نسخة جديدة.",
    check_updates: "التحقق من التحديثات", install_update: "تثبيت التحديث",
    update_checking: "بشوف لو فيه تحديث...",
    update_not_configured: "التحديثات مش مفعّلة لسه — مفيش رابط تحديثات متظبط.",
    update_up_to_date: "إنت على أحدث نسخة ✅",
    update_available: "فيه تحديث جديد!",
    update_installing: "بيثبّت التحديث وبيعاد التشغيل...",
    update_error: "مقدرتش أتحقق من التحديثات — شوف السجل",
    confirm_install: "هينزّل التحديث ويثبّته ويعيد تشغيل البرنامج. كمّل؟",
    sec_about: "عن المطور",
    about_by: "صمم بواسطة ENG/Mohamed El Sayed",
    visit_site: "زور الموقع",
    todo_note: "قيد التطوير (v2): ترجمة الشاشة، واتساب، المجلس متعدد النماذج، تنبيهات الجيميل والتقويم في التحية الصباحية، أصوات XTTS، وموديل 7B.",
    quit: "إغلاق البرنامج", save: "حفظ",
    online: "Online", offline: "Offline",
    listening: "🎙️ بسمعك... اتكلم دلوقتي",
    thinking: "بيفكر...",
    speaking: "بيتكلم...",
    hello: "أهلاً يا محمد! أنا Memo — اكتب أو اضغط المايك وتكلم.",
    heard_prefix: "سمعتك: ",
    no_bridge: "الواجهة شغالة في وضع المعاينة (من غير بايثون) — شغّل python app/main.py للاستخدام الكامل.",
    bridge_not_ready: "البرنامج لسه بيجهز — استنى ثانية ودوس تاني",
    log_unavailable: "السجل مش متاح دلوقتي",
    copied: "اتنسخ السجل ✅",
    saved: "اتحفظت الإعدادات ✅",
    confirm_quit: "متأكد إنك عايز تقفل Memo AI خالص؟",
  },
  en: {
    subtitle: "Local • Secure • Offline",
    input_placeholder: "Type your message here...",
    mic_hint: "Tap the mic and speak for 5 seconds",
    settings_title: "Settings",
    sec_general: "General",
    ai_name: "Assistant name",
    user_name: "What should I call you?",
    language: "Interface language",
    greeting_toggle: "Morning greeting on startup (with weather)",
    pet_toggle: "Show the desktop pet",
    sec_voice: "Voice — 4 voices",
    v_boy1: "Boy 1", v_boy1_style: "Calm and steady",
    v_boy2: "Boy 2", v_boy2_style: "Playful and light",
    v_girl1: "Girl 1", v_girl1_style: "Warm and friendly",
    v_girl2: "Girl 2", v_girl2_style: "Practical and fast",
    voice_hint: "Online voices come from Gemini; offline fallback is Piper (one Arabic voice).",
    sec_avatar: "Avatar",
    av_cat: "Cat", av_dog: "Dog", av_boy: "Boy", av_girl: "Girl",
    soon: "Soon", soon2: "Soon", soon3: "Soon",
    sec_owner: "Owner mode",
    owner_code: "Secret code (type it in chat to unlock owner mode)",
    owner_hint: "The code is stored on your device only and never appears in any log. Leave empty to keep it unchanged.",
    sec_logs: "Diagnostics log",
    logs_refresh: "Refresh", logs_copy: "Copy log",
    sec_updates: "Updates",
    update_idle: "Tap “Check for updates” to see if a new version is available.",
    check_updates: "Check for updates", install_update: "Install update",
    update_checking: "Checking for updates...",
    update_not_configured: "Updates aren't enabled yet — no update URL is configured.",
    update_up_to_date: "You're on the latest version ✅",
    update_available: "A new update is available!",
    update_installing: "Installing the update and restarting...",
    update_error: "Couldn't check for updates — see the log",
    confirm_install: "This will download, install the update and restart the app. Continue?",
    sec_about: "About the developer",
    about_by: "Designed by ENG/Mohamed El Sayed",
    visit_site: "Visit website",
    todo_note: "In development (v2): screen translation, WhatsApp, multi-model council, Gmail/calendar in the morning briefing, XTTS voices, and the 7B model.",
    quit: "Quit the app", save: "Save",
    online: "Online", offline: "Offline",
    listening: "🎙️ Listening... speak now",
    thinking: "Thinking...",
    speaking: "Speaking...",
    hello: "Hi Mohamed! I'm Memo — type or tap the mic and talk.",
    heard_prefix: "Heard: ",
    no_bridge: "Preview mode (no Python backend) — run python app/main.py for the full app.",
    bridge_not_ready: "The app is still starting — wait a second and tap again",
    log_unavailable: "Log isn't available right now",
    copied: "Log copied ✅",
    saved: "Settings saved ✅",
    confirm_quit: "Are you sure you want to quit Memo AI completely?",
  }
};

let LANG = "ar";
let busy = false;
let selectedVoice = "girl_warm";

function t(key) { return (I18N[LANG] && I18N[LANG][key]) || I18N.ar[key] || key; }
// v1.2.4: lazy bridge accessor — resolved AT CALL TIME, never captured.
// (On the MSHTML fallback engine the bridge is injected after
// DOMContentLoaded, so a single capture at init stayed null forever.)
function getApi() { return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null; }
function $(id) { return document.getElementById(id); }

// v1.2.2: report the browser engine to the diagnostics log, so we know
// for sure whether the windows are drawn by WebView2 (UA has "Edg/")
// or by the old MSHTML engine (UA has "MSIE"/"Trident/") — the latter
// cannot do transparency and paints the dark rectangle.
let _uaLogged = false;
function logEngine() {
  if (_uaLogged) return;
  const bridge = getApi();
  if (!bridge || !bridge.log_js) return;
  _uaLogged = true;
  try { bridge.log_js("UA: " + navigator.userAgent); } catch (e) {}
}

function applyLang() {
  document.documentElement.lang = LANG;
  document.documentElement.dir = LANG === "ar" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    el.placeholder = t(el.getAttribute("data-i18n-ph"));
  });
  $("avatar-name").textContent = ($("set-ai-name").value || "Memo") + " 🐱";
}

// Called from Python after settings change.
function applySettingsFromPython() { refreshStatus(); }

function setSpeaking(on) {
  $("avatar-frame").classList.toggle("speaking", !!on);
  if (on) $("status").textContent = t("speaking");
  else if (!busy) $("status").textContent = "";
}

function addMsg(text, who, meta) {
  const chat = $("chat");
  const div = document.createElement("div");
  div.className = "msg " + who;
  const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  div.textContent = text;
  const m = document.createElement("span");
  m.className = "meta";
  m.textContent = (meta || "") + (meta ? " • " : "") + time;
  div.appendChild(m);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function showTyping() {
  const chat = $("chat");
  const div = document.createElement("div");
  div.className = "msg ai typing";
  div.id = "typing-indicator";
  div.innerHTML = "<span></span><span></span><span></span>";
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function hideTyping() {
  const el = $("typing-indicator");
  if (el) el.remove();
}

async function sendText(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  busy = true;
  addMsg(text, "user");
  $("text-input").value = "";
  const bridge = getApi();
  if (!bridge) {
    addMsg(t("no_bridge"), "ai", "Memo AI");
    busy = false;
    return;
  }
  showTyping();
  $("status").textContent = t("thinking");
  try {
    const res = await bridge.chat(text);
    hideTyping();
    addMsg(res.reply || "…", "ai", "Memo AI" + (res.engine ? " • " + res.engine : ""));
  } catch (e) {
    hideTyping();
    addMsg("حصلت مشكلة، جرّب تاني.", "ai", "Memo AI");
  }
  $("status").textContent = "";
  busy = false;
}

async function voiceChat() {
  if (busy) return;
  const bridge = getApi();
  if (!bridge) {
    addMsg(t("no_bridge"), "ai", "Memo AI");
    return;
  }
  busy = true;
  const mic = $("mic-btn");
  mic.classList.add("recording");
  $("status").textContent = t("listening");
  $("status").classList.add("listening");
  try {
    const res = await bridge.voice_chat();
    if (res.heard) addMsg(res.heard, "user", t("heard_prefix").trim());
    addMsg(res.reply || "…", "ai", "Memo AI" + (res.engine ? " • " + res.engine : ""));
  } catch (e) {
    addMsg("مقدرتش أسمعك — جرّب تاني.", "ai", "Memo AI");
  }
  mic.classList.remove("recording");
  $("status").textContent = "";
  $("status").classList.remove("listening");
  busy = false;
}

async function refreshStatus() {
  const bridge = getApi();
  if (!bridge) return;
  try {
    const st = await bridge.get_status();
    LANG = (st.settings && st.settings.language) || "ar";
    applyLang();
    const pill = $("net-pill");
    pill.classList.toggle("offline", !st.online);
    $("net-text").textContent = st.online ? t("online") : t("offline");
    if (st.settings) {
      $("set-ai-name").value = st.settings.ai_name || "Memo";
      $("set-user-name").value = st.settings.user_name || "Mohamed";
      $("set-language").value = st.settings.language || "ar";
      $("set-greeting").checked = !!st.settings.greeting_enabled;
      $("set-pet").checked = st.settings.pet_visible !== false;
      selectedVoice = st.settings.voice || "girl_warm";
      document.querySelectorAll(".voice-card").forEach(c =>
        c.classList.toggle("selected", c.dataset.voice === selectedVoice));
      $("avatar-name").textContent = (st.settings.ai_name || "Memo") + " 🐱";
    }
  } catch (e) { /* UI keeps working offline */ }
}

async function refreshLogs() {
  const box = $("log-box");
  const bridge = getApi();
  if (!bridge) { box.textContent = t("no_bridge"); return; }
  try {
    const res = await bridge.get_logs();
    const rows = (res.logs || []).map(e =>
      `[${e.time}] [${e.level}] [${e.category}] ${e.message}`);
    box.textContent = rows.join("\n") || "—";
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.textContent = "…";
  }
}

function init() {
  logEngine();
  // The bridge may arrive late (MSHTML injects it after DOMContentLoaded):
  // re-probe then, and populate the UI as soon as it is available.
  window.addEventListener("pywebviewready", () => { logEngine(); refreshStatus(); });
  if (getApi()) {
    addMsg(t("hello"), "ai", "Memo AI");
    refreshStatus();
  } else {
    addMsg(t("no_bridge"), "ai", "Memo AI");
  }
  setInterval(refreshStatus, 15000);

  $("send-btn").addEventListener("click", () => sendText($("text-input").value));
  $("text-input").addEventListener("keydown", e => {
    if (e.key === "Enter") sendText($("text-input").value);
  });
  $("mic-btn").addEventListener("click", voiceChat);
  $("island-btn").addEventListener("click", () => { const b = getApi(); if (b) b.minimize_to_island(); });
  $("pet-btn").addEventListener("click", async () => {
    const b = getApi();
    if (!b) return;
    const res = await b.toggle_pet();
    $("set-pet").checked = !!res.visible;
  });
  $("settings-btn").addEventListener("click", () => {
    $("settings-modal").classList.add("open");
    refreshLogs();
  });
  $("settings-close").addEventListener("click", () => $("settings-modal").classList.remove("open"));
  $("settings-modal").addEventListener("click", e => {
    if (e.target === $("settings-modal")) $("settings-modal").classList.remove("open");
  });

  document.querySelectorAll(".voice-card").forEach(card => {
    card.addEventListener("click", () => {
      selectedVoice = card.dataset.voice;
      document.querySelectorAll(".voice-card").forEach(c =>
        c.classList.toggle("selected", c === card));
    });
  });
  document.querySelectorAll(".avatar-opt").forEach(opt => {
    opt.addEventListener("click", () => {
      if (opt.dataset.avatar !== "cat") return; // v1: cat only
      document.querySelectorAll(".avatar-opt").forEach(o =>
        o.classList.toggle("selected", o === opt));
    });
  });

  $("settings-save").addEventListener("click", async () => {
    const b = getApi();
    if (!b) { $("status").textContent = t("bridge_not_ready"); return; }
    const payload = {
      ai_name: $("set-ai-name").value.trim() || "Memo",
      user_name: $("set-user-name").value.trim() || "Mohamed",
      language: $("set-language").value,
      voice: selectedVoice,
      greeting_enabled: $("set-greeting").checked,
      pet_visible: $("set-pet").checked,
      owner_code: $("set-owner-code").value || "",
    };
    await b.save_settings(JSON.stringify(payload));
    $("set-owner-code").value = "";
    LANG = payload.language;
    applyLang();
    refreshStatus();
    $("status").textContent = t("saved");
    setTimeout(() => { if (!busy) $("status").textContent = ""; }, 2500);
  });

  $("logs-refresh").addEventListener("click", refreshLogs);
  $("logs-copy").addEventListener("click", async () => {
    const b = getApi();
    if (!b) { $("status").textContent = t("log_unavailable"); return; }
    try {
      const res = await b.get_log_text();
      await navigator.clipboard.writeText(res.text || "");
      $("status").textContent = t("copied");
      setTimeout(() => { if (!busy) $("status").textContent = ""; }, 2500);
    } catch (e) { $("status").textContent = t("log_unavailable"); }
  });
  $("portfolio-btn").addEventListener("click", () => { const b = getApi(); if (b) b.open_portfolio(); });
  $("check-updates-btn").addEventListener("click", async () => {
    const st = $("update-status");
    const installBtn = $("install-update-btn");
    const b = getApi();
    if (!b) { st.textContent = t("bridge_not_ready"); installBtn.style.display = "none"; return; }
    st.textContent = t("update_checking");
    installBtn.style.display = "none";
    try {
      const res = await b.check_updates();
      if (res.status === "not_configured") st.textContent = t("update_not_configured");
      else if (res.status === "up_to_date") st.textContent = t("update_up_to_date") + " (v" + res.current + ")";
      else if (res.status === "update_available") {
        st.textContent = t("update_available") + " v" + res.version + (res.notes ? " — " + res.notes : "");
        installBtn.style.display = "";
      } else st.textContent = t("update_error") + (res.message ? " — " + res.message : "");
    } catch (e) {
      st.textContent = t("update_error");
    }
  });
  $("install-update-btn").addEventListener("click", async () => {
    const b = getApi();
    if (!b) { $("update-status").textContent = t("bridge_not_ready"); return; }
    if (!confirm(t("confirm_install"))) return;
    $("update-status").textContent = t("update_installing");
    try { await b.install_update(); } catch (e) { /* see the log */ }
  });
  $("quit-btn").addEventListener("click", async () => {
    const b = getApi();
    if (!b) { $("status").textContent = t("bridge_not_ready"); return; }
    if (confirm(t("confirm_quit"))) await b.quit_app();
  });
}

document.addEventListener("DOMContentLoaded", init);
