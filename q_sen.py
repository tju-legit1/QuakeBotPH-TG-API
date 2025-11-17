#!/usr/bin/env python3
"""
Quake Sentinel (SEA-only)
- Sends Telegram alerts to multiple recipients (map + formatted message)
- Persists seen quake IDs to avoid duplicates
- Daily report at 08:00 PHT
- SEA bounding box: lat -15..25, lon 90..145
"""

import os
import time
import math
import json
import requests
from datetime import datetime, timezone, timedelta

# === CONFIG (set sensitive values via environment variables) ===
# Export BOT_TOKEN and RECIPIENTS before running:
#   export BOT_TOKEN="123:ABC..."
#   export RECIPIENTS="5747516199,123456789,987654321"
BOT_TOKEN = os.getenv("BOT_TOKEN")  # required
RECIPIENTS = os.getenv("CHAT_ID")# comma-separated chat_ids (strings)

if not BOT_TOKEN:
    raise SystemExit("ERROR: BOT_TOKEN environment variable not set. Abort.")

RECIPIENTS = [r.strip() for r in RECIPIENTS.split(",") if r.strip()]
if not RECIPIENTS:
    raise SystemExit("ERROR: RECIPIENTS env var empty — add at least one chat ID. Abort.")

USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
MIN_MAGNITUDE = float(os.getenv("MIN_MAGNITUDE", "1.0"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))  # seconds
SEEN_FILE = os.getenv("SEEN_FILE", "seen_ids.json")
LOG_FILE = os.getenv("LOG_FILE", "quake_log.txt")
PRIORITY_CITY = "Tacloban"

# City list for impact estimation (lat, lon)
CITIES = {
    "Manila": (14.6, 121.0),
    "Baguio": (16.4, 120.6),
    "Cebu": (10.3, 123.9),
    "Davao": (7.1, 125.6),
    "Iloilo": (10.7, 122.6),
    "Legazpi": (13.1, 123.7),
    "Tacloban": (11.2, 125.0),
    "Samar": (12.0, 125.0),
}

# SEA bounding box
def is_in_sea_region(lat, lon):
    return (4.5 <= lat <= 21.5) and (116.0 <= lon <= 127.5)


# === Utilities ===
def distance_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2) + math.cos(phi1) * math.cos(phi2) * (math.sin(dlambda / 2) ** 2)
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def estimate_intensity(mag, dist_km):
    if dist_km < 30:
        level = mag + 1.5
    elif dist_km < 100:
        level = mag
    elif dist_km < 300:
        level = mag - 1.5
    else:
        level = mag - 2.5

    if level >= 7:
        return "VII (Severe)"
    elif level >= 6:
        return "VI (Very Strong)"
    elif level >= 5:
        return "V (Strong)"
    elif level >= 4:
        return "IV (Moderate)"
    elif level >= 3:
        return "III (Weak)"
    elif level >= 2:
        return "II (Slight)"
    else:
        return "I (Barely Felt)"


# === Persistence for seen IDs ===
def load_seen():
    try:
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen_set):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(list(seen_set), f)
    except Exception as e:
        print("Warning: could not save seen IDs:", e)


# === Telegram send ===
def send_to_recipients(text, lat=None, lon=None):
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    for chat_id in RECIPIENTS:
        try:
            # Send map photo first if coordinates provided
            if lat is not None and lon is not None:
                map_url = f"https://maps.googleapis.com/maps/api/staticmap?center={lat},{lon}&zoom=6&size=600x400&markers=color:red|{lat},{lon}"
                # If you have a Google API key, append &key=YOUR_KEY to the map_url
                requests.post(f"{base}/sendPhoto", data={"chat_id": chat_id, "photo": map_url}, timeout=10)
            # Then send message (Markdown)
            requests.post(f"{base}/sendMessage", data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            print(f"Failed to send to {chat_id}:", e)


# === Logging helper ===
def log_event(line):
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {line}\n")


# === Alert formatting and impact analysis ===
def build_alert_message(quake):
    props = quake.get("properties", {})
    geom = quake.get("geometry", {})
    coords = geom.get("coordinates", [0, 0])
    lon, lat = coords[0], coords[1]
    mag = props.get("mag", 0)
    place = props.get("place", "Unknown")
    time_ms = props.get("time", 0)

    utc_time = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)
    ph_time = utc_time + timedelta(hours=8)
    time_str_utc = utc_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    time_str_ph = ph_time.strftime("%Y-%m-%d %I:%M %p (PHT)")

    # Impact analysis (only include relevant cities + always Tacloban)
    impact_data = []
    for city, (clat, clon) in CITIES.items():
        dist = distance_km(lat, lon, clat, clon)
        intensity = estimate_intensity(mag, dist)
        if city == PRIORITY_CITY or dist <= 400:
            impact_data.append((city, dist, intensity))

    # If none included (rare) still include Tacloban
    if not impact_data:
        clat, clon = CITIES[PRIORITY_CITY]
        d = distance_km(lat, lon, clat, clon)
        impact_data.append((PRIORITY_CITY, d, estimate_intensity(mag, d)))

    impact_data.sort(key=lambda x: x[1])
    epicenter_city, epicenter_dist, epicenter_int = impact_data[0]

    impact_lines = []
    for city, dist, intensity in impact_data:
        marker = "⚠️" if city == epicenter_city else "🏙️"
        priority_mark = "⭐" if city == PRIORITY_CITY else ""
        impact_lines.append(f"{marker} *{city}*{priority_mark}: ~{int(dist)} km → {intensity}")

    impact_text = "\n".join(impact_lines)

    msg = (
        f"🌏 *EARTHQUAKE ALERT*\n\n"
        f"📍 *Location:* {place}\n"
        f"💥 *Magnitude:* {mag}\n"
        f"🕒 *Time:* {time_str_utc} / {time_str_ph}\n"
        f"📌 *Epicenter Zone:* {epicenter_city} ({int(epicenter_dist)} km, {epicenter_int})\n\n"
        f"🌐 *Estimated Intensities*\n{impact_text}\n\n"
        f"🗺 https://www.google.com/maps?q={lat},{lon}\n\n"
        "⚠️ *QUICK REMINDER:*\n"
        "• Stay calm, move to safety\n"
        "• Avoid glass/walls/heavy items\n"
        "• Turn off gas/electricity if needed\n"
        "• Expect aftershocks — monitor updates\n"
    )

    return msg, lat, lon


