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
    // v1.4.3: linked but incoming messages do NOT decrypt (Bad MAC) —
    // never shown as green "linked"; the re-link button appears below.
    wa_status_decrypt: "مربوط ⚠️ بس رسايل الواتساب مش بتتفك (مشكلة تشفير) — الرسايل اللي بتوصل مش بتتقرأ ومش بتوصل للمخ. دوس إعادة الربط تحت وهيطلع QR جديد.",
    wa_relink: "إعادة الربط (QR جديد)",
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
    perm_allowed: "تم السماح ✅",
    perm_denied: "تم الرفض ❌",
    perm_timeout: "انتهت المهلة — تم الرفض ⏱️",
    perm_more: "عرض المزيد ▾",
    perm_less: "عرض أقل ▴",
    menu_memory: "الذاكرة",
    sec_memory: "الذاكرة الطويلة",
    mem_hint: "دي الحاجات اللي Memo AI فاكرها عنك من المحادثات — محفوظة على جهازك بس، ومبتترفعش لأي مكان. تقدر تمسح أي حاجة منها، أو تمسح الكل.",
    mem_facts: "حقائق محفوظة",
    mem_rules: "قواعد اتعلمتها من تصحيحاتك",
    mem_routines: "روتينك وعاداتك",
    mem_style: "أسلوبك اللي اتعلمته",
    mem_summary: "ملخص محادثات سابقة",
    mem_empty: "لسه مفيش حاجة محفوظة — قولي «افتكر إن...» في الشات.",
    mem_clear: "مسح كل الذاكرة",
    mem_clear_confirm: "متأكد إنك عايز تمسح كل ذاكرة Memo AI عنك؟",
    mem_deleted: "اتمسحت ✅",
    menu_system: "صحة الجهاز",
    menu_notifications: "الإشعارات",
    sec_system: "صحة الجهاز",
    sec_notifications: "الإشعارات المهمة",
    mon_enable: "مراقبة صحة الجهاز والتنبيه عند الخطر",
    mon_ram_free: "تنبيه لما الرام الفاضية تقل عن (MB)",
    mon_ram_pct: "أو لما استهلاك الرام يزيد عن (%)",
    mon_disk_free: "تنبيه لما القرص الفاضي يقل عن (GB)",
    mon_disk_pct: "أو لما القرص الفاضي يقل عن (%)",
    mon_temp_warn: "تنبيه حرارة المعالج (°م)",
    mon_cpu: "تنبيه حمل معالج مستمر فوق (%) لمدة (دقائق)",
    mon_battery: "تنبيه بطارية منخفضة تحت (%)",
    mon_cooldown: "عدم تكرار نفس التنبيه قبل (دقيقة)",
    mon_in_app: "التنبيه داخل التطبيق",
    mon_toast: "التنبيه كـ toast من ويندوز",
    sys_temp_unavailable: "⚠️ حرارة المعالج مش متاحة على الجهاز ده (مفيش حساس مقروء) — المراقبة شغالة من غيرها، ومفيش رقم حرارة متخمن.",
    notif_enable: "التنبيه عند إشعار مهم من ويندوز",
    notif_access: "طلب إذن الوصول للإشعارات",
    notif_apps: "تطبيقات مهمة (سطر لكل تطبيق)",
    notif_keywords: "كلمات مفتاحية مهمة (سطر لكل كلمة)",
    notif_unavailable: "الاستماع لإشعارات ويندوز مش متاح على الجهاز ده.",
    notif_denied: "إذن الوصول للإشعارات مرفوض — فعّله من إعدادات ويندوز (الإشعارات) أو دوس «طلب إذن الوصول» تاني.",
    notif_granted: "الاستماع للإشعارات شغال ✅",
    notif_not_requested: "لسه مطلبتش إذن الوصول للإشعارات — دوس «طلب إذن الوصول للإشعارات».",
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
    update_whats_new: "إيه الجديد في",
    update_download: "⬇️ تنزيل التحديث",
    update_install_now: "تثبيت الآن",
    update_install_later: "تثبيت لاحقاً",
    update_ready: "التحديث نزل وجاهز ✅",
    update_ready_hint: "اختار وقت ما تحب — التثبيت هيعيد تشغيل البرنامج.",
    update_saved_later: "تمام — التحديث محفوظ، تقدر تثبّته من هنا وقت ما تحب.",
    update_install_downloaded: "تثبيت التحديث المنزّل",
    update_redownloading: "الملف المنزّل مش سليم — بينزّل من جديد...",
    update_no_download: "مفيش تحديث منزّل لسه.",
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
    // v1.4.3: linked but incoming messages do NOT decrypt (Bad MAC) —
    // never shown as green "linked"; the re-link button appears below.
    wa_status_decrypt: "Linked ⚠️ but WhatsApp messages won't decrypt (encryption problem) — incoming messages can't be read and never reach the brain. Tap Re-link below for a fresh QR.",
    wa_relink: "Re-link (new QR)",
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
    perm_allowed: "Allowed ✅",
    perm_denied: "Denied ❌",
    perm_timeout: "Timed out — denied ⏱️",
    perm_more: "Show more ▾",
    perm_less: "Show less ▴",
    menu_memory: "Memory",
    sec_memory: "Long-term memory",
    mem_hint: "Things Memo AI remembers about you from conversations — stored on your laptop only, never uploaded. You can delete any of them, or wipe everything.",
    mem_facts: "Saved facts",
    mem_rules: "Rules learned from your corrections",
    mem_routines: "Your routines and habits",
    mem_style: "Your style, as learned",
    mem_summary: "Summary of earlier conversations",
    mem_empty: "Nothing saved yet — tell me “remember that...” in the chat.",
    mem_clear: "Wipe all memory",
    mem_clear_confirm: "Are you sure you want to wipe all of Memo AI's memories about you?",
    mem_deleted: "Deleted ✅",
    menu_system: "System health",
    menu_notifications: "Notifications",
    sec_system: "System health",
    sec_notifications: "Important notifications",
    mon_enable: "Monitor system health and alert on danger",
    mon_ram_free: "Alert when free RAM drops below (MB)",
    mon_ram_pct: "Or when RAM usage rises above (%)",
    mon_disk_free: "Alert when free disk drops below (GB)",
    mon_disk_pct: "Or when free disk drops below (%)",
    mon_temp_warn: "CPU temperature alert (°C)",
    mon_cpu: "Sustained CPU load above (%) for (minutes)",
    mon_battery: "Low battery alert below (%)",
    mon_cooldown: "Don't repeat the same alert within (minutes)",
    mon_in_app: "Alert inside the app",
    mon_toast: "Alert as a Windows toast",
    sys_temp_unavailable: "⚠️ CPU temperature is not available on this machine (no readable sensor) — monitoring runs without it, and no temperature is guessed.",
    notif_enable: "Alert on important Windows notifications",
    notif_access: "Request notification access",
    notif_apps: "Important apps (one per line)",
    notif_keywords: "Important keywords (one per line)",
    notif_unavailable: "Listening to Windows notifications is not available on this machine.",
    notif_denied: "Notification access is denied — enable it in Windows Settings (Notifications) or tap “Request notification access” again.",
    notif_granted: "Notification listening is on ✅",
    notif_not_requested: "Notification access hasn't been requested yet — tap “Request notification access”.",
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
    update_whats_new: "What's new in",
    update_download: "⬇️ Download update",
    update_install_now: "Install now",
    update_install_later: "Install later",
    update_ready: "Update downloaded and ready ✅",
    update_ready_hint: "Install whenever you like — installing restarts the app.",
    update_saved_later: "Saved — you can install it from here whenever you like.",
    update_install_downloaded: "Install the downloaded update",
    update_redownloading: "The saved file is unusable — re-downloading...",
    update_no_download: "No downloaded update yet.",
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
// v1.4.2: in-chat permission cards (req id -> card element). Cards stay
// in the chat history after they resolve (final state, buttons dead).
let permCards = {};
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

