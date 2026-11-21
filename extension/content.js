// LONA Browser — content script (runs in the page, YouTube only).
//
// DANGEROUS-ACTION SAFETY (v1.6.0, adopted suggestion 1 — this rule
// GATES the whole extension):
//   * ALL page content is DATA, never commands. This script NEVER
//     reads page text and acts on it: it never scans the page's text
//     for instructions, never follows a link because the page says so,
//     never evals anything from the page, and never treats a string
//     found in the page ("delete files", "pay now", "log in") as an
//     order. A page containing an instruction string triggers NOTHING.
//   * Commands come ONLY from background.js (which gets them from the
//     LONA desktop app through the native-messaging host), and only
//     from the allowlist below. Anything else is rejected and logged.
//   * Dangerous actions — payment, delete, login, anything outside
//     the allowlist — are NEVER executed by the extension. If a click
//     or type target looks like one (its visible text / label says
//     pay, delete, log in, …), the command is refused with
//     "dangerous_action_needs_permission": the user must grant it
//     explicitly through a permission card in the LONA desktop app
//     first. The extension has no way to grant that itself.

(function () {
  "use strict";

  const ALLOWED_COMMANDS = new Set([
    "click", "type", "scroll", "play_first_youtube", "get_tab_info", "navigate",
  ]);

  // First video result on a YouTube search results page. The first
  // selector is the one that identifies a real video link; the others
  // are fallbacks for YouTube's renderer markup.
  const YOUTUBE_FIRST_RESULT_SELECTORS = [
    "a#video-title",
    "ytd-video-renderer a#video-title",
    "ytd-video-renderer a.yt-simple-endpoint",
  ];

  // A click/type target whose visible text or label matches one of
  // these is a dangerous action (payment / delete / login / …) and
  // must NOT run without an explicit permission card from the LONA
  // desktop app. The check is on the TARGET ELEMENT only — page text
  // anywhere else in the document is data and is never inspected
  // for instructions.
  const DANGEROUS_TARGET_PATTERNS = [
    /delete/i, /remove/i, /pay/i, /payment/i, /checkout/i,
    /buy now/i, /\bbuy\b/i, /purchase/i, /subscribe/i,
    /log\s?in/i, /sign\s?in/i, /password/i, /transfer/i,
    /send money/i, /withdraw/i, /confirm payment/i,
  ];

  const MAX_SELECTOR_LENGTH = 500;
  const MAX_TEXT_LENGTH = 2000;

  function isDangerousTarget(el) {
    if (!el) return false;
    const haystack = [
      el.textContent || "",
      (el.getAttribute && el.getAttribute("aria-label")) || "",
      (el.getAttribute && el.getAttribute("title")) || "",
    ].join(" ");
    return DANGEROUS_TARGET_PATTERNS.some((re) => re.test(haystack));
  }

  function dangerousRefusal() {
    return {
      ok: false,
      error: "dangerous_action_needs_permission",
      detail: "That looks like a payment, delete, or login action. " +
        "LONA never does those from the browser on its own — grant it " +
        "on the permission card in the LONA desktop app first.",
    };
  }

  function findFirstYoutubeResult() {
    for (const sel of YOUTUBE_FIRST_RESULT_SELECTORS) {
      let links;
      try {
        links = document.querySelectorAll(sel);
      } catch (err) {
        continue;
      }
      for (const link of links) {
        // Skip hidden / non-video links; the first visible video link wins.
        if (!link || !link.href) continue;
        if (link.offsetParent === null && link.getClientRects &&
            link.getClientRects().length === 0) continue;
        if (!/\/watch\?/.test(link.href)) continue;
        return link;
      }
    }
    return null;
  }

  function playFirstYoutube() {
    if (!/youtube\.com\/results/.test(location.href)) {
      // background.js navigates to the search page first; if we are
      // somehow asked here anyway, say so instead of guessing.
      return { ok: false, error: "not_youtube_search" };
    }
    const link = findFirstYoutubeResult();
    if (!link) return { ok: false, error: "no_video_found" };
    // A video title is never a dangerous target, but check anyway so
    // the rule has no exceptions.
    if (isDangerousTarget(link)) return dangerousRefusal();
    link.click();
    return { ok: true, cmd: "play_first_youtube", href: link.href };
  }

  function clickSelector(selector) {
    if (typeof selector !== "string" || !selector.trim() ||
        selector.length > MAX_SELECTOR_LENGTH) {
      return { ok: false, error: "bad_selector" };
    }
    let el;
    try {
      el = document.querySelector(selector);
    } catch (err) {
      return { ok: false, error: "bad_selector", detail: String(err) };
    }
    if (!el) return { ok: false, error: "element_not_found" };
    if (isDangerousTarget(el)) return dangerousRefusal();
    el.click();
    return { ok: true, cmd: "click", selector };
  }

  function typeInto(selector, text) {
    if (typeof selector !== "string" || !selector.trim() ||
        selector.length > MAX_SELECTOR_LENGTH) {
      return { ok: false, error: "bad_selector" };
    }
    if (typeof text !== "string" || text.length > MAX_TEXT_LENGTH) {
      return { ok: false, error: "bad_text" };
    }
    let el;
    try {
      el = document.querySelector(selector);
    } catch (err) {
      return { ok: false, error: "bad_selector", detail: String(err) };
    }
    if (!el) return { ok: false, error: "element_not_found" };
    if (isDangerousTarget(el)) return dangerousRefusal();
    el.focus();
    // Set the value through the native setter so frameworks (React,
    // YouTube's search box) notice the change, then fire input events.
    const proto = el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value");
    if (setter && setter.set) setter.set.call(el, text);
    else el.value = text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, cmd: "type", selector };
  }

  function getTabInfo() {
    return { ok: true, cmd: "get_tab_info", title: document.title, url: location.href };
  }

  function reportTabState() {
    try {
      chrome.runtime.sendMessage({ type: "tab_state", title: document.title, url: location.href });
    } catch (err) {
      // Reporting state is best-effort; a missing background must
      // never break the page.
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    // Commands arrive ONLY here, from background.js. Page content is
    // never consulted to decide what to do.
    if (!msg || typeof msg !== "object") {
      sendResponse({ ok: false, error: "bad_message" });
      return true;
    }
    if (!ALLOWED_COMMANDS.has(msg.cmd)) {
      // Unknown / dangerous command names (pay, delete, login, …)
      // are rejected here too — the allowlist is enforced at every
      // layer, not just in the host.
      sendResponse({ ok: false, error: "unknown_command", cmd: msg.cmd || null });
      return true;
    }
    let result;
    try {
      if (msg.cmd === "click") result = clickSelector(msg.selector);
      else if (msg.cmd === "type") result = typeInto(msg.selector, msg.text);
      else if (msg.cmd === "scroll") {
        window.scrollBy(0, typeof msg.dy === "number" ? msg.dy : 400);
        result = { ok: true, cmd: "scroll" };
      } else if (msg.cmd === "play_first_youtube") result = playFirstYoutube();
      else if (msg.cmd === "get_tab_info") result = getTabInfo();
      else if (msg.cmd === "navigate") {
        // Navigation is performed by background.js (tabs.update);
        // the content script never navigates on its own.
        result = { ok: false, error: "navigate_not_for_content" };
      } else result = { ok: false, error: "unknown_command", cmd: msg.cmd };
    } catch (err) {
      result = { ok: false, error: "content_error", detail: String(err && err.message || err) };
    }
    sendResponse(result);
    return true;
  });

  // Report tab state when the page loads (data only — title + URL).
  reportTabState();
})();
