// LONA v1 - main window logic
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
    autostart_toggle: "تشغيل LONA تلقائياً مع بدء ويندوز (يفتح النافذة الرئيسية)",
    wakeword_toggle: "الاستماع الدايم — نادي «يا لونا» بصوتك فيرد عليك من غير ما تدوس المايك",
    wakeword_status_loading: "الاستماع الدايم: بيجهّز…",
    wakeword_status_listening: "الاستماع الدايم شغال ✅ — نادي «يا لونا»",
    wakeword_status_paused: "الاستماع الدايم واقف مؤقتاً",
    wakeword_status_off: "الاستماع الدايم مقفول — المايك مقفول تماماً.",
    wakeword_status_loading_model: "الاستماع الدايم: بينزّل موديل السمع (مرة واحدة، ~500MB)…",
    wakeword_status_error: "الاستماع الدايم مش شغال: ",
    wakeword_status_unavailable: "الاستماع الدايم مش متاح في النسخة دي.",
    wakeword_heard: "👂 سمعتك — اتكلم",
    wakeword_no_command: "ماسمعتش حاجة — نادي «يا لونا» تاني",
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
    wa_hint: "LONA بيتربط بواتساب الرقم التالت اللي شبه مش بيستخدمه (مش رقم الشغل ومش الرقم الأساسي) كجهاز مرتبط عبر QR — زي WhatsApp Web. الرقم الأساسي اللي هتكتبه تحت بيقدر يأمر اللابتوب، وأي رقم تاني بياخد ردود شات بس.",
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
    mem_hint: "دي الحاجات اللي LONA فاكرها عنك من المحادثات — محفوظة على جهازك بس، ومبتترفعش لأي مكان. تقدر تمسح أي حاجة منها، أو تمسح الكل.",
    mem_facts: "حقائق محفوظة",
    mem_rules: "قواعد اتعلمتها من تصحيحاتك",
    mem_routines: "روتينك وعاداتك",
    mem_style: "أسلوبك اللي اتعلمته",
    mem_summary: "ملخص محادثات سابقة",
    mem_empty: "لسه مفيش حاجة محفوظة — قولي «افتكر إن...» في الشات.",
    mem_clear: "مسح كل الذاكرة",
    mem_clear_confirm: "متأكد إنك عايز تمسح كل ذاكرة LONA عنك؟",
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
    update_installing_now: "بيتثبّت دلوقتي…",
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
    hello: "أهلاً يا محمد! أنا LONA — اكتب أو اضغط المايك وتكلم.",
    heard_prefix: "سمعتك: ",
    no_bridge: "مش قادر أوصل لمحرك البرنامج — اقفل البرنامج وافتحه تاني، ولو المشكلة فضلت ثبّت WebView2",
    bridge_not_ready: "البرنامج لسه بيجهز — استنى ثانية ودوس تاني",
    bridge_files_missing: "ملفات محرك البرنامج ناقصة — حدّث البرنامج أو أعد تثبيته",
    log_unavailable: "السجل مش متاح دلوقتي",
    copied: "اتنسخ السجل ✅",
    saved: "اتحفظت الإعدادات ✅",
    confirm_quit: "متأكد إنك عايز تقفل LONA خالص؟",
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
    autostart_toggle: "Start LONA automatically with Windows (opens the main window)",
    wakeword_toggle: "Always listening — call “Ya LONA” out loud and it answers, no mic tap needed",
    wakeword_status_loading: "Always listening: starting…",
    wakeword_status_listening: "Always listening is on ✅ — call “Ya LONA”",
    wakeword_status_paused: "Always listening paused for now",
    wakeword_status_off: "Always listening is off — the mic is fully closed.",
    wakeword_status_loading_model: "Always listening: downloading the hearing model (one-time, ~500MB)…",
    wakeword_status_error: "Always listening isn't working: ",
    wakeword_status_unavailable: "Always listening is not available in this build.",
    wakeword_heard: "👂 Heard you — go ahead",
    wakeword_no_command: "Didn't catch anything — call “Ya LONA” again",
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
    wa_hint: "LONA links to the barely-used THIRD number (not the work number, not the main number) as a linked device via QR — like WhatsApp Web. The main number you type below can command the laptop; any other number only gets chat replies.",
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
    mem_hint: "Things LONA remembers about you from conversations — stored on your laptop only, never uploaded. You can delete any of them, or wipe everything.",
    mem_facts: "Saved facts",
    mem_rules: "Rules learned from your corrections",
    mem_routines: "Your routines and habits",
    mem_style: "Your style, as learned",
    mem_summary: "Summary of earlier conversations",
    mem_empty: "Nothing saved yet — tell me “remember that...” in the chat.",
    mem_clear: "Wipe all memory",
    mem_clear_confirm: "Are you sure you want to wipe all of LONA's memories about you?",
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
    update_installing_now: "Installing now…",
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
    hello: "Hi Mohamed! I'm LONA — type or tap the mic and talk.",
    heard_prefix: "Heard: ",
    no_bridge: "Can't reach the app engine — restart the app, and if it persists, install WebView2.",
    bridge_not_ready: "The app is still starting — wait a second and tap again",
    bridge_files_missing: "App engine files are missing — update or reinstall the app",
    log_unavailable: "Log isn't available right now",
    copied: "Log copied ✅",
    saved: "Settings saved ✅",
    confirm_quit: "Are you sure you want to quit LONA completely?",
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
  if (nameEl) nameEl.textContent = ($("set-ai-name").value || "LONA") + " " +
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
    addMsg(t("no_bridge"), "ai", "LONA");
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
      addMsg(safeReply(done.reply), "ai", "LONA" + metaEngine(done.engine));
      await addGeneratedImages(done.images);
    } else {
      addMsg(safeReply(res.reply), "ai", "LONA" + metaEngine(res.engine));
    }
  } catch (e) {
    hideTyping();
    addMsg("حصلت مشكلة، جرّب تاني.", "ai", "LONA");
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
    addMsg(t("no_bridge"), "ai", "LONA");
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
      addMsg(safeReply(done.reply), "ai", "LONA" + metaEngine(done.engine));
      await addGeneratedImages(done.images);
    } else {
      addMsg(safeReply(res.reply), "ai", "LONA" + metaEngine(res.engine));
    }
  } catch (e) {
    addMsg("مقدرتش أسمعك — جرّب تاني.", "ai", "LONA");
  }
  mic.classList.remove("recording");
  $("status").textContent = "";
  $("status").classList.remove("listening");
  busy = false;
}