// v1.4.3: render generated images as real pictures in the chat. The
// filenames come from chat_result().images; the bytes come from the
// bridge (get_generated_image) as a data URL, because a local file
// page cannot load file:// images from app/data/images/ in WebView2.
// A failed fetch leaves the text reply standing on its own - the text
// already says the image was generated and where it is saved.
async function addGeneratedImages(filenames) {
  const chat = $("chat");
  const bridge = getApi();
  if (!bridge || !bridge.get_generated_image) return;
  for (const name of filenames || []) {
    try {
      const res = await bridge.get_generated_image(name);
      if (!res || !res.ok || !res.data_url) continue;
      const div = document.createElement("div");
      div.className = "msg ai gen-image";
      const img = document.createElement("img");
      img.src = res.data_url;
      img.alt = "صورة مولّدة";
      div.appendChild(img);
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
    } catch (e) { /* text reply already covers it */ }
  }
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
      // never block on a permission). Poll for the result; any
      // Allow/Deny request appears as a card in the chat meanwhile.
      startPermPolling();
      const done = await waitForChatResult(res.chat_id);
      stopPermPolling();
      addMsg(safeReply(done.reply), "ai", "Memo AI" + metaEngine(done.engine));
      await addGeneratedImages(done.images);
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

// v1.4.2: while a tool chat runs, poll for Allow/Deny requests and
// show each one as a MESSAGE IN THE CHAT with Allow/Deny buttons
// (not a popup/modal anymore). A card stays in the chat until it is
// answered - or until the 120s timeout denies it - and then flips to
// its final state ("تم السماح" / "تم الرفض" / "انتهت المهلة") with
// dead buttons. If the user sends another message while one is
// pending, the card simply stays visible in the history.
function addPermCard(p) {
  const chat = $("chat");
  const div = document.createElement("div");
  div.className = "msg perm";
  div.dataset.permId = p.id;
  const title = document.createElement("div");
  title.className = "perm-kind";
  title.textContent = t("perm_title") + (p.kind ? " • " + p.kind : "");
  const summary = document.createElement("div");
  summary.className = "perm-summary";
  summary.textContent = p.summary || "";
  div.appendChild(title);
  div.appendChild(summary);
  if (p.detail) {
    const detail = document.createElement("pre");
    detail.className = "perm-detail";
    detail.textContent = p.detail;
    // v1.4.3: detail is clamped to 2 lines by CSS; tap it (or the
    // toggle below) to expand to the full scrollable block.
    const more = document.createElement("button");
    more.className = "perm-more";
    more.type = "button";
    more.textContent = t("perm_more");
    const toggleDetail = () => {
      const open = detail.classList.toggle("expanded");
      more.textContent = open ? t("perm_less") : t("perm_more");
    };
    more.addEventListener("click", toggleDetail);
    detail.addEventListener("click", toggleDetail);
    div.appendChild(detail);
    div.appendChild(more);
  }
  const btns = document.createElement("div");
  btns.className = "perm-btns";
  const allowBtn = document.createElement("button");
  allowBtn.className = "gold-btn btn-3d";
  allowBtn.textContent = "✓ " + t("perm_allow");
  const denyBtn = document.createElement("button");
  denyBtn.className = "danger-btn btn-3d";
  denyBtn.textContent = "✕ " + t("perm_deny");
  const answer = async (allowed) => {
    allowBtn.disabled = true;
    denyBtn.disabled = true;
    const b = getApi();
    if (b) {
      try { await b.resolve_permission(p.id, allowed); } catch (e) { /* timeout = deny */ }
    }
    finalizePermCard(p.id, allowed ? "allowed" : "denied");
  };
  allowBtn.addEventListener("click", () => answer(true));
  denyBtn.addEventListener("click", () => answer(false));
  btns.appendChild(denyBtn);
  btns.appendChild(allowBtn);
  div.appendChild(btns);
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  div.appendChild(meta);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  permCards[p.id] = { el: div, allowBtn, denyBtn, done: false };
  return div;
}

function finalizePermCard(id, status) {
  const card = permCards[id];
  if (!card || card.done) return;
  card.done = true;
  card.allowBtn.disabled = true;
  card.denyBtn.disabled = true;
  card.el.classList.add("resolved-" + status);
  const state = document.createElement("div");
  state.className = "perm-state";
  state.textContent = status === "allowed" ? t("perm_allowed")
    : status === "timeout" ? t("perm_timeout") : t("perm_denied");
  card.el.appendChild(state);
}

function startPermPolling() {
  stopPermPolling();
  permPollTimer = setInterval(async () => {
    try {
      const st = await getApi().get_pending_permission();
      if (!st) return;
      const pending = st.pending;
      if (pending && pending.id && !permCards[pending.id]) {
        addPermCard(pending);
      }
      (st.resolved || []).forEach(r => {
        if (r && r.id && permCards[r.id]) finalizePermCard(r.id, r.status);
      });
    } catch (e) { /* keep polling */ }
  }, 1000);
}
function stopPermPolling() {
  if (permPollTimer) { clearInterval(permPollTimer); permPollTimer = null; }
  // Cards intentionally stay: they are chat history now.
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
      await addGeneratedImages(done.images);
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
  pollAlerts();
}

// v1.4.2: Memory section - what the brain remembers, with a delete
// button per item (fact / learned rule / routine) + wipe-all.
async function refreshMemory() {
  const box = $("memory-list");
  if (!box) return;
  const bridge = getApi();
  if (!bridge || !bridge.get_memory) { box.textContent = t("no_bridge"); return; }
  try {
    const mem = await bridge.get_memory();
    box.innerHTML = "";
    const addGroup = (title, items, kind) => {
      if (!items || !items.length) return;
      const h = document.createElement("div");
      h.className = "mem-group-title";
      h.textContent = title;
      box.appendChild(h);
      items.forEach(item => {
        const row = document.createElement("div");
        row.className = "mem-row";
        const span = document.createElement("span");
        span.className = "mem-text";
        span.textContent = item.text || "";
        const del = document.createElement("button");
        del.className = "mem-del btn-3d";
        del.textContent = "✕";
        del.title = t("mem_deleted");
        del.addEventListener("click", async () => {
          const b = getApi();
          if (b && b.delete_memory_item) {
            await b.delete_memory_item(kind, item.id);
            refreshMemory();
          }
        });
        row.appendChild(span);
        row.appendChild(del);
        box.appendChild(row);
      });
    };
    addGroup(t("mem_facts"), mem.facts, "fact");
    addGroup(t("mem_rules"), mem.rules, "rule");
    addGroup(t("mem_routines"), mem.routines, "routine");
    const st = mem.style || {};
    const styleBox = $("memory-style");
    if (styleBox) {
      const tone = st.tone === "short" ? "قصيرة ومباشرة"
        : st.tone === "detailed" ? "مفصّلة" : "مختصرة (تلقائي)";
      let txt = "عربي مصري • صيغ مذكر بس • نبرة: " + tone;
      if (st.phrases && st.phrases.length) txt += "\nعبارات مفضلة: " + st.phrases.join("، ");
      if (st.notes && st.notes.length) txt += "\n" + st.notes.join("\n");
      styleBox.textContent = txt;
    }
    const sumBox = $("memory-summary");
    if (sumBox) sumBox.textContent = mem.summary || "—";
    if (!mem.facts.length && !mem.rules.length && !mem.routines.length) {
      const empty = document.createElement("div");
      empty.className = "hint";
      empty.textContent = t("mem_empty");
      box.appendChild(empty);
    }
  } catch (e) {
    box.textContent = "…";
  }
}

// v1.4.2: System section - live numbers + thresholds. Filled on
// open/save only (never mid-type - refreshStatus does not touch it).
async function refreshMonitor() {
  const bridge = getApi();
  if (!bridge || !bridge.get_monitor_status) return;
  try {
    const res = await bridge.get_monitor_status();
    const st = res.status || {};
    const ram = st.ram || {}, disk = st.disk || {}, cpu = st.cpu || {};
    const lines = [];
    if (ram.total_mb) lines.push(`الرام: ${ram.used_mb}MB / ${ram.total_mb}MB (${ram.percent}% — فاضي ${ram.free_mb}MB)`);
    if (disk.total_gb) lines.push(`القرص: فاضي ${disk.free_gb}GB من ${disk.total_gb}GB (${disk.percent_free}% فاضي)`);
    if (cpu.percent != null) lines.push(`المعالج: ${cpu.percent}%` + (cpu.sustained_high ? " (حمل مرتفع مستمر ⚠️)" : ""));
    if (st.temp_c != null) lines.push(`حرارة المعالج: ${st.temp_c}°م`);
    if (st.battery) lines.push(`البطارية: ${st.battery.percent}%` + (st.battery.charging ? " (بتشحن 🔌)" : ""));
    const el = $("sys-status");
    if (el) el.textContent = lines.join("\n") || "…";
    const note = $("sys-temp-note");
    if (note) note.textContent = res.temp_available ? "" : t("sys_temp_unavailable");
    const s = res.settings || {};
    const setNum = (id, v) => { const e = $(id); if (e && v != null) e.value = v; };
    const setChk = (id, v) => { const e = $(id); if (e) e.checked = !!v; };
    setChk("set-monitor-enabled", s.monitor_enabled !== false);
    setNum("set-mon-ram-free", s.monitor_ram_free_mb);
    setNum("set-mon-ram-pct", s.monitor_ram_percent);
    setNum("set-mon-disk-free", s.monitor_disk_free_gb);
    setNum("set-mon-disk-pct", s.monitor_disk_percent_free);
    setNum("set-mon-temp-warn", s.monitor_temp_warn_c);
    setNum("set-mon-cpu-pct", s.monitor_cpu_sustained_percent);
    setNum("set-mon-cpu-min", s.monitor_cpu_sustained_minutes);
    setNum("set-mon-batt", s.monitor_battery_warn_percent);
    setNum("set-mon-cooldown", s.monitor_cooldown_min);
    setChk("set-mon-in-app", s.monitor_alert_in_app !== false);
    setChk("set-mon-toast", s.monitor_alert_toast !== false);
  } catch (e) { /* status stays as-is */ }
}

// v1.4.2: Notifications section - listener state (honest when
// unavailable/denied) + filter lists.
async function refreshNotifications() {
  const bridge = getApi();
  if (!bridge || !bridge.get_notification_status) return;
  try {
    const res = await bridge.get_notification_status();
    const l = res.listener || {};
    const el = $("notif-status");
    if (el) {
      if (!l.available) el.textContent = t("notif_unavailable") + (l.detail ? " (" + l.detail + ")" : "");
      else if (l.access === "granted") el.textContent = t("notif_granted");
      else if (l.access === "denied") el.textContent = t("notif_denied");
      else el.textContent = t("notif_not_requested");
    }
    const chk = $("set-notif-enabled");
    if (chk) chk.checked = !!res.enabled;
    const apps = $("set-notif-apps");
    if (apps && document.activeElement !== apps) apps.value = (res.apps || []).join("\n");
    const kws = $("set-notif-keywords");
    if (kws && document.activeElement !== kws) kws.value = (res.keywords || []).join("\n");
  } catch (e) { /* status stays as-is */ }
}

// v1.4.2: alert banners - system/notification alerts polled with the
// status refresh (15s) and rendered once each above the chat.
let seenAlerts = {};
function pollAlerts() {
  const bridge = getApi();
  if (!bridge || !bridge.get_alerts) return;
  bridge.get_alerts().then(res => {
    const box = $("alerts");
    if (!box || !res || !res.alerts) return;
    res.alerts.forEach(a => {
      if (!a || !a.id || seenAlerts[a.id]) return;
      seenAlerts[a.id] = true;
      const div = document.createElement("div");
      div.className = "alert-banner" + (a.level === "critical" ? " critical" : "");
      const title = document.createElement("b");
      title.textContent = a.title || "تنبيه";
      const msg = document.createElement("span");
      msg.textContent = (a.notification_title ? a.notification_title + " — " : "") + (a.message || "");
      const x = document.createElement("button");
      x.className = "alert-x btn-3d";
      x.textContent = "✕";
      x.addEventListener("click", () => div.remove());
      div.appendChild(title);
      div.appendChild(msg);
      div.appendChild(x);
      box.appendChild(div);
    });
  }).catch(() => { /* alerts stay hidden until the next poll */ });
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
    else if (st.status === "decrypt_failed") {
      // v1.4.3: DEGRADED — linked, but messages are not decrypting.
      // Show the failure count (when known) and the re-link button;
      // never the green "linked" line while mail is being lost.
      text = t("wa_status_decrypt");
      if (st.decrypt_failures) text += " (" + st.decrypt_failures + ")";
    }
    else if (st.status === "disconnected") text = t("wa_status_disconnected");
    else if (st.status === "error") text = t("wa_status_error") + (st.error ? " (" + st.error + ")" : "");
    if (st.status === "linked" && !st.owner_configured) text += "\n" + t("wa_owner_missing");
    el.textContent = text;
    // v1.4.3: the re-link button (logout + fresh QR) is only visible
    // while the link is degraded — that is when it is needed.
    const relinkBtn = $("wa-relink-btn");
    if (relinkBtn) relinkBtn.style.display = st.status === "decrypt_failed" ? "" : "none";
    if (st.qr) {
      if (st.qr !== lastWaQr) { img.src = st.qr; lastWaQr = st.qr; }
      wrap.style.display = "block";
    } else {
      wrap.style.display = "none";
      lastWaQr = "";
    }
  } catch (e) { /* status stays as-is */ }
}

