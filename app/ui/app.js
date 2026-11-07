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
    autostart_toggle: "تشغيل Memo AI تلقائياً مع بدء ويندوز (يفتح النافذة الرئيسية)",
    menu_general: "عام", menu_whatsapp: "WhatsApp", menu_voices: "الأصوات",
    menu_avatar: "الأفاتار", menu_logs: "سجل التشخيص", menu_updates: "التحديثات",
    back: "→ رجوع",
    speaker: "صوت المساعد",
    owner_code: "كود المالك", owner_code_ph: "اكتب الكود السري",
    owner_unlock: "تفعيل وضع المالك",
    owner_unlocked: "وضع المالك اتفتح ✅", owner_wrong: "الكود غلط",
    wa_status_off_enabled: "الواتساب مفعّل بس الخدمة مش شغالة — جرّب تقفل الإعدادات وتفتحها تاني أو أعد تشغيل البرنامج.",
    wa_save_error: "مقدرتش أحفظ إعدادات الواتساب: ",
    sec_whatsapp: "واتساب — ربط كجهاز مرتبط",
    wa_hint: "Memo AI بيتربط بواتساب الرقم التالت اللي شبه مش بيستخدمه (مش رقم الشغل ومش الرقم الأساسي) كجهاز مرتبط عبر QR — زي WhatsApp Web. الرقم الأساسي اللي هتكتبه تحت بيقدر يأمر اللابتوب، وأي رقم تاني بياخد ردود شات بس.",
    wa_toggle: "تفعيل ربط الواتساب",
    wa_owner_label: "رقمك الأساسي (owner) — اللي يأمر اللابتوب من الواتساب",
    wa_status_off: "الواتساب مش مفعّل.",
    wa_status_unavailable: "الواتساب مش متاح في النسخة دي.",
    wa_status_connecting: "بيتصل بواتساب...",
    wa_status_waiting: "امسح الـ QR من واتساب على الرقم التالت.",
    wa_status_linked: "مربوط ✅ — الرقم: ",
    wa_status_disconnected: "اتقطع الاتصال — بيحاول يرجع لوحده...",
    wa_status_error: "فيه مشكلة في الواتساب — شوف السجل.",
    wa_scan_hint: "افتح واتساب على الرقم التالت ← الإعدادات ← الأجهزة المرتبطة ← ربط جهاز، وامسح الكود ده.",
    wa_disconnect: "فصل وإلغاء الربط",
    wa_owner_missing: "⚠️ اكتب رقمك الأساسي فوق عشان أوامر الواتساب تشتغل — من غيره كل الرسايل هتاخد ردود شات بس.",
    sec_voice: "الأصوات",
    voice_output_on: "صوت المساعد شغال 🔊",
    voice_output_off: "صوت المساعد مقفول 🔇",
    v_boy1: "ولد 1", v_boy1_style: "هادي ورزين",
    v_boy2: "ولد 2", v_boy2_style: "مرح وخفيف",
    v_girl1: "بنت 1", v_girl1_style: "دافية وودودة",
    v_girl2: "بنت 2", v_girl2_style: "عملية وسريعة",
    voice_hint: "الصوت اللي تختاره بيتحفظ على طول وبيتطبق بعد إعادة التشغيل. الأصوات الأونلاين من Gemini، ولو مفيش نت الصوت الاحتياطي Piper (صوت واحد عربي).",
    sec_avatar: "الأفاتار",
    av_cat: "قطة", av_dog: "كلب", av_boy: "ولد", av_girl: "بنت",
    avatar_hint: "الشخصية اللي تختارها بتتحفظ على طول وبتتطبق بعد إعادة التشغيل.",
    sec_logs: "سجل التشخيص",
    perm_title: "طلب تنفيذ إجراء ⚠️",
    perm_allow: "سماح",
    perm_deny: "رفض",
    logs_refresh: "تحديث", logs_copy: "نسخ السجل",
    sec_updates: "التحديثات",
    update_idle: "اضغط «التحقق من التحديثات» عشان تشوف لو فيه نسخة جديدة.",
    check_updates: "التحقق من التحديثات", install_update: "تثبيت التحديث",
    update_checking: "بشوف لو فيه تحديث...",
    update_not_configured: "التحديثات مش مفعّلة لسه — مفيش رابط تحديثات متظبط.",
    update_up_to_date: "إنت على أحدث نسخة ✅",
    update_available: "فيه تحديث جديد!",
    update_installing: "بيثبّت التحديث وبيعاد التشغيل...",
    update_downloading: "بينزّل التحديث...",
    update_downloaded: "نزل",
    update_of: "من",
    update_verifying: "بيتحقق من التحديث...",
    update_done: "التحديث نزل وبيتثبّت — البرنامج هيعيد التشغيل...",
    update_failed: "التحديث فشل — شوف السجل",
    update_error: "مقدرتش أتحقق من التحديثات — شوف السجل",
    confirm_install: "هينزّل التحديث ويثبّته ويعيد تشغيل البرنامج. كمّل؟",
    sec_about: "عن المطور",
    about_by: "صمم بواسطة ENG/Mohamed El Sayed",
    visit_site: "زور الموقع", github: "GitHub",
    version_label: "الإصدار الحالي: ",
    save_failed: "مقدرتش أحفظ — جرّب تاني",
    reply_failed: "حصلت مشكلة وأنا بفكر — جرّب تاني، ولو اتكررت شوف السجل.",
    todo_note: "قيد التطوير (v2): ترجمة الشاشة، المجلس متعدد النماذج، تنبيهات الجيميل والتقويم في التحية الصباحية، أصوات XTTS، وموديل 7B.",
    quit: "إغلاق البرنامج", save: "حفظ",
    online: "Online", offline: "Offline",
    listening: "🎙️ بسمعك... اتكلم دلوقتي",
    thinking: "بيفكر...",
    speaking: "بيتكلم...",
    hello: "أهلاً يا محمد! أنا Memo — اكتب أو اضغط المايك وتكلم.",
    heard_prefix: "سمعتك: ",
    no_bridge: "مش قادر أوصل لمحرك البرنامج — اقفل البرنامج وافتحه تاني، ولو المشكلة فضلت ثبّت WebView2",
    bridge_not_ready: "البرنامج لسه بيجهز — استنى ثانية ودوس تاني",
    bridge_files_missing: "ملفات محرك البرنامج ناقصة — حدّث البرنامج أو أعد تثبيته",
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
    autostart_toggle: "Start Memo AI automatically with Windows (opens the main window)",
    menu_general: "General", menu_whatsapp: "WhatsApp", menu_voices: "Voices",
    menu_avatar: "Avatar", menu_logs: "Diagnostics log", menu_updates: "Updates",
    back: "→ Back",
    speaker: "Assistant voice",
    owner_code: "Owner code", owner_code_ph: "Type the secret code",
    owner_unlock: "Unlock owner mode",
    owner_unlocked: "Owner mode unlocked ✅", owner_wrong: "Wrong code",
    wa_status_off_enabled: "WhatsApp is enabled but the service isn't running — reopen Settings or restart the app.",
    wa_save_error: "Couldn't save WhatsApp settings: ",
    sec_whatsapp: "WhatsApp — link as a device",
    wa_hint: "Memo AI links to the barely-used THIRD number (not the work number, not the main number) as a linked device via QR — like WhatsApp Web. The main number you type below can command the laptop; any other number only gets chat replies.",
    wa_toggle: "Enable WhatsApp linking",
    wa_owner_label: "Your main number (owner) — the one that commands the laptop from WhatsApp",
    wa_status_off: "WhatsApp is off.",
    wa_status_unavailable: "WhatsApp is not available in this build.",
    wa_status_connecting: "Connecting to WhatsApp...",
    wa_status_waiting: "Scan the QR from WhatsApp on the third number.",
    wa_status_linked: "Linked ✅ — Number: ",
    wa_status_disconnected: "Connection lost — reconnecting on its own...",
    wa_status_error: "WhatsApp has a problem — see the log.",
    wa_scan_hint: "Open WhatsApp on the THIRD number → Settings → Linked devices → Link a device, and scan this code.",
    wa_disconnect: "Disconnect and unlink",
    wa_owner_missing: "⚠️ Type your main number above so WhatsApp commands work — without it every message only gets chat replies.",
    sec_voice: "Voices",
    voice_output_on: "Assistant voice on 🔊",
    voice_output_off: "Assistant voice off 🔇",
    v_boy1: "Boy 1", v_boy1_style: "Calm and steady",
    v_boy2: "Boy 2", v_boy2_style: "Playful and light",
    v_girl1: "Girl 1", v_girl1_style: "Warm and friendly",
    v_girl2: "Girl 2", v_girl2_style: "Practical and fast",
    voice_hint: "The voice you pick is saved right away and applies after restart. Online voices come from Gemini; offline fallback is Piper (one Arabic voice).",
    sec_avatar: "Avatar",
    av_cat: "Cat", av_dog: "Dog", av_boy: "Boy", av_girl: "Girl",
    avatar_hint: "The avatar you pick is saved right away and applies after restart.",
    sec_logs: "Diagnostics log",
    perm_title: "Action request ⚠️",
    perm_allow: "Allow",
    perm_deny: "Deny",
    logs_refresh: "Refresh", logs_copy: "Copy log",
    sec_updates: "Updates",
    update_idle: "Tap “Check for updates” to see if a new version is available.",
    check_updates: "Check for updates", install_update: "Install update",
    update_checking: "Checking for updates...",
    update_not_configured: "Updates aren't enabled yet — no update URL is configured.",
    update_up_to_date: "You're on the latest version ✅",
    update_available: "A new update is available!",
    update_installing: "Installing the update and restarting...",
    update_downloading: "Downloading the update...",
    update_downloaded: "downloaded",
    update_of: "of",
    update_verifying: "Verifying the update...",
    update_done: "Update downloaded, installing — the app will restart...",
    update_failed: "Update failed — see the log",
    update_error: "Couldn't check for updates — see the log",
    confirm_install: "This will download, install the update and restart the app. Continue?",
    sec_about: "About the developer",
    about_by: "Designed by ENG/Mohamed El Sayed",
    visit_site: "Visit website", github: "GitHub",
    version_label: "Current version: ",
    save_failed: "Couldn't save — try again",
    reply_failed: "Something went wrong while thinking — try again, and check the log if it repeats.",
    todo_note: "In development (v2): screen translation, multi-model council, Gmail/calendar in the morning briefing, XTTS voices, and the 7B model.",
    quit: "Quit the app", save: "Save",
    online: "Online", offline: "Offline",
    listening: "🎙️ Listening... speak now",
    thinking: "Thinking...",
    speaking: "Speaking...",
    hello: "Hi Mohamed! I'm Memo — type or tap the mic and talk.",
    heard_prefix: "Heard: ",
    no_bridge: "Can't reach the app engine — restart the app, and if it persists, install WebView2.",
    bridge_not_ready: "The app is still starting — wait a second and tap again",
    bridge_files_missing: "App engine files are missing — update or reinstall the app",
    log_unavailable: "Log isn't available right now",
    copied: "Log copied ✅",
    saved: "Settings saved ✅",
    confirm_quit: "Are you sure you want to quit Memo AI completely?",
  }
};