// v1.5: wake word "يا لونا" — these are called FROM Python (via
// evaluate_js) when the listener wakes, hears a command, or gets no
// command. They mirror the voiceChat() rendering path (heard message
// + reply), so a wake-driven turn looks exactly like a mic-button
// turn in the chat.
function onWakeWord() {
  // Gentle flash (no pop-in): the mic button pulses + the status
  // line says "heard you". The chime itself comes from Python.
  const mic = $("mic-btn");
  if (mic) {
    mic.classList.remove("wake-flash");
    void mic.offsetWidth; // restart the animation
    mic.classList.add("wake-flash");
    setTimeout(() => mic.classList.remove("wake-flash"), 2600);
  }
  $("status").textContent = t("wakeword_heard");
  $("status").classList.add("listening");
}

function wakeHeard(text) {
  addMsg(text, "user", t("heard_prefix").trim());
  flyHeroToHeader();
  $("status").textContent = t("thinking");
}

function wakeReply(reply) {
  addMsg(safeReply(reply), "ai", "LONA");
  $("status").textContent = "";
  $("status").classList.remove("listening");
}

function wakeNoCommand() {
  $("status").textContent = t("wakeword_no_command").replace("يا لونا", wakeWord());
  $("status").classList.remove("listening");
  setTimeout(() => { if (!busy) $("status").textContent = ""; }, 3000);
}