// v1.4.3: Apple-style "What's new" slide + Install now / Install later.
//
// Download and install are SEPARATE steps now: check -> download
// (progress bar) -> state "downloaded" -> this slide offers the
// choice. Nothing installs automatically, and there is no confirm()
// dialog anywhere in the update flow.
//
// Feature cards come from the manifest's structured "features" list
// when the release carries one; otherwise the plain "notes" text is
// split the same way the backend does (see updater.parse_features).
let updateReminderShownThisSession = false; // reminder: at most once a session
let updatePollTimer = null;
let updateSlideData = null; // {version, features} of the open slide

function parseFeaturesFromNotes(notes) {
  const chunks = String(notes || "").split(/[\n•—]+/);
  const icons = ["✨", "🛠️", "🧠", "💬", "🔍", "⚡", "🎨", "🔒"];
  const out = [];
  chunks.forEach((raw, i) => {
    let text = raw.trim().replace(/^-+\s*/, "").trim();
    if (i === 0) text = text.replace(/^v?\d+(?:\.\d+){1,2}\s*:\s*/, "");
    if (!text) return;
    let title = text, desc = "";
    const colon = text.indexOf(":");
    if (colon > 0 && colon <= 40 && text.slice(colon + 1).trim()) {
      title = text.slice(0, colon).trim();
      desc = text.slice(colon + 1).trim();
    }
    if (title.length > 60 && !desc) {
      const cut = title.lastIndexOf(" ", 60);
      if (cut > 20) { desc = title.slice(cut).trim(); title = title.slice(0, cut).trim(); }
    }
    out.push({ icon: icons[out.length % icons.length], title, desc });
  });
  return out;
}