let LANG = "ar";
let busy = false;
let selectedVoice = "girl_warm";
let selectedAvatar = "cat";  // v1.4.1: the avatar pick is real state now
let voiceOutput = true;      // v1.3.0 speaker toggle (OUTPUT only - mic is separate)
let currentPermId = null;    // id of the permission request shown in the modal
let permPollTimer = null;    // polls for Allow/Deny requests while a tool chat runs

// v1.4.1: avatar images (all 4 are real now - dog/boy/girl shipped in
// ui/assets) + the emoji shown next to the name.
const AVATAR_IMAGES = {
  cat: "assets/avatar-cat.png", dog: "assets/avatar-dog.png",
  boy: "assets/avatar-boy.png", girl: "assets/avatar-girl.png",
};
const AVATAR_EMOJI = { cat: "🐱", dog: "🐶", boy: "👦", girl: "👧" };

// v1.4.1: apply the picked avatar everywhere (header logo + hero +
// name line). Called on load (restore) and on every pick.
function applyAvatar() {
  const img = AVATAR_IMAGES[selectedAvatar] || AVATAR_IMAGES.cat;
  const logoImg = document.querySelector("header .logo img");
  if (logoImg) logoImg.src = img;
  const heroImg = document.querySelector("#avatar-frame img");
  if (heroImg) heroImg.src = img;
  const nameEl = $("avatar-name");
  if (nameEl) nameEl.textContent = ($("set-ai-name").value || "Memo") + " " +
    (AVATAR_EMOJI[selectedAvatar] || "🐱");
}