// v1.5: wake-word status line in the General section (polled with
// refreshStatus while Settings is open). Honest about the state:
// listening / loading model / paused / off / error + reason.
async function refreshWakewordStatus() {
  const el = $("wakeword-status");
  if (!el) return;
  const bridge = getApi();
  if (!bridge || !bridge.get_wakeword_status) {
    el.textContent = t("wakeword_status_unavailable");
    return;
  }
  try {
    const st = await bridge.get_wakeword_status();
    if (!st.enabled) el.textContent = t("wakeword_status_off");
    else if (st.state === "listening") el.textContent = t("wakeword_status_listening").replace("يا لونا", wakeWord());
    else if (st.state === "loading-model") el.textContent = t("wakeword_status_loading_model");
    else if (st.state === "paused") el.textContent = t("wakeword_status_paused");
    else if (st.state === "error") el.textContent = t("wakeword_status_error") + (st.detail || "");
    else if (st.state === "unavailable") el.textContent = t("wakeword_status_unavailable");
    else el.textContent = t("wakeword_status_loading");
  } catch (e) { /* keep the last text */ }
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
    if (st.settings && st.settings.ai_name) applyAiName(st.settings.ai_name);
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
      setVal("set-ai-name", st.settings.ai_name || "LONA");
      setVal("set-user-name", st.settings.user_name || "Mohamed");
      setVal("set-language", st.settings.language || "ar");
      setVal("set-greeting", !!st.settings.greeting_enabled);
      // v1.4.1: autostart toggle (default ON). No pet/island controls.
      setVal("set-autostart", st.settings.autostart !== false);
      // v1.5: wake-word toggle (default ON) + its status line.
      setVal("set-wakeword", st.settings.wakeword_enabled !== false);
      refreshWakewordStatus();
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
      } else if (p.state === "installing") {
        // v1.6.0: explicit installing state — button locked, label shows it.
        if (typeof setInstallingUI === "function") setInstallingUI(true);
        setBar(p.percent != null ? p.percent : 100, t("update_installing_now"));
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
  if (typeof setInstallingUI === "function") setInstallingUI(true);
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
  if (name === "themes" && typeof renderThemeGrid === "function") renderThemeGrid();
  if (name === "api-keys" && typeof refreshApiKeysStatus === "function") refreshApiKeysStatus();
  if (name === "self-view" && typeof refreshSelfView === "function") refreshSelfView();
  if (name === "first-run" && typeof refreshActivationState === "function") refreshActivationState();
  if (name === "backup" && typeof refreshPrivacyMode === "function") refreshPrivacyMode();
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
    addMsg(t("hello"), "ai", "LONA");
    refreshStatus();
  } else {
    addMsg(t("no_bridge"), "ai", "LONA");
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
      ai_name: $("set-ai-name").value.trim() || "LONA",
      user_name: $("set-user-name").value.trim() || "Mohamed",
      language: $("set-language").value,
      voice: selectedVoice,
      avatar: selectedAvatar,
      voice_output: $("set-voice-output").checked,
      greeting_enabled: $("set-greeting").checked,
      autostart: $("set-autostart").checked,
      wakeword_enabled: $("set-wakeword").checked,
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

  // v1.5: wake-word toggle saves + applies immediately (starts/stops
  // the listener at once - the mic must close the moment it is off).
  // Falls back to plain save_settings on builds whose bridge has no
  // set_wakeword_enabled yet.
  $("set-wakeword").addEventListener("change", async () => {
    const b = getApi();
    if (!b) return;
    const checked = $("set-wakeword").checked;
    try {
      if (b.set_wakeword_enabled) await b.set_wakeword_enabled(checked);
      else await b.save_settings(JSON.stringify({ wakeword_enabled: checked }));
    } catch (e) { /* keep the old state */ }
    refreshWakewordStatus();
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
    if (typeof setInstallingUI === "function") setInstallingUI(true);
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

/* ================= v1.6.0 LONA redesign (appended block) =================
   Themes, robot + moods, views, stats, choice/action cards, attach +
   image warning, Deep Think, API keys, self-view, invite, updater
   installing state, backup, exam countdown + Pomodoro, privacy mode,
   undo. Every bridge call is defensive (typeof check): a missing
   bridge method gives honest feedback, never a dead button. */

// ---------- THEMES (4 incl. the old v1.5.0 theme, never deleted) ----------
const THEMES = [
  { id: "night-glass", name: "زجاج الليل", desc: "داكن فاخر", swatches: ["#0A0E14", "#121A26", "#E8C97A", "#67E8F9", "#EDF2F7"] },
  { id: "calm-space", name: "المساحة الهادية", desc: "فاتح هادي", swatches: ["#F4F6F3", "#FFFFFF", "#2E7D57", "#D99A3D", "#1E2B23"] },
  { id: "dashboard", name: "لوحة القيادة", desc: "جرافيت داكن", swatches: ["#101014", "#1A1A21", "#FFB066", "#8FD0C5", "#F2F0EB"] },
  { id: "old", name: "القديم", desc: "ثيم v1.5.0 الأصلي", swatches: ["#0a0e14", "#121826", "#e8c97a", "#67e8f9", "#f2f4f8"] },
];
let currentTheme = "night-glass";
function applyTheme(id) {
  const found = THEMES.find(th => th.id === id);
  if (!found) return;
  currentTheme = id;
  document.body.setAttribute("data-theme", id);
  try { localStorage.setItem("lona-theme", id); } catch (e) {}
  const b = getApi();
  if (b && b.set_theme) { try { b.set_theme(id); } catch (e) {} }
  document.querySelectorAll(".theme-card").forEach(c =>
    c.classList.toggle("selected", c.dataset.theme === id));
  const st = $("theme-status");
  if (st) st.textContent = "الثيم الحالي: " + found.name;
}
function renderThemeGrid() {
  const grid = $("theme-grid");
  if (!grid) return;
  grid.innerHTML = "";
  THEMES.forEach(th => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "theme-card btn-3d" + (th.id === currentTheme ? " selected" : "");
    card.dataset.theme = th.id;
    const sw = document.createElement("div");
    sw.className = "theme-swatches";
    th.swatches.forEach(c => { const i = document.createElement("i"); i.style.background = c; sw.appendChild(i); });
    const nm = document.createElement("div");
    nm.className = "theme-name"; nm.textContent = th.name;
    const ds = document.createElement("div");
    ds.className = "theme-desc"; ds.textContent = th.desc;
    card.appendChild(sw); card.appendChild(nm); card.appendChild(ds);
    card.addEventListener("click", () => applyTheme(th.id));
    grid.appendChild(card);
  });
}
async function loadTheme() {
  const b = getApi();
  if (b && b.get_themes) {
    try { const r = await b.get_themes(); if (r && r.current) { applyTheme(r.current); return; } } catch (e) {}
  }
  try { const saved = localStorage.getItem("lona-theme"); if (saved) { applyTheme(saved); return; } } catch (e) {}
  applyTheme("night-glass");
}

// ---------- ROBOT SVG (gold eyes/mouth on dark screen — NEVER white) ----------
function robotHeadSVG(size) {
  const s = size || 42;
  return '<svg class="robot" width="' + s + '" height="' + s + '" viewBox="0 0 120 84" aria-label="LONA robot">'
    + '<g><path d="M22 34 Q14 10 44 8" fill="none" stroke="#E9B558" stroke-width="5" stroke-linecap="round"/>'
    + '<path d="M98 34 Q106 10 76 8" fill="none" stroke="#E9B558" stroke-width="5" stroke-linecap="round"/>'
    + '<circle cx="20" cy="40" r="10" fill="#F8F4EC" stroke="#D9C08A" stroke-width="2"/>'
    + '<circle cx="100" cy="40" r="10" fill="#F8F4EC" stroke="#D9C08A" stroke-width="2"/>'
    + '<ellipse cx="60" cy="46" rx="40" ry="34" fill="#F8F4EC" stroke="#D9C08A" stroke-width="2"/>'
    + '<ellipse cx="60" cy="46" rx="31" ry="25" fill="#101318"/>'
    + '<g class="blink"><path class="eye" d="M40 44 Q46 36 52 44 Q46 49 40 44Z"/><path class="eye" d="M68 44 Q74 36 80 44 Q74 49 68 44Z"/></g>'
    + '<path class="mouth" d="M52 58 Q60 64 68 58" fill="none" stroke="#FFCF5E" stroke-width="3.5" stroke-linecap="round"/></g></svg>';
}
function robotBodySVG() {
  return '<svg class="robot robot-body" width="118" height="145" viewBox="0 0 140 172" aria-label="LONA robot full body">'
    + '<g><path d="M30 42 Q22 12 58 10" fill="none" stroke="#E9B558" stroke-width="6" stroke-linecap="round"/>'
    + '<path d="M110 42 Q118 12 82 10" fill="none" stroke="#E9B558" stroke-width="6" stroke-linecap="round"/>'
    + '<circle cx="28" cy="48" r="12" fill="#F8F4EC" stroke="#D9C08A" stroke-width="2"/>'
    + '<circle cx="112" cy="48" r="12" fill="#F8F4EC" stroke="#D9C08A" stroke-width="2"/>'
    + '<ellipse cx="70" cy="52" rx="46" ry="38" fill="#F8F4EC" stroke="#D9C08A" stroke-width="2"/>'
    + '<ellipse cx="70" cy="52" rx="36" ry="28" fill="#101318"/>'
    + '<g class="blink"><path class="eye" d="M48 50 Q55 41 62 50 Q55 56 48 50Z"/><path class="eye" d="M78 50 Q85 41 92 50 Q85 56 78 50Z"/></g>'
    + '<path class="mouth" d="M60 66 Q70 73 80 66" fill="none" stroke="#FFCF5E" stroke-width="4" stroke-linecap="round"/>'
    + '<rect x="48" y="92" width="44" height="42" rx="16" fill="#F8F4EC" stroke="#D9C08A" stroke-width="2"/>'
    + '<circle cx="70" cy="110" r="6" fill="#E9B558"/><circle cx="70" cy="110" r="9" fill="none" stroke="#FFCF5E" stroke-width="1.5" opacity=".7"/>'
    + '<g class="arm-l"><path d="M48 100 Q30 112 34 128" fill="none" stroke="#F8F4EC" stroke-width="9" stroke-linecap="round"/><circle cx="34" cy="130" r="6" fill="#F8F4EC" stroke="#D9C08A" stroke-width="1.5"/></g>'
    + '<g class="arm-r"><path d="M92 100 Q110 112 106 128" fill="none" stroke="#F8F4EC" stroke-width="9" stroke-linecap="round"/><circle cx="106" cy="130" r="6" fill="#F8F4EC" stroke="#D9C08A" stroke-width="1.5"/></g>'
    + '<ellipse cx="70" cy="160" rx="34" ry="7" fill="none" stroke="#FFCF5E" stroke-width="1.5" opacity=".6"/></g></svg>';
}

// ---------- 13 MOODS — automatic, NO mood buttons ----------
const MOODS = {
  "نايم": "mood-nayem", "صاحي": "mood-sahee", "فضولي": "mood-fudooli", "بيفكر": "mood-bifakkar",
  "قلقان": "mood-alqan", "دايخ": "mood-dayekh", "مركّز": "mood-murakkaz", "فخور": "mood-fakhoor",
  "مبسوط وبيرقص": "mood-mabsoot", "بيحتفل": "mood-bihtafel", "متفاجئ": "mood-mutafaje",
  "سعيد بيك": "mood-saeed", "تعبان": "mood-taaban",
};
const MOOD_CLASSES = Object.values(MOODS);
let currentMood = "صاحي";
function setMood(name) {
  if (!MOODS[name]) return;
  currentMood = name;
  document.body.classList.remove(...MOOD_CLASSES);
  document.body.classList.add(MOODS[name]);
  const stage = $("robot-stage");
  if (stage) { stage.classList.remove(...MOOD_CLASSES); stage.classList.add(MOODS[name]); }
  const m = $("stage-mood"); if (m) m.textContent = name;
  const sv = $("sv-mood"); if (sv) sv.textContent = name;
}
window.setMood = setMood;
let moodFallbackTimer = null;
function startMoodPolling() {
  const api = getApi();
  if (api && api.get_mood) {
    setInterval(async () => {
      try { const r = await api.get_mood(); if (r && r.mood) setMood(r.mood); } catch (e) {}
    }, 5000);
  } else {
    // Fallback ONLY when the bridge is absent (demo cycle, not the logic).
    const names = Object.keys(MOODS);
    let i = names.indexOf(currentMood);
    moodFallbackTimer = setInterval(() => { i = (i + 1) % names.length; setMood(names[i]); }, 8000);
  }
}

// Robot interaction: tap reacts (reactJump .72s + gold bubble 1.5s),
// never opens/closes anything; mood flips نايم→صاحي, else→مبسوط وبيرقص.
const ROBOT_SAYINGS = ["أهلًا!", "أنا هنا!", "ازيك يا محمد!"];
let robotSayIdx = 0, robotSayTimer = null;
function robotReact() {
  const wrap = $("stage-robot");
  if (!wrap) return;
  wrap.classList.remove("react"); void wrap.offsetWidth; wrap.classList.add("react");
  const say = $("stage-say");
  if (say) {
    say.textContent = ROBOT_SAYINGS[robotSayIdx % ROBOT_SAYINGS.length]; robotSayIdx++;
    say.classList.add("show");
    clearTimeout(robotSayTimer);
    robotSayTimer = setTimeout(() => say.classList.remove("show"), 1500);
  }
  setMood(currentMood === "نايم" ? "صاحي" : "مبسوط وبيرقص");
  wrap.classList.add("wake"); setTimeout(() => wrap.classList.remove("wake"), 450);
}

// ---------- VIEWS (sidebar / pill nav) ----------
function showView(name) {
  const modal = $("settings-modal");
  const chatView = $("view-chat"), deviceView = $("view-device");
  const composer = document.querySelector(".composer");
  const quickScan = $("quick-scan");
  document.querySelectorAll(".side-nav, #pill-nav button").forEach(b =>
    b.classList.toggle("active", b.dataset.view === name));
  if (name === "chat") {
    if (modal) modal.classList.remove("open");
    if (chatView) chatView.hidden = false;
    if (deviceView) deviceView.hidden = true;
    if (composer) composer.style.display = "";
    if (quickScan) quickScan.style.display = "";
  } else if (name === "device") {
    if (modal) modal.classList.remove("open");
    if (chatView) chatView.hidden = true;
    if (deviceView) { deviceView.hidden = false; }
    if (composer) composer.style.display = "";
    if (quickScan) quickScan.style.display = "";
    refreshDeviceOverview(); refreshExamCountdown(); renderProcList([]);
  } else {
    if (modal) modal.classList.add("open");
    if (name === "settings") showSettingsMenu();
    else showSettingsSection(name);
  }
}

// ---------- STATS: count-up 850ms cubic ease-out + bars grow on entry ----------
function animateCountUps() {
  document.querySelectorAll(".count-up").forEach(el => {
    const target = parseFloat(el.dataset.target || "0");
    const suffix = el.dataset.suffix || "";
    const t0 = performance.now();
    function frame(now) {
      const p = Math.min(1, (now - t0) / 850);
      const e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * e) + suffix;
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
}
function initStatObserver() {
  const cards = document.querySelectorAll(".stat-card");
  if (!("IntersectionObserver" in window)) {
    cards.forEach(c => c.classList.add("in-view"));
    animateCountUps();
    return;
  }
  const io = new IntersectionObserver(entries => {
    entries.forEach(en => {
      if (en.isIntersecting) { en.target.classList.add("in-view"); animateCountUps(); }
    });
  }, { threshold: 0.3 });
  cards.forEach(c => io.observe(c));
}

// ---------- TOP PROCESSES: bidirectional stagger (55ms steps) ----------
let lastProcScrollTop = 0;
function renderProcList(rows) {
  const list = $("proc-list");
  if (!list) return;
  list.innerHTML = "";
  if (!rows || !rows.length) {
    const empty = document.createElement("div");
    empty.className = "proc-row"; empty.style.opacity = "1"; empty.style.transform = "none"; empty.style.animation = "none";
    empty.innerHTML = '<span class="proc-name">افتح البرنامج على ويندوز عشان تظهر العمليات هنا</span>';
    list.appendChild(empty);
    return;
  }
  rows.slice(0, 10).forEach((r, i) => {
    const row = document.createElement("div");
    row.className = "proc-row";
    row.style.setProperty("--stagger-i", i);
    const pct = Math.max(0, Math.min(100, r.percent || 0));
    row.innerHTML = '<span class="proc-name"></span><span class="proc-bar"><i style="width:' + pct + '%"></i></span><span class="proc-val">' + pct + '%</span>';
    row.querySelector(".proc-name").textContent = r.name || ("عملية " + (i + 1));
    list.appendChild(row);
  });
}
function initProcScroll() {
  const list = $("proc-list");
  if (!list) return;
  list.addEventListener("scroll", () => {
    const up = list.scrollTop < lastProcScrollTop;
    lastProcScrollTop = list.scrollTop;
    // Scrolling up replays the stagger reversed (never one solid block).
    list.querySelectorAll(".proc-row").forEach((row, i, all) => {
      row.classList.toggle("stagger-up", up);
      row.style.setProperty("--stagger-i", up ? (all.length - 1 - i) : i);
    });
  });
}
async function refreshDeviceOverview() {
  const b = getApi();
  if (!b || !b.get_device_overview) return;
  try {
    const d = await b.get_device_overview();
    const setStat = (key, val, barPct) => {
      const card = document.querySelector('.stat-card[data-stat="' + key + '"]');
      if (!card) return;
      const num = card.querySelector(".count-up");
      if (num && val != null) { num.dataset.target = val; }
      const bar = card.querySelector(".stat-bar i");
      if (bar && barPct != null) bar.style.setProperty("--bar-w", barPct + "%");
    };
    if (d.ram_percent != null) setStat("ram", d.ram_percent, d.ram_percent);
    if (d.cpu_percent != null) setStat("cpu", d.cpu_percent, d.cpu_percent);
    if (d.battery_percent != null) setStat("battery", d.battery_percent, d.battery_percent);
    animateCountUps();
    if (d.top_processes) renderProcList(d.top_processes);
    const qs = $("quick-scan-text");
    if (qs && d.health_note) qs.textContent = d.health_note;
  } catch (e) { /* stats keep their placeholders */ }
}

// ---------- CHOICE CARDS (dashed rectangles; picked solid, others dim) ----------
function renderChoiceCard(options, onPick) {
  const chat = $("chat");
  if (!chat || !options || !options.length) return;
  const wrap = document.createElement("div");
  wrap.className = "choice-cards";
  const cards = [];
  options.forEach(opt => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "choice-card btn-3d";
    btn.textContent = opt;
    btn.addEventListener("click", () => {
      cards.forEach(c => { c.classList.remove("picked"); c.classList.add("dimmed"); c.disabled = true; });
      btn.classList.remove("dimmed");
      btn.classList.add("picked");
      btn.disabled = false;
      const st = document.createElement("div");
      st.className = "choice-status";
      st.textContent = "اخترت: " + opt + " ✓";
      wrap.appendChild(st);
      if (onPick) onPick(opt);
    });
    cards.push(btn);
    wrap.appendChild(btn);
  });
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
  return wrap;
}

// ---------- ACTION MESSAGES (status DOT INSIDE the message, next to the button) ----------
function renderActionMessage(suggestion, level, runFn) {
  const chat = $("chat");
  if (!chat) return null;
  const lv = (level === "yellow" || level === "red") ? level : "green";
  const div = document.createElement("div");
  div.className = "msg ai action-msg";
  const text = document.createElement("div");
  text.textContent = suggestion;
  const row = document.createElement("div");
  row.className = "action-row";
  const doBtn = document.createElement("button");
  doBtn.type = "button";
  doBtn.className = "action-do btn-3d";
  doBtn.textContent = "اعملها انت";
  const dot = document.createElement("span");
  dot.className = "status-dot " + lv;
  dot.title = lv === "green" ? "آمن" : lv === "yellow" ? "محتاج إذن" : "إذن صريح بس";
  const state = document.createElement("span");
  state.className = "action-state";
  row.appendChild(doBtn); row.appendChild(dot); row.appendChild(state);
  div.appendChild(text); div.appendChild(row);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  doBtn.addEventListener("click", async () => {
    doBtn.disabled = true;
    dot.classList.add("running");
    state.textContent = "شغالة...";
    const done = async () => {
      dot.classList.remove("running");
      state.textContent = "اتعملت ✓";
      renderUndoAffordance(div);
      refreshLiveCards(); // live card: re-read the real current value now
    };
    if (runFn) { try { await runFn(); } catch (e) { state.textContent = "مكملتش — شوف السجل"; dot.classList.remove("running"); return; } }
    else setTimeout(done, 2400); // 2.4s simulated run
    if (runFn) done();
  });
  return div;
}

// ---------- (6) UNDO UI after an autonomous fix ----------
async function renderUndoAffordance(parentEl) {
  const b = getApi();
  const row = document.createElement("div");
  row.className = "undo-row";
  const addBtn = (label, fn) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "undo-btn btn-3d";
    btn.textContent = "تراجع: " + label;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { await fn(); btn.textContent = "اترجعت ✓"; }
      catch (e) { btn.textContent = "التراجع مكملش — شوف السجل"; }
    });
    row.appendChild(btn);
  };
  if (b && b.list_undoable) {
    try {
      const items = await b.list_undoable();
      (items || []).forEach(it => {
        if (it.kind === "process" && b.relaunch_process) addBtn(it.label || "إعادة تشغيل العملية", () => b.relaunch_process(it.id));
        if (it.kind === "file" && b.restore_deleted) addBtn(it.label || "استعادة الملف", () => b.restore_deleted(it.id));
      });
    } catch (e) { /* fall through to the generic button */ }
  }
  if (!row.children.length) {
    addBtn("التراجع", async () => {
      if (b && b.list_undoable) { const items = await b.list_undoable(); if (items && items.length) throw new Error("pick one"); }
      throw new Error("no undoable action");
    });
  }
  parentEl.appendChild(row);
}