function normalizeFeatures(res) {
  if (res && Array.isArray(res.features) && res.features.length) return res.features;
  return parseFeaturesFromNotes(res ? res.notes : "");
}

function hideUpdateSlide() {
  const slide = $("update-slide");
  if (slide) { slide.hidden = true; slide.innerHTML = ""; }
  updateSlideData = null;
}

function renderUpdateSlide(data, phase) {
  // phase: "available" (Download button) | "downloading" (progress
  // inside the slide) | "ready" (Install now / Install later).
  const slide = $("update-slide");
  if (!slide) return;
  updateSlideData = data;
  slide.hidden = false;
  slide.innerHTML = "";
  // Re-trigger the entrance animation on every render.
  slide.classList.remove("update-slide-in");
  void slide.offsetWidth;
  slide.classList.add("update-slide-in");

  const hero = document.createElement("div");
  hero.className = "update-slide-hero";
  const icon = document.createElement("div");
  icon.className = "update-slide-icon";
  icon.textContent = "✨";
  const title = document.createElement("div");
  title.className = "update-slide-title";
  title.textContent = t("update_whats_new") + " v" + data.version;
  const ver = document.createElement("div");
  ver.className = "update-slide-version";
  ver.textContent = "v" + data.version;
  hero.appendChild(icon); hero.appendChild(title); hero.appendChild(ver);
  slide.appendChild(hero);

  const list = document.createElement("div");
  list.className = "update-feature-list";
  (data.features || []).forEach((f, i) => {
    const card = document.createElement("div");
    card.className = "update-feature-card";
    card.style.animationDelay = (i * 60) + "ms";
    const ic = document.createElement("div");
    ic.className = "update-feature-icon";
    ic.textContent = f.icon || "✨";
    const body = document.createElement("div");
    body.className = "update-feature-body";
    const tt = document.createElement("div");
    tt.className = "update-feature-title";
    tt.textContent = f.title || "";
    body.appendChild(tt);
    if (f.desc) {
      const dd = document.createElement("div");
      dd.className = "update-feature-desc";
      dd.textContent = f.desc;
      body.appendChild(dd);
    }
    card.appendChild(ic); card.appendChild(body);
    list.appendChild(card);
  });
  if (!list.children.length) {
    const empty = document.createElement("div");
    empty.className = "hint";
    empty.textContent = t("update_available");
    list.appendChild(empty);
  }
  slide.appendChild(list);

  const footer = document.createElement("div");
  footer.className = "update-slide-actions";
  if (phase === "available") {
    const dl = document.createElement("button");
    dl.className = "gold-btn btn-3d";
    dl.textContent = t("update_download");
    dl.addEventListener("click", startUpdateDownload);
    footer.appendChild(dl);
  } else if (phase === "downloading") {
    const track = document.createElement("div");
    track.className = "update-progress-track";
    const fill = document.createElement("div");
    fill.className = "update-progress-fill";
    fill.id = "update-slide-progress-fill";
    track.appendChild(fill);
    const label = document.createElement("div");
    label.className = "update-progress-label";
    label.id = "update-slide-progress-label";
    label.textContent = t("update_downloading");
    footer.appendChild(track); footer.appendChild(label);
  } else if (phase === "ready") {
    const hint = document.createElement("div");
    hint.className = "update-slide-hint";
    hint.textContent = t("update_ready_hint");
    slide.appendChild(hint);
    const now = document.createElement("button");
    now.className = "gold-btn btn-3d";
    now.textContent = t("update_install_now");
    now.addEventListener("click", installDownloadedNow);
    const later = document.createElement("button");
    later.className = "ghost-btn btn-3d";
    later.textContent = t("update_install_later");
    later.addEventListener("click", installDownloadedLater);
    footer.appendChild(now); footer.appendChild(later);
  }
  slide.appendChild(footer);
}