// v1.4.1: save a settings payload RIGHT AWAY (voice/avatar picks,
// speaker toggle) and show a brief "saved" note in the given span.
// Returns true on success.
async function saveNow(payload, statusId) {
  const bridge = getApi();
  const el = statusId ? $(statusId) : null;
  if (!bridge) return false;
  try {
    const res = await bridge.save_settings(JSON.stringify(payload));
    if (res && res.ok) {
      if (el) {
        el.textContent = t("saved");
        setTimeout(() => { el.textContent = ""; }, 2000);
      }
      return true;
    }
  } catch (e) { /* fall through */ }
  if (el) {
    el.textContent = t("save_failed");
    setTimeout(() => { el.textContent = ""; }, 3000);
  }
  return false;
}

// v1.4.1: a reply must NEVER render as the literal text "none" /
// "None" / "null" (a Python None leaking into the UI - the v1.4.0
// field bug). Any missing/empty/null-ish reply becomes a human
// Arabic error message instead.
function safeReply(reply) {
  if (typeof reply !== "string") return t("reply_failed");
  const clean = reply.trim();
  const low = clean.toLowerCase();
  if (!clean || low === "none" || low === "null" || low === "undefined") {
    return t("reply_failed");
  }
  return reply;
}

// v1.4.1: the engine badge in the message meta shows ONLY a real
// engine ("groq"/"ollama"/"owner"). Anything else - "none", "None",
// "null", "" - renders as nothing (a programmatic null is not a
// thing a human writes).
function metaEngine(engine) {
  if (engine === "groq" || engine === "ollama" || engine === "owner") {
    return " • " + engine;
  }
  return "";
}

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