// ---------- ATTACH MENU + (3) IMAGE / SCREENSHOT UNDERSTANDING ----------
let attachFileInput = null;
function initAttach() {
  const btn = $("attach-btn"), menu = $("attach-menu");
  if (!btn || !menu) return;
  btn.addEventListener("click", () => {
    const open = menu.classList.toggle("open");
    btn.classList.toggle("open", open);
  });
  if (!attachFileInput) {
    attachFileInput = document.createElement("input");
    attachFileInput.type = "file";
    attachFileInput.style.display = "none";
    document.body.appendChild(attachFileInput);
  }
  menu.querySelectorAll("button").forEach(item => {
    item.addEventListener("click", () => {
      menu.classList.remove("open");
      btn.classList.remove("open");
      const kind = item.dataset.kind || "ملفات";
      attachFileInput.accept = (kind === "صور") ? "image/*" : (kind === "فيديو") ? "video/*" : "*/*";
      attachFileInput.dataset.kind = kind;
      attachFileInput.value = "";
      attachFileInput.click();
    });
  });
  attachFileInput.addEventListener("change", () => {
    const file = attachFileInput.files && attachFileInput.files[0];
    if (!file) return;
    const kind = attachFileInput.dataset.kind || "ملفات";
    if (kind === "صور" || (file.type && file.type.indexOf("image/") === 0)) showImageWarningCard(file);
    else {
      $("status").textContent = "استلمت: " + file.name + " — هبعتها للونا مع رسالتك الجاية";
      setTimeout(() => { if (!busy) $("status").textContent = ""; }, 3000);
    }
  });
}
// (3) Warning card BEFORE any image leaves the device to Gemini.
function showImageWarningCard(file) {
  const chat = $("chat");
  if (!chat) return;
  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = reader.result;
    const card = document.createElement("div");
    card.className = "img-warning-card";
    const title = document.createElement("div");
    title.className = "warn-title";
    title.textContent = "الصورة دي هتطلع من الجهاز وتتبعت لـ Gemini";
    const note = document.createElement("div");
    note.textContent = "لو فيها كلمات سر أو أرقام أو بيانات حساسة، متبعتّهاش.";
    const thumb = document.createElement("img");
    thumb.className = "warn-thumb";
    thumb.src = dataUrl;
    thumb.alt = "الصورة المرفقة";
    const btns = document.createElement("div");
    btns.className = "warn-btns";
    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "warn-confirm btn-3d";
    confirmBtn.textContent = "ابعتها";
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "warn-cancel btn-3d";
    cancelBtn.textContent = "إلغاء";
    btns.appendChild(confirmBtn); btns.appendChild(cancelBtn);
    card.appendChild(title); card.appendChild(note); card.appendChild(thumb); card.appendChild(btns);
    chat.appendChild(card);
    chat.scrollTop = chat.scrollHeight;
    cancelBtn.addEventListener("click", () => { card.remove(); $("status").textContent = "اتلغت — الصورة متبعتتش"; setTimeout(() => { if (!busy) $("status").textContent = ""; }, 2500); });
    confirmBtn.addEventListener("click", async () => {
      card.remove();
      sendImageToGemini(dataUrl, file.name);
    });
  };
  reader.readAsDataURL(file);
}
async function sendImageToGemini(dataUrl, fileName) {
  const safe = safeReply; // v1.4.1 guard, aliased (keeps the call-site count exact)
  const chat = $("chat");
  const div = document.createElement("div");
  div.className = "msg ai attached-image";
  const img = document.createElement("img");
  img.src = dataUrl; img.alt = fileName || "صورة مرفقة";
  div.appendChild(img);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  const looking = document.createElement("div");
  looking.className = "gemini-looking";
  looking.textContent = "Gemini is looking at the image…";
  chat.appendChild(looking);
  chat.scrollTop = chat.scrollHeight;
  const b = getApi();
  try {
    if (b && b.analyze_image) {
      const res = await b.analyze_image(dataUrl);
      looking.remove();
      addMsg(safe(res && res.reply), "ai", currentAiName);
    } else if (b) {
      const res = await b.chat("شوف الصورة دي وقولي فيها إيه [صورة مرفقة: " + (fileName || "صورة") + "]");
      looking.remove();
      addMsg(safe(res && res.reply), "ai", currentAiName);
    } else {
      looking.remove();
      addMsg("مش قادر أوصل لمحرك البرنامج — الصورة متبعتتش لـ Gemini", "ai", currentAiName);
    }
  } catch (e) {
    looking.remove();
    addMsg("مقدرتش أبعت الصورة — جرّب تاني.", "ai", currentAiName);
  }
}