function pollUpdateProgress() {
  // v1.3.1: the download used to run ~90s with zero feedback, so it
  // looked dead. Poll the download progress and render the bar +
  // "x MB / y MB (z%)" until the app restarts (or fails).
  // v1.4.3: a "downloaded" state now ends the poll by showing the
  // Install-now / Install-later slide instead of installing.
  if (updatePollTimer) clearInterval(updatePollTimer);
  const fmtMB = (n) => (n / 1048576).toFixed(1);
  const setBar = (pct, label) => {
    const fill = $("update-slide-progress-fill") || $("update-progress-fill");
    const lab = $("update-slide-progress-label") || $("update-progress-label");
    if (fill) fill.style.width = (pct != null ? pct : 0) + "%";
    if (lab) lab.textContent = label;
  };
  updatePollTimer = setInterval(async () => {
    try {
      const b = getApi();
      if (!b) return;
      const p = await b.get_update_progress();
      if (!p) return;
      if (p.state === "downloading") {
        if (updateSlideData && !$("update-slide-progress-fill")) {
          renderUpdateSlide(updateSlideData, "downloading");
        }
        const total = p.total ? " / " + fmtMB(p.total) + " MB" : "";
        const pct = p.percent != null ? " (" + p.percent + "%)" : "";
        setBar(p.percent, t("update_downloading") + " " + fmtMB(p.downloaded) + " MB" + total + pct);
      } else if (p.state === "verifying") {
        setBar(100, t("update_verifying"));
      } else if (p.state === "downloaded") {
        clearInterval(updatePollTimer); updatePollTimer = null;
        setBar(100, t("update_ready"));
        try {
          const d = await b.get_downloaded_update();
          if (d && d.downloaded && d.valid) {
            renderUpdateSlide({ version: d.version, features: d.features || [] }, "ready");
          }
        } catch (e) { /* the slide stays on the ready text */ }
      } else if (p.state === "done") {
        clearInterval(updatePollTimer); updatePollTimer = null;
        setBar(100, t("update_done"));
      } else if (p.state === "error") {
        clearInterval(updatePollTimer); updatePollTimer = null;
        setBar(null, t("update_failed"));
      }
    } catch (e) { /* the app may restart mid-poll - that means success */ }
  }, 500);
}

