/* LONA reskin (v1.6.3 base + v2 Liquid Glass look) — shared pywebview
   bridge helper.
   Rules: every bridge call uses a fresh getApi() (polls up to 10s for
   window.pywebview.api); errors are NEVER swallowed — they are shown
   in the page error bar and reported via log_error.
   FIX vs the v2 file: pywebview 6.2.1 exposes ONLY window.pywebview.api
   — window.pywebview.js_api does not exist (that was the v2.0.0-rebuild.5
   dead-bridge bug: every button dead, key test stuck on "testing"
   forever). .api MUST come first; js_api stays only as a fallback for
   any older pywebview that still exposes it.
   no demo data. */
(function (global) {
  'use strict';

  var BRIDGE_TIMEOUT_MS = 10000;
  var POLL_MS = 100;

  function getApi() {
    return new Promise(function (resolve, reject) {
      var start = Date.now();
      (function poll() {
        var api = null;
        try {
          /* .api FIRST — the only property pywebview 6.2.1 exposes. */
          api = global.pywebview && (global.pywebview.api || global.pywebview.js_api);
        } catch (err) {
          api = null;
        }
        if (api) {
          resolve(api);
          return;
        }
        if (Date.now() - start > BRIDGE_TIMEOUT_MS) {
          reject(new Error('الجسر مش متاح — pywebview.api لم يظهر خلال 10 ثواني'));
          return;
        }
        setTimeout(poll, POLL_MS);
      })();
    });
  }

  /* Report a failure to the Python diagnostics log. Best effort only:
     if the bridge itself is down there is nothing to call — the error
     is still written to console and shown on screen by the caller. */
  function logError(source, message) {
    try {
      console.error('[LONA UI][' + source + '] ' + message);
    } catch (err) {
      void err; // console itself unavailable — nothing else to do.
    }
    getApi().then(function (api) {
      try {
        var p = api.log_error(String(source), String(message));
        if (p && typeof p.catch === 'function') {
          p.catch(function (err) {
            try { console.error('[LONA UI] log_error failed: ' + err); } catch (e) { void e; }
          });
        }
      } catch (err) {
        try { console.error('[LONA UI] log_error call failed: ' + err); } catch (e) { void e; }
      }
    }).catch(function () {
      showError(message); // Bridge down — the error is already on console too.
    });
  }

  /* Show a failure to the user in the page error bar (if present). */
  function showError(message) {
    var bar = document.getElementById('errbar');
    var txt = document.getElementById('errbarText');
    if (bar && txt) {
      txt.textContent = String(message);
      bar.hidden = false;
    } else {
      try { console.error('[LONA UI] ' + message); } catch (e) { void e; }
    }
  }

  function hideError() {
    var bar = document.getElementById('errbar');
    if (bar) { bar.hidden = true; }
  }

  /* One bridge call with a FRESH getApi(). Throws on failure; the caller
     must catch, showError() and logError() — never swallow silently.
     Callers that set a transient "testing…" / "بيتم…" label MUST reset
     it in their own .catch() — a bridge timeout rejects here, and the
     label would otherwise stay stuck forever (the v2 key-test bug). */
  function call(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    return getApi().then(function (api) {
      if (typeof api[method] !== 'function') {
        throw new Error('دالة الجسر مش موجودة: ' + method);
      }
      return api[method].apply(api, args);
    });
  }

  /* Startup-critical calls get retries with backoff: the field
     failure (2026-11-23) was a blank white main window + «دالة الجسر
     مش موجودة: get_settings» even though the method EXISTS — a
     runtime/race failure on first paint. 3 attempts: immediate,
     +1s, +3s. Failures are still thrown so the caller surfaces
     them (showError/logError) — never swallowed. */
  var CALL_RETRY_DELAYS_MS = [0, 1000, 3000];

  function callRetry(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    var attempt = 0;
    function tryOnce() {
      return call.apply(null, [method].concat(args)).catch(function (err) {
        attempt += 1;
        if (attempt >= CALL_RETRY_DELAYS_MS.length) { throw err; }
        var delay = CALL_RETRY_DELAYS_MS[attempt];
        return new Promise(function (resolve) { setTimeout(resolve, delay); }).then(tryOnce);
      });
    }
    return tryOnce();
  }

  /* Poll a pending async result: fn(id) must return {done:bool,...}.
     Resolves with the final object, rejects on timeout. */
  function pollUntil(id, fn, timeoutMs, intervalMs) {
    var deadline = Date.now() + (timeoutMs || 120000);
    var step = intervalMs || 800;
    return new Promise(function (resolve, reject) {
      (function tick() {
        Promise.resolve()
          .then(function () { return fn(id); })
          .then(function (res) {
            if (res && res.done) {
              resolve(res);
            } else if (Date.now() > deadline) {
              reject(new Error('انتهت المهلة أثناء انتظار الرد'));
            } else {
              setTimeout(tick, step);
            }
          })
          .catch(function (err) {
            reject(err instanceof Error ? err : new Error(String(err)));
          });
      })();
    });
  }

  /* Promise with a hard timeout (used for check_invite: 10s). */
  function withTimeout(promise, ms, label) {
    return Promise.race([
      promise,
      new Promise(function (_, reject) {
        setTimeout(function () {
          reject(new Error((label || 'الطلب') + ' خد أكتر من ' + Math.floor(ms / 1000) + ' ثواني — جرّب تاني'));
        }, ms);
      })
    ]);
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* Arabic-Indic / Extended Arabic-Indic digits -> ASCII. */
  function toAsciiDigits(s) {
    return String(s == null ? '' : s)
      .replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 0x660); })
      .replace(/[۰-۹]/g, function (d) { return String(d.charCodeAt(0) - 0x6F0); });
  }

  function reportFailure(source, err) {
    var msg = err && err.message ? err.message : String(err);
    logError(source, msg);
    showError(msg);
  }

  global.LonaBridge = {
    getApi: getApi,
    call: call,
    callRetry: callRetry,
    logError: logError,
    showError: showError,
    hideError: hideError,
    pollUntil: pollUntil,
    withTimeout: withTimeout,
    escapeHtml: escapeHtml,
    toAsciiDigits: toAsciiDigits,
    reportFailure: reportFailure
  };
})(window);