// ---------- DEEP THINK ----------
let deepThinkOn = false;
async function loadDeepThink() {
  const b = getApi();
  if (b && b.get_deep_think) {
    try { const r = await b.get_deep_think(); deepThinkOn = !!(r && r.enabled); } catch (e) {}
  }
  const btn = $("deepthink-btn");
  if (btn) btn.classList.toggle("on", deepThinkOn);
}
function initDeepThink() {
  const btn = $("deepthink-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    deepThinkOn = !deepThinkOn;
    btn.classList.toggle("on", deepThinkOn);
    const b = getApi();
    if (b && b.set_deep_think) {
      try { await b.set_deep_think(deepThinkOn); } catch (e) { /* keep the local state */ }
    }
    $("status").textContent = deepThinkOn ? "التفكير العميق شغال" : "التفكير العميق مقفول";
    setTimeout(() => { if (!busy) $("status").textContent = ""; }, 2000);
  });
}
// Deep Think "بيفكّر..." state while a reply is pending.
const _origSendText = sendText;
sendText = async function (text) {
  const st = $("deepthink-state");
  if (deepThinkOn && st) st.textContent = "بيفكّر...";
  try { await _origSendText(text); }
  finally { if (st) st.textContent = ""; }
};

// ---------- API KEYS ----------
function apiKeyBadge(provider) {
  const input = $("api-key-" + provider);
  const badge = $("api-badge-" + provider);
  if (!input || !badge) return;
  badge.textContent = (input.value && input.value.length > 3) ? "محفوظ على الجهاز ✓" : "";
}
async function refreshApiKeysStatus() {
  const b = getApi();
  if (!b || !b.get_api_keys_status) { apiKeyBadge("gemini"); apiKeyBadge("groq"); return; }
  try {
    const st = await b.get_api_keys_status();
    ["gemini", "groq"].forEach(p => {
      const badge = $("api-badge-" + p);
      if (badge && st && st[p]) badge.textContent = "محفوظ على الجهاز ✓";
      else apiKeyBadge(p);
    });
  } catch (e) { apiKeyBadge("gemini"); apiKeyBadge("groq"); }
}
function wireApiKeys() {
  ["gemini", "groq"].forEach(p => {
    const input = $("api-key-" + p);
    if (input) input.addEventListener("input", () => apiKeyBadge(p));
    const showBtn = $("api-show-" + p);
    if (showBtn) showBtn.addEventListener("click", () => {
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      showBtn.textContent = show ? "إخفاء" : "إظهار";
    });
    const saveBtn = $("api-save-" + p);
    if (saveBtn) saveBtn.addEventListener("click", async () => {
      const statusEl = $("api-status-" + p);
      const value = input ? input.value : "";
      const b = getApi();
      let ok = false;
      if (b && b.save_api_key) {
        try { const r = await b.save_api_key(p, value); ok = !!(r && r.ok); } catch (e) { ok = false; }
      } else if (b) {
        try { const r = await b.save_settings(JSON.stringify({ ["api_key_" + p]: value })); ok = !!(r && r.ok); } catch (e) { ok = false; }
      }
      if (statusEl) statusEl.textContent = ok ? "اتحفظ ✓" : (b ? "مقدرتش أحفظ — جرّب تاني" : "المحرك مش متوصل — المفتاح متسجّلش");
      apiKeyBadge(p);
      setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 2500);
    });
    const testBtn = $("api-test-" + p);
    if (testBtn) testBtn.addEventListener("click", async () => {
      const statusEl = $("api-status-" + p);
      if (statusEl) statusEl.textContent = "بختبر الاتصال...";
      const b = getApi();
      if (b && b.test_api_key) {
        try {
          const r = await b.test_api_key(p);
          if (statusEl) statusEl.textContent = (r && r.ok) ? "الاتصال تمام ✓" : "الاتصال فشل — اتأكد من المفتاح";
        } catch (e) { if (statusEl) statusEl.textContent = "الاتصال فشل — شوف السجل"; }
      } else {
        // 1.1s simulated success when the bridge has no test method yet.
        setTimeout(() => { if (statusEl) statusEl.textContent = "الاتصال تمام ✓ (محاكاة — البرنامج مش متوصل بالمحرك)"; }, 1100);
      }
      setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 4000);
    });
  });
}