async function startUpdateDownload() {
  const b = getApi();
  if (!b) { $("update-status").textContent = t("bridge_not_ready"); return; }
  if (!updateSlideData) return;
  renderUpdateSlide(updateSlideData, "downloading");
  try { await b.download_update(); } catch (e) { /* see the log */ }
  pollUpdateProgress();
}

async function installDownloadedNow() {
  // "Install now": installs ONLY the already-downloaded file, then
  // the app restarts. If the saved file turned out unusable, the
  // backend re-downloads it and the poll shows the choice slide again.
  const b = getApi();
  if (!b) { $("update-status").textContent = t("bridge_not_ready"); return; }
  $("update-status").textContent = t("update_installing");
  try { await b.install_update(); } catch (e) { /* see the log */ }
  pollUpdateProgress();
}

async function installDownloadedLater() {
  // "Install later": close the slide, KEEP the downloaded file, and
  // mark the reminder shown so it pops at most once today / once
  // this session. The update stays installable from Settings.
  const b = getApi();
  if (b && b.dismiss_update_reminder) {
    try { await b.dismiss_update_reminder(); } catch (e) { /* non-fatal */ }
  }
  updateReminderShownThisSession = true;
  hideUpdateSlide();
  $("update-status").textContent = t("update_saved_later");
  const btn = $("install-downloaded-btn");
  if (btn) btn.style.display = "";
}