// v1.3.2 bridge-ready marker: Python's watchdog waits for this, so
// the log proves the main page's bridge came up (or names it missing).
let _readyLogged = false;
function logReady() {
  if (_readyLogged) return;
  const bridge = getApi();
  if (!bridge || !bridge.log_js) return;
  _readyLogged = true;
  try { bridge.log_js("READY main"); } catch (e) {}
}

// v1.3.2 HERO CAT FLIES TO THE HEADER (user request): the big hero cat
// (190px, center) and the small header avatar (44px, top-right) are the
// same cat. When the conversation starts (first message), the hero
// flies to the header with an eased FLIP animation - shrink + travel,
// never a sudden disappearance - then the hero collapses and the chat
// takes the freed space (only ~2 messages were visible before).
// Runs once per launch; a reload brings the hero back.
let heroFlown = false;
function flyHeroToHeader() {
  if (heroFlown) return;
  heroFlown = true;
  const frame = $("avatar-frame");
  const logo = document.querySelector("header .logo");
  const wrap = frame ? frame.closest(".avatar-wrap") : null;
  if (!frame || !logo || !wrap) {
    if (wrap) wrap.classList.add("flown");
    return;
  }
  frame.classList.remove("speaking");   // the breathe/speaking animations own `transform`
  const fr = frame.getBoundingClientRect();
  const lr = logo.getBoundingClientRect();
  const dx = (lr.left + lr.width / 2) - (fr.left + fr.width / 2);
  const dy = (lr.top + lr.height / 2) - (fr.top + fr.height / 2);
  frame.style.setProperty("--fly-dx", dx + "px");
  frame.style.setProperty("--fly-dy", dy + "px");
  frame.classList.add("flying");
  logo.classList.add("fly-target");     // brief landing pulse on the header avatar
  setTimeout(() => logo.classList.remove("fly-target"), 800);
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    frame.removeEventListener("transitionend", finish);
    wrap.classList.add("flown");   // hero collapses -> the chat grows into the freed space
  };
  frame.addEventListener("transitionend", finish);
  setTimeout(finish, 900);   // safety: never leave the hero mid-flight
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
  applyAvatar();
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
  flyHeroToHeader();   // v1.3.2: first message -> the hero cat flies to the header, chat grows
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
    if (res && res.pending) {
      // v1.3.2: tool chat runs in a background thread (the bridge must
      // never block on a permission). Poll for the result; the
      // permission poller shows any Allow/Deny dialog meanwhile.
      startPermPolling();
      const done = await waitForChatResult(res.chat_id);
      stopPermPolling();
      addMsg(safeReply(done.reply), "ai", "Memo AI" + metaEngine(done.engine));
    } else {
      addMsg(safeReply(res.reply), "ai", "Memo AI" + metaEngine(res.engine));
    }
  } catch (e) {
    hideTyping();
    addMsg("حصلت مشكلة، جرّب تاني.", "ai", "Memo AI");
  }
  $("status").textContent = "";
  busy = false;
}