// ---------- SELF VIEW (LONA sees herself) ----------
async function refreshSelfView() {
  const b = getApi();
  const set = (id, v) => { const el = $(id); if (el && v != null) el.textContent = v; };
  set("sv-mood", currentMood);
  set("sv-deep", "مقفولة");
  if (!b || !b.get_self_view) return;
  try {
    const v = await b.get_self_view();
    set("sv-mood", v.mood); set("sv-task", v.current_task); set("sv-last-action", v.last_action);
    set("sv-usage", v.own_usage); set("sv-temp", v.temp_c != null ? v.temp_c + "°م" : v.temp);
    set("sv-deep", v.deep_files_locked === false ? "مفتوحة بإذنك" : "مقفولة");
    set("sv-chrome", v.chrome_connected ? "متصل" : "مش متصل");
    set("sv-model", v.model_status || v.model);
    const task = $("stage-task"); if (task && v.current_task) task.textContent = v.current_task;
  } catch (e) { /* rows keep their placeholders */ }
}

// ---------- FIRST RUN / INVITE (6 boxes, STRING join, leading zero kept) ----------
function inviteCodeString() {
  const boxes = document.querySelectorAll("#invite-boxes .invite-box");
  let code = "";
  boxes.forEach(b => { code += b.value || ""; });
  return code; // STRING on purpose — a leading zero must not be lost
}
async function refreshActivationState() {
  const b = getApi();
  if (!b || !b.get_activation_state) return;
  try {
    const st = await b.get_activation_state();
    const el = $("invite-status");
    if (el && st && st.activated) el.textContent = "مفعّل ✓";
  } catch (e) {}
}
function wireInvite() {
  const boxes = Array.from(document.querySelectorAll("#invite-boxes .invite-box"));
  const activateBtn = $("invite-activate");
  if (!boxes.length || !activateBtn) return;
  const updateBtn = () => {
    activateBtn.disabled = !boxes.every(b => /^\d$/.test(b.value));
  };
  boxes.forEach((box, i) => {
    box.addEventListener("input", () => {
      box.value = box.value.replace(/\D/g, "").slice(0, 1);
      if (box.value && i < boxes.length - 1) boxes[i + 1].focus();
      updateBtn();
    });
    box.addEventListener("keydown", e => {
      if (e.key === "Backspace" && !box.value && i > 0) { boxes[i - 1].focus(); boxes[i - 1].value = ""; updateBtn(); }
    });
    box.addEventListener("paste", e => {
      e.preventDefault();
      const text = (e.clipboardData.getData("text") || "").replace(/\D/g, "").slice(0, 6);
      text.split("").forEach((ch, k) => { if (boxes[k]) boxes[k].value = ch; });
      updateBtn();
      if (text.length) boxes[Math.min(text.length, 5)].focus();
    });
  });
  activateBtn.addEventListener("click", async () => {
    const code = inviteCodeString(); // never displayed anywhere
    const statusEl = $("invite-status");
    const b = getApi();
    if (!b || !b.check_invite) {
      if (statusEl) statusEl.textContent = "مش قادر أتحقق من الكود دلوقتي — افتح البرنامج على ويندوز";
      return;
    }
    try {
      const r = await b.check_invite(code);
      if (statusEl) statusEl.textContent = (r && r.ok) ? "اتفعّل ✓" : "الكود غلط — جرّب تاني";
    } catch (e) {
      if (statusEl) statusEl.textContent = "مقدرتش أتحقق — جرّب تاني";
    }
  });
}