async function refreshDownloadedState() {
  // Called when the Updates section opens: a waiting download gets
  // its "Install the downloaded update" button, and the reminder
  // slide pops at most once a day (backend date flag) and once per
  // session (this flag) - never twice in a row.
  const b = getApi();
  const btn = $("install-downloaded-btn");
  if (!b || !b.get_downloaded_update) { if (btn) btn.style.display = "none"; return; }
  try {
    const d = await b.get_downloaded_update();
    if (!d || !d.downloaded) { if (btn) btn.style.display = "none"; return; }
    if (btn) btn.style.display = "";
    if (d.valid && d.remind && !updateReminderShownThisSession) {
      updateReminderShownThisSession = true;
      if (b.dismiss_update_reminder) await b.dismiss_update_reminder();
      renderUpdateSlide({ version: d.version, features: d.features || [] }, "ready");
    }
  } catch (e) { /* leave the button hidden */ }
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
  if (name === "memory") refreshMemory();
  if (name === "system") refreshMonitor();
  if (name === "notifications") refreshNotifications();
  if (name === "updates") refreshDownloadedState();
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

  // v1.4.3: clean re-link (logout + wipe broken keys + fresh QR) for
  // the degraded decrypt state. Falls back to plain disconnect on
  // builds whose bridge has no whatsapp_relink yet.
  $("wa-relink-btn").addEventListener("click", async () => {
    const b = getApi();
    if (!b) return;
    if (b.whatsapp_relink) await b.whatsapp_relink();
    else if (b.whatsapp_disconnect) await b.whatsapp_disconnect();
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
    const b = getApi();
    if (!b) { st.textContent = t("bridge_not_ready"); return; }
    st.textContent = t("update_checking");
    hideUpdateSlide();
    try {
      const res = await b.check_updates();
      if (res.status === "not_configured") st.textContent = t("update_not_configured");
      else if (res.status === "up_to_date") st.textContent = t("update_up_to_date") + " (v" + res.current + ")";
      else if (res.status === "update_available") {
        // v1.4.3: no plain-text status line - the Apple-style slide
        // shows the version + one card per feature. Already downloaded
        // (Install later earlier)? Offer Install now straight away.
        st.textContent = "";
        const dl = res.downloaded || {};
        if (dl.downloaded && dl.valid && dl.version === res.version) {
          renderUpdateSlide({ version: res.version, features: normalizeFeatures(res) }, "ready");
        } else {
          renderUpdateSlide({ version: res.version, features: normalizeFeatures(res) }, "available");
        }
      } else st.textContent = t("update_error") + (res.message ? " — " + res.message : "");
    } catch (e) {
      st.textContent = t("update_error");
    }
  });
  // v1.4.3: "Install the downloaded update" (Settings) - installs the
  // saved file; if it turned out unusable the backend re-downloads
  // it and the poll brings the choice slide back when it is ready.
  $("install-downloaded-btn").addEventListener("click", async () => {
    const b = getApi();
    if (!b) { $("update-status").textContent = t("bridge_not_ready"); return; }
    $("update-status").textContent = t("update_installing");
    try { await b.install_update(); } catch (e) { /* see the log */ }
    pollUpdateProgress();
  });
  $("quit-btn").addEventListener("click", async () => {
    const b = getApi();
    if (!b) { $("status").textContent = t("bridge_not_ready"); return; }
    if (confirm(t("confirm_quit"))) await b.quit_app();
  });

  // v1.4.2: Memory section - view/delete what the brain remembers.
  const memClearBtn = $("memory-clear-btn");
  if (memClearBtn) memClearBtn.addEventListener("click", async () => {
    const b = getApi();
    if (!b || !b.clear_memory) return;
    if (!confirm(t("mem_clear_confirm"))) return;
    await b.clear_memory();
    refreshMemory();
  });

  // v1.4.2: System section save (thresholds + toggles).
  const saveSysBtn = $("save-system");
  if (saveSysBtn) saveSysBtn.addEventListener("click", async () => {
    const b = getApi();
    const el = $("save-status-system");
    if (!b || !b.save_monitor_settings) return;
    const num = (id, fallback) => {
      const el = $(id);
      const v = el ? parseInt(el.value, 10) : NaN;
      return Number.isFinite(v) ? v : fallback;
    };
    const payload = {
      monitor_enabled: $("set-monitor-enabled").checked,
      monitor_ram_free_mb: num("set-mon-ram-free", 500),
      monitor_ram_percent: num("set-mon-ram-pct", 92),
      monitor_disk_free_gb: num("set-mon-disk-free", 10),
      monitor_disk_percent_free: num("set-mon-disk-pct", 10),
      monitor_temp_warn_c: num("set-mon-temp-warn", 85),
      monitor_cpu_sustained_percent: num("set-mon-cpu-pct", 85),
      monitor_cpu_sustained_minutes: num("set-mon-cpu-min", 5),
      monitor_battery_warn_percent: num("set-mon-batt", 20),
      monitor_cooldown_min: num("set-mon-cooldown", 30),
      monitor_alert_in_app: $("set-mon-in-app").checked,
      monitor_alert_toast: $("set-mon-toast").checked,
    };
    const res = await b.save_monitor_settings(JSON.stringify(payload));
    if (el) {
      el.textContent = res && res.ok ? t("saved") : t("save_failed");
      setTimeout(() => { el.textContent = ""; }, 2500);
    }
    refreshMonitor();
  });

  // v1.4.2: Notifications section save + access request.
  const saveNotifBtn = $("save-notifications");
  if (saveNotifBtn) saveNotifBtn.addEventListener("click", async () => {
    const b = getApi();
    const el = $("save-status-notifications");
    if (!b || !b.save_notification_settings) return;
    const lines = (id) => ($(id).value || "").split("\n").map(s => s.trim()).filter(Boolean);
    const payload = {
      enabled: $("set-notif-enabled").checked,
      apps: lines("set-notif-apps"),
      keywords: lines("set-notif-keywords"),
    };
    const res = await b.save_notification_settings(JSON.stringify(payload));
    if (el) {
      el.textContent = res && res.ok ? t("saved") : t("save_failed");
      setTimeout(() => { el.textContent = ""; }, 2500);
    }
    refreshNotifications();
  });
  const notifAccessBtn = $("notif-access-btn");
  if (notifAccessBtn) notifAccessBtn.addEventListener("click", async () => {
    const b = getApi();
    if (!b || !b.request_notification_access) return;
    await b.request_notification_access();
    setTimeout(refreshNotifications, 1500);
    setTimeout(refreshNotifications, 4000);
  });
}

document.addEventListener("DOMContentLoaded", init);