// v1.3.2: poll chat_result() until the background tool chat finishes.
function waitForChatResult(chatId) {
  return new Promise((resolve) => {
    const timer = setInterval(async () => {
      try {
        const res = await getApi().chat_result(chatId);
        if (res && res.done) { clearInterval(timer); resolve(res); }
      } catch (e) { /* keep polling */ }
    }, 1200);
    setTimeout(() => { clearInterval(timer); resolve({ reply: "…", engine: "" }); }, 150000); // 2.5 min cap
  });
}

// v1.3.0: while a tool chat runs, poll for Allow/Deny requests and show
// the permission modal for the oldest unresolved one.
function startPermPolling() {
  stopPermPolling();
  permPollTimer = setInterval(async () => {
    try {
      const p = await getApi().get_pending_permission();
      if (p && p.id && p.id !== currentPermId) {
        currentPermId = p.id;
        $("perm-kind").textContent = p.kind || "";
        $("perm-summary").textContent = p.summary || "";
        $("perm-detail").textContent = p.detail || "";
        $("perm-modal").classList.add("open");
      }
    } catch (e) { /* keep polling */ }
  }, 1000);
}
function stopPermPolling() {
  if (permPollTimer) { clearInterval(permPollTimer); permPollTimer = null; }
  currentPermId = null;
  $("perm-modal").classList.remove("open");
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
    if (res.heard) { addMsg(res.heard, "user", t("heard_prefix").trim()); flyHeroToHeader(); }
    if (res && res.pending) {
      // v1.3.2: tool chat - poll for the reply.
      startPermPolling();
      const done = await waitForChatResult(res.chat_id);
      stopPermPolling();
      addMsg(safeReply(done.reply), "ai", "Memo AI" + metaEngine(done.engine));
    } else {
      addMsg(safeReply(res.reply), "ai", "Memo AI" + metaEngine(res.engine));
    }
  } catch (e) {
    addMsg("مقدرتش أسمعك — جرّب تاني.", "ai", "Memo AI");
  }
  mic.classList.remove("recording");
  $("status").textContent = "";
  $("status").classList.remove("listening");
  busy = false;
}

// v1.3.0 voice-output toggle (OUTPUT only - the mic button is separate).
// Icon + style show the state: 🔊 normal when ON, 🔇 dimmed amber when OFF.
function updateVoiceBtn() {
  const btn = $("voice-btn");
  if (!btn) return;
  btn.textContent = voiceOutput ? "🔊" : "🔇";
  btn.classList.toggle("muted", !voiceOutput);
  btn.title = voiceOutput ? t("voice_output_on") : t("voice_output_off");
}

