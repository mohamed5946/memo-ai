"""LONA v2 — device-health rules (thresholds from settings).

Each rule names the exact measurement that fired. Alerts are
rate-limited per rule (r8 cooldown) so the user is not spammed.
"""

import time

_RULES = [
    ("ram_free", "الرام الفاضي أقل من {v} MB",
     lambda o, s: (o["ram"]["used_gb"] is not None
                   and o["ram"]["total_gb"] is not None
                   and (o["ram"]["total_gb"] - o["ram"]["used_gb"]) * 1024
                   < s["r1_ram_free_mb"]),
     lambda o, s: {"limit": s["r1_ram_free_mb"],
                   "value": round((o["ram"]["total_gb"]
                                   - o["ram"]["used_gb"]) * 1024)}),
    ("ram_pct", "استخدام الرام أعلى من {v}%",
     lambda o, s: o["ram"]["percent"] is not None
     and o["ram"]["percent"] > s["r2_ram_pct"],
     lambda o, s: {"limit": s["r2_ram_pct"],
                   "value": o["ram"]["percent"]}),
    ("disk_free_gb", "المساحة الفاضية أقل من {v} GB",
     lambda o, s: o["disk"]["free_gb"] is not None
     and o["disk"]["free_gb"] < s["r3_disk_free_gb"],
     lambda o, s: {"limit": s["r3_disk_free_gb"],
                   "value": o["disk"]["free_gb"]}),
    ("disk_free_pct", "المساحة الفاضية أقل من {v}%",
     lambda o, s: o["disk"]["free_pct"] is not None
     and o["disk"]["free_pct"] < s["r4_disk_free_pct"],
     lambda o, s: {"limit": s["r4_disk_free_pct"],
                   "value": o["disk"]["free_pct"]}),
    ("cpu_temp", "حرارة المعالج فوق {v}°C",
     lambda o, s: o["temp"]["value"] is not None
     and o["temp"]["source"] == "lhm"
     and o["temp"]["value"] > s["r5_cpu_temp_c"],
     lambda o, s: {"limit": s["r5_cpu_temp_c"],
                   "value": o["temp"]["value"]}),
    ("battery", "البطارية أقل من {v}%",
     lambda o, s: o["battery"]["percent"] is not None
     and o["battery"]["percent"] < s["r7_battery_pct"]
     and not o["battery"]["charging"],
     lambda o, s: {"limit": s["r7_battery_pct"],
                   "value": o["battery"]["percent"]}),
]

_last_fired = {}


def evaluate(overview: dict, settings: dict) -> list:
    """Alerts that fired now (and are past their cooldown)."""
    if not settings.get("monitor_enabled", True):
        return []
    now = time.monotonic()
    cooldown = float(settings.get("r8_alert_cooldown_min", 30)) * 60
    fired = []
    for key, template, test, detail in _RULES:
        try:
            if not test(overview, settings):
                continue
        except Exception:
            continue
        last = _last_fired.get(key, 0.0)
        if now - last < cooldown:
            continue
        _last_fired[key] = now
        info = detail(overview, settings)
        fired.append({"rule": key,
                      "message": template.format(v=info["limit"]),
                      "limit": info["limit"], "value": info["value"]})
    return fired