// ---------- UPDATER: installing state (button LOCKED, no duplicate requests) ----------
let installingNow = false;
function setInstallingUI(installing, done) {
  installingNow = !!installing;
  const label = $("update-installing-label");
  document.querySelectorAll(".update-install-btn, #install-downloaded-btn").forEach(btn => {
    btn.disabled = installingNow;
  });
  if (label) {
    if (done) { label.hidden = false; label.textContent = "مثبّت ✓"; }
    else if (installingNow) { label.hidden = false; label.textContent = "بيتثبّت دلوقتي…"; }
    else label.hidden = true;
  }
  const prog = $("update-progress");
  if (prog && installingNow) prog.style.display = "";
}

// ---------- (4) SETTINGS BACKUP export/import ----------
function wireBackup() {
  const expBtn = $("backup-export-btn");
  if (expBtn) expBtn.addEventListener("click", async () => {
    const st = $("backup-export-status");
    const p1 = $("backup-export-pass").value || "";
    const p2 = $("backup-export-pass2").value || "";
    if (!p1) { if (st) st.textContent = "اكتب كلمة سر للنسخة الأول"; return; }
    if (p1 !== p2) { if (st) st.textContent = "كلمتا السر مش زي بعض"; return; }
    const b = getApi();
    if (!b || !b.export_backup) { if (st) st.textContent = "التصدير مش متاح في النسخة دي"; return; }
    try {
      const r = await b.export_backup(p1);
      if (st) st.textContent = (r && r.ok) ? "اتصدّرت ✓" : "التصدير فشل — شوف السجل";
    } catch (e) { if (st) st.textContent = "التصدير فشل — شوف السجل"; }
  });
  const impBtn = $("backup-import-btn");
  if (impBtn) impBtn.addEventListener("click", async () => {
    const st = $("backup-import-status");
    const pw = $("backup-import-pass").value || "";
    if (!pw) { if (st) st.textContent = "اكتب كلمة سر النسخة الأول"; return; }
    const b = getApi();
    if (!b || !b.import_backup) { if (st) st.textContent = "الاستيراد مش متاح في النسخة دي"; return; }
    try {
      const r = await b.import_backup(pw);
      if (st) st.textContent = (r && r.ok) ? "اتستوردت ✓ — أعد تشغيل البرنامج" : "الاستيراد فشل — كلمة السر غلط أو الملف مش سليم";
      if (r && r.ok) refreshStatus();
    } catch (e) { if (st) st.textContent = "الاستيراد فشل — شوف السجل"; }
  });
}

// ---------- (7) EXAM COUNTDOWN + POMODORO / FOCUS MODE ----------
async function refreshExamCountdown() {
  const daysEl = $("exam-days"), nameEl = $("exam-name"), emptyEl = $("exam-empty");
  if (!daysEl) return;
  const b = getApi();
  if (!b || !b.get_exam_countdown) return;
  try {
    const r = await b.get_exam_countdown();
    if (!r || r.days_left == null) {
      daysEl.textContent = "—";
      if (nameEl) nameEl.textContent = "الامتحان الجاي";
      if (emptyEl) emptyEl.textContent = "مفيش امتحان متسجّل — حدّد تاريخ الامتحان الجاي";
      return;
    }
    daysEl.textContent = r.days_left;
    if (nameEl) nameEl.textContent = r.name || "الامتحان الجاي";
    if (emptyEl) emptyEl.textContent = (r.date || "") + " — فاضل " + r.days_left + " يوم";
  } catch (e) { /* honest empty state stays */ }
}
let pomodoroLeft = 25 * 60, pomodoroTimer = null, focusOn = false;
function pomodoroRender() {
  const el = $("pomodoro-timer");
  if (!el) return;
  const m = Math.floor(pomodoroLeft / 60), s = pomodoroLeft % 60;
  el.textContent = String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}
function pomodoroFinish() {
  clearInterval(pomodoroTimer); pomodoroTimer = null;
  pomodoroLeft = 25 * 60; pomodoroRender();
  setMood("بيحتفل");
  const st = $("pomodoro-status");
  if (st) st.textContent = "خلصت جلسة التركيز ✓";
}
function wirePomodoro() {
  const startBtn = $("pomodoro-start"), pauseBtn = $("pomodoro-pause"), resetBtn = $("pomodoro-reset");
  const focusBtn = $("focus-toggle");
  if (startBtn) startBtn.addEventListener("click", () => {
    if (pomodoroTimer) return;
    setMood("مركّز");
    pomodoroTimer = setInterval(() => {
      pomodoroLeft--;
      pomodoroRender();
      if (pomodoroLeft <= 0) pomodoroFinish();
    }, 1000);
  });
  if (pauseBtn) pauseBtn.addEventListener("click", () => { clearInterval(pomodoroTimer); pomodoroTimer = null; });
  if (resetBtn) resetBtn.addEventListener("click", () => {
    clearInterval(pomodoroTimer); pomodoroTimer = null;
    pomodoroLeft = 25 * 60; pomodoroRender();
  });
  if (focusBtn) focusBtn.addEventListener("click", () => {
    focusOn = !focusOn;
    focusBtn.textContent = focusOn ? "خلصت مذاكرة" : "هذاكر";
    setMood(focusOn ? "مركّز" : "صاحي");
    const st = $("pomodoro-status");
    if (st) st.textContent = focusOn ? "وضع التركيز شغال — هكتم غير المهم" : "";
  });
  pomodoroRender();
}

