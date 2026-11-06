# Memo AI - Weather via Open-Meteo (free, no key needed)
# Location: 6th of October City, Egypt (~29.96, 30.92).

import requests
from logger import error, info

LAT, LON = 29.96, 30.92
URL = ("https://api.open-meteo.com/v1/forecast"
       f"?latitude={LAT}&longitude={LON}"
       "&current=temperature_2m,weather_code&timezone=Africa%2FCairo")

# WMO weather codes -> Arabic description
CODES = {
    0: ("صافي", "☀️"), 1: ("صافي غالباً", "🌤️"), 2: ("غائم جزئياً", "⛅"),
    3: ("غائم", "☁️"), 45: ("ضباب", "🌫️"), 48: ("ضباب متجمد", "🌫️"),
    51: ("رذاذ خفيف", "🌦️"), 53: ("رذاذ", "🌦️"), 55: ("رذاذ شديد", "🌦️"),
    61: ("مطر خفيف", "🌧️"), 63: ("مطر", "🌧️"), 65: ("مطر شديد", "🌧️"),
    71: ("ثلج خفيف", "🌨️"), 73: ("ثلج", "🌨️"), 75: ("ثلج شديد", "🌨️"),
    80: ("أمطار متقطعة", "🌧️"), 81: ("أمطار", "🌧️"), 82: ("أمطار غزيرة", "⛈️"),
    95: ("عاصفة رعدية", "⛈️"), 96: ("عاصفة رعدية وبَرَد", "⛈️"),
    99: ("عاصفة رعدية وبَرَد شديد", "⛈️"),
}


def get_weather() -> dict:
    """Returns {"temp": int, "desc": str, "emoji": str} or {} on failure."""
    try:
        resp = requests.get(URL, timeout=8)
        resp.raise_for_status()
        cur = resp.json()["current"]
        code = int(cur.get("weather_code", 0))
        desc, emoji = CODES.get(code, ("", "🌡️"))
        result = {"temp": round(float(cur["temperature_2m"])),
                  "desc": desc, "emoji": emoji}
        info("weather", f"Weather: {result['temp']}°C {desc}")
        return result
    except Exception as exc:
        error("weather", f"Open-Meteo request failed: {exc}")
        return {}