async function toggleVoiceOutput() {
  const bridge = getApi();
  if (!bridge) return;
  try {
    const res = await bridge.toggle_voice_output();
    voiceOutput = !!res.voice_output;
    updateVoiceBtn();
  } catch (e) { /* keep the old state */ }
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
    if (st.bridge_files_missing) $("status").textContent = t("bridge_files_missing");
    if (st.settings) {
      // v1.4.1: never overwrite a control the user is typing in / has
      // focused - the 15s poll used to revert unsaved picks and wipe a
      // WhatsApp number mid-type (the v1.4.0 owner-number bug).
      const setVal = (id, value) => {
        const el = $(id);
        if (!el || document.activeElement === el) return;
        if (el.type === "checkbox") el.checked = !!value;
        else el.value = value == null ? "" : value;
      };
      setVal("set-ai-name", st.settings.ai_name || "Memo");
      setVal("set-user-name", st.settings.user_name || "Mohamed");
      setVal("set-language", st.settings.language || "ar");
      setVal("set-greeting", !!st.settings.greeting_enabled);
      // v1.4.1: autostart toggle (default ON). No pet/island controls.
      setVal("set-autostart", st.settings.autostart !== false);
      // v1.4.1: WhatsApp fields.
      setVal("set-whatsapp", st.settings.whatsapp_enabled === true);
      setVal("set-wa-owner", st.settings.whatsapp_owner_phone || "");
      selectedVoice = st.settings.voice || "girl_warm";
      // v1.3.0 voice-output toggle (persisted; default ON).
      voiceOutput = st.settings.voice_output !== false;
      updateVoiceBtn();
      const spk = $("set-voice-output");
      if (spk && document.activeElement !== spk) spk.checked = voiceOutput;
      document.querySelectorAll(".voice-card").forEach(c =>
        c.classList.toggle("selected", c.dataset.voice === selectedVoice));
      // v1.4.1: avatar (was NEVER restored before - the load path
      // hardcoded cat). Restore + apply before the UI paints.
      selectedAvatar = st.settings.avatar || "cat";
      document.querySelectorAll(".avatar-opt").forEach(o =>
        o.classList.toggle("selected", o.dataset.avatar === selectedAvatar));
      applyAvatar();
      const vl = $("version-line");
      if (vl && st.version) vl.textContent = t("version_label") + "v" + st.version;
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

// v1.4.0: WhatsApp link status + QR (polled while the Settings modal
// is open). The QR data URL changes on every re-issue, so the img src
// is only replaced when it actually changed (avoids flicker).
let lastWaQr = "";
async function refreshWhatsApp() {
  const bridge = getApi();
  if (!bridge || !bridge.get_whatsapp_status) return;
  try {
    const st = await bridge.get_whatsapp_status();
    const el = $("wa-status"), wrap = $("wa-qr-wrap"), img = $("wa-qr");
    let text = t("wa_status_off");
    // v1.4.1: enabled but the service is off (e.g. after a logout) -
    // say so plainly instead of the bare "off" line.
    if (st.enabled && st.status === "off") text = t("wa_status_off_enabled");
    else if (st.status === "unavailable") text = t("wa_status_unavailable");
    else if (st.status === "connecting") text = t("wa_status_connecting");
    else if (st.status === "waiting_qr") text = t("wa_status_waiting");
    else if (st.status === "linked") text = t("wa_status_linked") + (st.linked_phone || "");
    else if (st.status === "disconnected") text = t("wa_status_disconnected");
    else if (st.status === "error") text = t("wa_status_error") + (st.error ? " (" + st.error + ")" : "");
    if (st.status === "linked" && !st.owner_configured) text += "\n" + t("wa_owner_missing");
    el.textContent = text;
    if (st.qr) {
      if (st.qr !== lastWaQr) { img.src = st.qr; lastWaQr = st.qr; }
      wrap.style.display = "block";
    } else {
      wrap.style.display = "none";
      lastWaQr = "";
    }
  } catch (e) { /* status stays as-is */ }
}

// v1.4.1: Settings menu (DeliveryFlow-style) - one section at a time,
// with a back button to the menu. The menu itself is a .settings-view.
function showSettingsSection(name) {
  $("settings-menu").hidden = true;
  document.querySelectorAll(".settings-view").forEach(v => {
    if (v.id !== "settings-menu") v.hidden = (v.id !== "section-" + name);
  });
  if (name === "logs") refreshLogs();
  if (name === "whatsapp") refreshWhatsApp();
}
function showSettingsMenu() {
  document.querySelectorAll(".settings-view").forEach(v => {
    v.hidden = (v.id !== "settings-menu");
  });
}

function init() {
  logEngine();
  logReady();
  // v1.4.1: no island/pet - the chat is big from the start. The hero
  // starts collapsed (no first message needed); flyHeroToHeader()
  // stays a no-op guard for the old call sites in sendText/voiceChat.
  heroFlown = true;
  const wrap = document.querySelector(".avatar-wrap");
  if (wrap) wrap.classList.add("flown");
  // The bridge may arrive late (MSHTML injects it after DOMContentLoaded):
  // re-probe then, and populate the UI as soon as it is available.
  window.addEventListener("pywebviewready", () => { logEngine(); logReady(); refreshStatus(); });
  // If the bridge still hasn't arrived a few seconds in, say so visibly
  // in the status line instead of leaving dead buttons with no explanation.
  setTimeout(() => { if (!getApi()) $("status").textContent = t("no_bridge"); }, 3000);
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
  $("voice-btn").addEventListener("click", toggleVoiceOutput);
  $("settings-btn").addEventListener("click", () => {
    $("settings-modal").classList.add("open");
    showSettingsMenu();
    // While the WhatsApp section is open, poll its status (QR refreshes).
    if (!window._waTimer) {
      window._waTimer = setInterval(() => {
        if ($("settings-modal").classList.contains("open") &&
            !$("section-whatsapp").hidden) refreshWhatsApp();
      }, 3000);
    }
  });
  $("settings-close").addEventListener("click", () => $("settings-modal").classList.remove("open"));
  $("settings-modal").addEventListener("click", e => {
    if (e.target === $("settings-modal")) $("settings-modal").classList.remove("open");
  });

  // v1.4.1: menu navigation (DeliveryFlow-style).
  document.querySelectorAll(".menu-btn").forEach(b =>
    b.addEventListener("click", () => showSettingsSection(b.dataset.section)));
  document.querySelectorAll(".back-btn").forEach(b =>
    b.addEventListener("click", showSettingsMenu));

  document.querySelectorAll(".voice-card").forEach(card => {
    card.addEventListener("click", async () => {
      selectedVoice = card.dataset.voice;
      document.querySelectorAll(".voice-card").forEach(c =>
        c.classList.toggle("selected", c === card));
      // v1.4.1: save AT THE MOMENT of picking. The old code only kept
      // it in memory until Settings Save - and the 15s poll reverted
      // it to the saved value. The voice applies on the next reply.
      await saveNow({ voice: selectedVoice }, "save-status-voice");
    });
  });
  document.querySelectorAll(".avatar-opt").forEach(opt => {
    opt.addEventListener("click", async () => {
      // v1.4.1: all 4 avatars are live now (real dog/boy/girl images)
      // and the pick saves immediately, like the voice. (The old code
      // early-returned for anything but cat - the pick did nothing.)
      selectedAvatar = opt.dataset.avatar;
      document.querySelectorAll(".avatar-opt").forEach(o =>
        o.classList.toggle("selected", o === opt));
      applyAvatar();
      await saveNow({ avatar: selectedAvatar }, "save-status-avatar");
    });
  });

  // v1.4.1: General section saves ONLY its own fields (voice + avatar
  // are already saved on pick; they are included here as a safety net).
  $("save-general").addEventListener("click", async () => {
    const b = getApi();
    if (!b) { $("status").textContent = t("bridge_not_ready"); return; }
    const payload = {
      ai_name: $("set-ai-name").value.trim() || "Memo",
      user_name: $("set-user-name").value.trim() || "Mohamed",
      language: $("set-language").value,
      voice: selectedVoice,
      avatar: selectedAvatar,
      voice_output: $("set-voice-output").checked,
      greeting_enabled: $("set-greeting").checked,
      autostart: $("set-autostart").checked,
    };
    await b.save_settings(JSON.stringify(payload));
    LANG = payload.language;
    applyLang();
    refreshStatus();
    const el = $("save-status-general");
    if (el) {
      el.textContent = t("saved");
      setTimeout(() => { el.textContent = ""; }, 2500);
    }
  });

  // v1.4.1: WhatsApp section saves ONLY via whatsapp_save - and its
  // result is CHECKED, not swallowed. The fields are SNAPSHOT before
  // any bridge call: the old handler saved general settings first,
  // whose re-render wiped these fields back to their old values, and
  // it then read the wiped values and saved THOSE (empty number,
  // disabled) while saying "saved ✅" - the v1.4.0 owner-number /
  // missing-QR bug.
  $("save-whatsapp").addEventListener("click", async () => {
    const b = getApi();
    const el = $("save-status-whatsapp");
    if (!b) { $("status").textContent = t("bridge_not_ready"); return; }
    const snap = {
      enabled: $("set-whatsapp").checked,
      owner_phone: $("set-wa-owner").value.trim(),
    };
    let res = null;
    try {
      res = await b.whatsapp_save(JSON.stringify(snap));
    } catch (e) { res = { ok: false, error: String(e) }; }
    if (res && res.ok) {
      if (el) {
        el.textContent = t("saved");
        setTimeout(() => { el.textContent = ""; }, 2500);
      }
    } else {
      const err = (res && res.error) ? res.error : "";
      if (el) el.textContent = t("wa_save_error") + err;
      $("wa-status").textContent = t("wa_status_error") + (err ? " (" + err + ")" : "");
    }
    refreshWhatsApp();
    refreshStatus();
  });

  // v1.4.1: owner-code unlock from the General section (same hash
  // compare as chat() - the typed code is never logged or echoed).
  $("owner-unlock-btn").addEventListener("click", async () => {
    const b = getApi();
    const input = $("set-owner-code");
    const el = $("owner-status");
    if (!b || !b.unlock_owner) return;
    const code = input.value;
    input.value = "";
    try {
      const res = await b.unlock_owner(code);
      if (el) {
        el.textContent = res.ok ? t("owner_unlocked") : t("owner_wrong");
        setTimeout(() => { el.textContent = ""; }, 3000);
      }
    } catch (e) {
      if (el) el.textContent = t("owner_wrong");
    }
  });

  // v1.4.1: speaker toggle in the General section saves immediately.
  $("set-voice-output").addEventListener("change", async () => {
    const b = getApi();
    if (!b) return;
    const checked = $("set-voice-output").checked;
    try {
      await b.save_settings(JSON.stringify({ voice_output: checked }));
      voiceOutput = checked;
      updateVoiceBtn();
    } catch (e) { /* keep the old state */ }
  });

  $("github-btn").addEventListener("click", () => { const b = getApi(); if (b) b.open_github(); });

  $("wa-disconnect-btn").addEventListener("click", async () => {
    const b = getApi();
    if (!b || !b.whatsapp_disconnect) return;
    await b.whatsapp_disconnect();
    refreshWhatsApp();
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
    // v1.3.1: the download used to run ~90s with zero feedback, so it
    // looked dead. Poll the download progress and render the bar +
    // "x MB / y MB (z%)" until the app restarts (or fails).
    const bar = $("update-progress");
    const fill = $("update-progress-fill");
    const label = $("update-progress-label");
    bar.style.display = "";
    const fmtMB = (n) => (n / 1048576).toFixed(1);
    const timer = setInterval(async () => {
      try {
        const p = await getApi().get_update_progress();
        if (!p) return;
        if (p.state === "downloading") {
          const total = p.total ? " / " + fmtMB(p.total) + " MB" : "";
          const pct = p.percent != null ? " (" + p.percent + "%)" : "";
          label.textContent = t("update_downloading") + " " + fmtMB(p.downloaded) +
            " MB" + total + pct;
          fill.style.width = (p.percent != null ? p.percent : 0) + "%";
        } else if (p.state === "verifying") {
          label.textContent = t("update_verifying");
          fill.style.width = "100%";
        } else if (p.state === "done") {
          label.textContent = t("update_done");
          fill.style.width = "100%";
          clearInterval(timer);
        } else if (p.state === "error") {
          label.textContent = t("update_failed");
          clearInterval(timer);
        }
      } catch (e) { /* the app may restart mid-poll - that means success */ }
    }, 500);
  });
  $("quit-btn").addEventListener("click", async () => {
    const b = getApi();
    if (!b) { $("status").textContent = t("bridge_not_ready"); return; }
    if (confirm(t("confirm_quit"))) await b.quit_app();
  });

  // v1.3.0 permission dialog: Allow/Deny resolve the pending tool request.
  const answerPerm = async (allowed) => {
    const b = getApi();
    if (b && currentPermId) {
      try { await b.resolve_permission(currentPermId, allowed); } catch (e) { /* timeout = deny */ }
    }
    currentPermId = null;
    $("perm-modal").classList.remove("open");
  };
  $("perm-allow").addEventListener("click", () => answerPerm(true));
  $("perm-deny").addEventListener("click", () => answerPerm(false));
}

document.addEventListener("DOMContentLoaded", init);