# === Daily report function (can also be called manually) ===
def send_daily_report():
    try:
        r = requests.get(USGS_URL, timeout=10)
        data = r.json()
        features = data.get("features", [])
        if not features:
            send_to_recipients("📅 *Daily Report:* No earthquakes recorded in the past 24 hours.")
            return

        latest = features[0]
        props = latest.get("properties", {})
        coords = latest.get("geometry", {}).get("coordinates", [0, 0])
        mag = props.get("mag", 0)
        place = props.get("place", "Unknown")
        time_ms = props.get("time", 0)
        utc_time = datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)
        ph_time = utc_time + timedelta(hours=8)
        time_str_utc = utc_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        time_str_ph = ph_time.strftime("%Y-%m-%d %I:%M %p (PHT)")

        lat, lon = coords[1], coords[0]

        # Build impact lines using the same logic
        impact_data = []
        for city, (clat, clon) in CITIES.items():
            dist = distance_km(lat, lon, clat, clon)
            intensity = estimate_intensity(mag, dist)
            if city == PRIORITY_CITY or dist <= 400:
                impact_data.append((city, dist, intensity))
        if not impact_data:
            clat, clon = CITIES[PRIORITY_CITY]
            d = distance_km(lat, lon, clat, clon)
            impact_data.append((PRIORITY_CITY, d, estimate_intensity(mag, d)))
        impact_data.sort(key=lambda x: x[1])

        epicenter_city, epicenter_dist, epicenter_int = impact_data[0]
        impact_lines = []
        for city, dist, intensity in impact_data:
            marker = "⚠️" if city == epicenter_city else "🏙️"
            priority_mark = "⭐" if city == PRIORITY_CITY else ""
            impact_lines.append(f"{marker} *{city}*{priority_mark}: ~{int(dist)} km → {intensity}")
        impact_text = "\n".join(impact_lines)

        report = (
            f"📊 *Daily Quake Report*\n\n"
            f"🕒 {time_str_utc} / {time_str_ph}\n"
            f"📍 {place}\n"
            f"💥 Magnitude: {mag}\n\n"
            f"⚠️ *Epicenter Zone:* {epicenter_city} ({int(epicenter_dist)} km, {epicenter_int})\n\n"
            f"🌐 *Estimated Intensities*\n{impact_text}\n\n"
            "✅ System Operational, Chief."
        )
        send_to_recipients(report, lat, lon)
        log_event(f"Daily report sent for quake {place} mag {mag}")
    except Exception as e:
        print("Daily report error:", e)


# === Main monitoring loop ===
def monitor_loop():
    seen = load_seen()
    last_daily_date = None
    print("⚡ Quake Sentinel (SEA) online. Monitoring...")

    while True:
        try:
            r = requests.get(USGS_URL, timeout=15)
            data = r.json()
            features = data.get("features", [])

            # iterate newest first
            for quake in features:
                quake_id = quake.get("id")
                props = quake.get("properties", {})
                geom = quake.get("geometry", {})
                coords = geom.get("coordinates", [0, 0])
                lon, lat = coords[0], coords[1]
                mag = props.get("mag", 0)

                # skip if below magnitude threshold or outside SEA
                if mag is None or mag < MIN_MAGNITUDE:
                    continue
                if not is_in_sea_region(lat, lon):
                    continue

                if quake_id and quake_id not in seen:
                    # new quake of interest
                    msg, mlat, mlon = build_alert_message(quake)
                    send_to_recipients(msg, mlat, mlon)
                    seen.add(quake_id)
                    save_seen(seen)
                    log_event(f"Alert sent: id={quake_id} mag={mag} loc={props.get('place')}")
                    # small delay between notifications to recipients to avoid bursts
                    time.sleep(1)

            # Daily report at 08:00 PHT (UTC+8)
            now_utc = datetime.now(timezone.utc)
            now_ph = now_utc + timedelta(hours=8)
            if now_ph.hour == 8 and (last_daily_date != now_ph.date()):
                send_daily_report()
                last_daily_date = now_ph.date()

        except Exception as e:
            print("Monitor loop error:", e)
            log_event(f"Monitor error: {e}")

        time.sleep(CHECK_INTERVAL)


# === Optional manual test function ===
def send_test_alert():
    # sends a simulated test message centered near Manila
    test_msg = "🧪 *Test Quake Alert* — This is a system check."
    send_to_recipients(test_msg, 14.5995, 120.9842)
    log_event("Manual test alert sent")


# === ENTRY ===
if __name__ == "__main__":
    # quick note: ensure BOT_TOKEN and RECIPIENTS set in environment before running
    # run monitor loop
    monitor_loop()