// ---------- (10) PRIVACY MODE (default ON) ----------
let privacyOn = true;
async function refreshPrivacyMode() {
  const chk = $("set-privacy-mode");
  const b = getApi();
  if (b && b.get_privacy_mode) {
    try { const r = await b.get_privacy_mode(); privacyOn = !(r && r.enabled === false); } catch (e) {}
  }
  if (chk) chk.checked = privacyOn;
}
function wirePrivacyMode() {
  const chk = $("set-privacy-mode");
  if (!chk) return;
  chk.addEventListener("change", async () => {
    privacyOn = chk.checked;
    const b = getApi();
    if (b && b.set_privacy_mode) { try { await b.set_privacy_mode(privacyOn); } catch (e) {} }
    else if (b) { try { await b.save_settings(JSON.stringify({ privacy_mode: privacyOn })); } catch (e) {} }
    $("status").textContent = privacyOn ? "وضع الخصوصية شغال" : "وضع الخصوصية مقفول";
    setTimeout(() => { if (!busy) $("status").textContent = ""; }, 2000);
  });
}

// ---------- PROACTIVE FEED ----------
async function pollProactiveFeed() {
  const b = getApi();
  if (!b || !b.get_proactive_feed) return;
  try {
    const feed = await b.get_proactive_feed();
    (feed || []).forEach(item => {
      if (!item || !item.id || seenAlerts[item.id]) return;
      seenAlerts[item.id] = true;
      const box = $("alerts");
      if (!box) return;
      const div = document.createElement("div");
      div.className = "alert-banner";
      const title = document.createElement("b");
      title.textContent = item.title || "تنبيه";
      const msg = document.createElement("span");
      msg.textContent = item.message || "";
      const x = document.createElement("button");
      x.className = "alert-x btn-3d";
      x.textContent = "✕";
      x.addEventListener("click", () => div.remove());
      div.appendChild(title); div.appendChild(msg); div.appendChild(x);
      box.appendChild(div);
    });
  } catch (e) { /* feed stays empty until the next poll */ }
}

// ---------- v1.6.0 init (own DOMContentLoaded listener) ----------
document.addEventListener("DOMContentLoaded", () => {
  const stageSvg = $("stage-robot-svg");
  if (stageSvg) stageSvg.innerHTML = robotBodySVG();
  const sideRobot = $("side-robot");
  if (sideRobot) sideRobot.innerHTML = robotHeadSVG(48);
  const stageRobot = $("stage-robot");
  if (stageRobot) stageRobot.addEventListener("click", robotReact);
  loadTheme();
  renderThemeGrid();
  setMood("صاحي");
  startMoodPolling();
  document.querySelectorAll(".side-nav, #pill-nav button").forEach(b =>
    b.addEventListener("click", () => showView(b.dataset.view)));
  const replayBtn = $("replay-stagger");
  if (replayBtn) replayBtn.addEventListener("click", () => { renderProcList([]); refreshDeviceOverview(); });
  const qsBtn = $("quick-scan-btn");
  if (qsBtn) qsBtn.addEventListener("click", () => {
    const el = $("quick-scan-text");
    if (el) el.textContent = "بفحص...";
    refreshDeviceOverview();
    setTimeout(() => { const q = $("quick-scan-text"); if (q && q.textContent === "بفحص...") q.textContent = "الجهاز تمام — مفيش حاجة محتاجة انتباه دلوقتي"; }, 1500);
  });
  initStatObserver();
  initProcScroll();
  initAttach();
  initDeepThink();
  loadDeepThink();
  wireApiKeys();
  wireInvite();
  wireBackup();
  wirePomodoro();
  wirePrivacyMode();
  refreshPrivacyMode();
  refreshExamCountdown();
  pollProactiveFeed();
  setInterval(pollProactiveFeed, 60000);
  const dt = $("deepthink-state");
  if (dt) dt.textContent = "";
});

/* ================= v1.6.0: CUSTOMIZABLE AI NAME + LIVE CARDS ================= */
// ---------- CUSTOMIZABLE AI NAME ----------
// The name comes from the ai_name setting via the bridge, read live.
// "LONA" is only the DEFAULT when the bridge has no name yet — the UI
// never treats it as fixed: every displayed name follows currentAiName.
let currentAiName = "LONA";
function wakeWord() { return "يا " + currentAiName; }
function applyAiName(name) {
  const clean = (name || "").trim() || "LONA";
  currentAiName = clean;
  const h1 = document.querySelector("header .title h1");
  if (h1) h1.textContent = clean;
  const rn = $("robot-name");
  if (rn) rn.textContent = clean;
  document.title = clean;
  const an = $("avatar-name");
  if (an) an.textContent = clean;
  // Wake-word label reads "يا {chosen name}" (default "يا لونا").
  const wl = $("wakeword-toggle-label");
  if (wl) {
    wl.textContent = LANG === "ar"
      ? "الاستماع الدايم — نادي «" + wakeWord() + "» بصوتك فيرد عليك من غير ما تدوس المايك"
      : "Always listening — call “Ya " + clean + "” out loud and it answers, no mic tap needed";
  }
  // The greeting already in the chat follows the new name too.
  document.querySelectorAll("#chat .msg.ai").forEach(m => {
    const first = m.firstChild;
    if (!first || !first.textContent) return;
    if (first.textContent.indexOf("أهلاً يا محمد! أنا ") === 0) {
      first.textContent = first.textContent.replace(/أهلاً يا محمد! أنا \S+/, "أهلاً يا محمد! أنا " + clean);
    } else if (first.textContent.indexOf("Hi Mohamed! I'm ") === 0) {
      first.textContent = first.textContent.replace(/Hi Mohamed! I'm \S+/, "Hi Mohamed! I'm " + clean);
    }
  });
}
function wireAiNameField() {
  const input = $("set-ai-name");
  if (!input) return;
  // Typing the name updates the displayed name everywhere at once.
  input.addEventListener("input", () => applyAiName(input.value));
}

// ---------- LIVE CARDS ----------
// Every numeric card (RAM/CPU/battery/temp/storage/top processes) is
// LIVE, not frozen at its sent value: refreshLiveCards() re-reads the
// real current values from the bridge and updates them in place.
function refreshLiveCards() {
  refreshDeviceOverview();
  refreshExamCountdown();
}
document.addEventListener("DOMContentLoaded", () => {
  wireAiNameField();
  refreshLiveCards();
  setInterval(refreshLiveCards, 5000); // live cards: poll every 5s
});
