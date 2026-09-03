import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


def env_float(name, default):
    return float(os.getenv(name, str(default)))


ADSBLOL_BASE = os.getenv("ADSBLOL_BASE_URL", "https://api.adsb.lol").rstrip("/")
POLL = env_float("POLL_SECONDS", 10)
SEARCH_RADIUS = env_float("SEARCH_RADIUS_NM", 50)

WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]

HOME_LAT = float(os.environ["HOME_LAT"])
HOME_LON = float(os.environ["HOME_LON"])
HOME_MAX_ALT = env_float("HOME_MAX_ALTITUDE_FT", 15000)
HOME_EARLY_RADIUS = env_float("HOME_EARLY_RADIUS_NM", 2.0)
HOME_EARLY_ETA = env_float("HOME_EARLY_ETA_MIN", 8)
HOME_IMMEDIATE_RADIUS = env_float("HOME_IMMEDIATE_RADIUS_NM", 1.0)
HOME_IMMEDIATE_ETA = env_float("HOME_IMMEDIATE_ETA_MIN", 3)
HOME_MAX_BEARING_ERROR = env_float("HOME_MAX_BEARING_ERROR_DEG", 25)
HOME_MIN_CONVERGING_SAMPLES = int(os.getenv("HOME_MIN_CONVERGING_SAMPLES", "3"))

STADIUM_LAT = env_float("STADIUM_LAT", 40.25753)
STADIUM_LON = env_float("STADIUM_LON", -111.65456)
STADIUM_MAX_ALT = env_float("STADIUM_MAX_ALTITUDE_FT", 15000)
STADIUM_EARLY_RADIUS = env_float("STADIUM_EARLY_RADIUS_NM", 5.0)
STADIUM_EARLY_ETA = env_float("STADIUM_EARLY_ETA_MIN", 10)
STADIUM_IMMEDIATE_RADIUS = env_float("STADIUM_IMMEDIATE_RADIUS_NM", 2.0)
STADIUM_IMMEDIATE_ETA = env_float("STADIUM_IMMEDIATE_ETA_MIN", 5)
STADIUM_MAX_BEARING_ERROR = env_float("STADIUM_MAX_BEARING_ERROR_DEG", 30)
STADIUM_MIN_CONVERGING_SAMPLES = int(os.getenv("STADIUM_MIN_CONVERGING_SAMPLES", "3"))

WATCHED_TAIL = os.getenv("WATCHED_TAIL", "N130TP").strip().upper()
PVU_LAT = env_float("PVU_LAT", 40.2192)
PVU_LON = env_float("PVU_LON", -111.7234)
PVU_ELEV_FT = env_float("PVU_ELEV_FT", 4497)
PVU_RADIUS = env_float("PVU_START_RADIUS_NM", 2.0)
PVU_LOW_AGL = env_float("PVU_LOW_ALT_AGL_FT", 800)

EARLY_COOLDOWN = env_float("EARLY_COOLDOWN_MINUTES", 45) * 60
IMMEDIATE_COOLDOWN = env_float("IMMEDIATE_COOLDOWN_MINUTES", 30) * 60
PVU_COOLDOWN = env_float("PVU_COOLDOWN_MINUTES", 30) * 60
SEND_STARTUP_TEST = os.getenv("SEND_STARTUP_TEST", "true").lower() in ("1", "true", "yes", "y")

STATE_FILE = Path("/data/state.json")
session = requests.Session()
session.headers.update({"User-Agent": "home-flight-watcher/3.0"})


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


state = load_state()

# Short-term in-memory position history for convergence checks.
track_history = {}


def save_state():
    STATE_FILE.write_text(json.dumps(state, indent=2))


def nm_distance(lat1, lon1, lat2, lon2):
    r = 3440.065
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_to(lat1, lon1, lat2, lon2):
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)

    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)

    return (math.degrees(math.atan2(x, y)) + 360) % 360


def heading_error(track, desired_bearing):
    return abs((track - desired_bearing + 180) % 360 - 180)


def xy_nm(lat, lon, ref_lat, ref_lon):
    y = (lat - ref_lat) * 60.0
    x = (lon - ref_lon) * 60.0 * math.cos(math.radians(ref_lat))
    return x, y


def projected_cpa(ac, target_lat, target_lon, max_minutes):
    lat, lon = ac.get("lat"), ac.get("lon")
    gs, track = ac.get("gs"), ac.get("track")
    if not all(isinstance(v, (int, float)) for v in (lat, lon, gs, track)):
        return None
    if gs < 15:
        return None

    x, y = xy_nm(lat, lon, target_lat, target_lon)
    speed = gs / 60.0
    theta = math.radians(track)
    vx = speed * math.sin(theta)
    vy = speed * math.cos(theta)
    vv = vx * vx + vy * vy
    if vv <= 0:
        return None

    t = -(x * vx + y * vy) / vv
    if t < 0 or t > max_minutes:
        return None

    miss = math.hypot(x + vx * t, y + vy * t)
    return t, miss


def altitude_ft(ac):
    alt = ac.get("alt_baro")
    if alt == "ground":
        return 0
    if isinstance(alt, (int, float)):
        return float(alt)
    alt = ac.get("alt_geom")
    return float(alt) if isinstance(alt, (int, float)) else None


def ident(ac):
    reg = str(ac.get("r") or "").strip().upper()
    call = str(ac.get("flight") or "").strip().upper()
    return reg, call


def aircraft_key(ac):
    reg, call = ident(ac)
    return str(ac.get("hex") or reg or call or "unknown").lower()


def is_military(ac):
    try:
        return bool(int(ac.get("dbFlags", 0)) & 1)
    except (ValueError, TypeError):
        return False


def field_value(v, suffix=""):
    return f"{v}{suffix}" if v not in (None, "") else "unknown"


def send_discord(title, description, ac, emoji="✈️"):
    reg, call = ident(ac)
    alt = altitude_ft(ac)
    fields = [
        {"name": "Callsign", "value": call or "unknown", "inline": True},
        {"name": "Registration", "value": reg or "unknown", "inline": True},
        {"name": "Type", "value": str(ac.get("t") or ac.get("desc") or "unknown"), "inline": True},
        {"name": "Altitude", "value": "ground" if alt == 0 else (f"{alt:,.0f} ft" if alt is not None else "unknown"), "inline": True},
        {"name": "Groundspeed", "value": field_value(ac.get("gs"), " kt"), "inline": True},
        {"name": "Track", "value": field_value(ac.get("track"), "°"), "inline": True},
        {"name": "Source", "value": str(ac.get("type") or "unknown"), "inline": True},
        {"name": "ICAO Hex", "value": str(ac.get("hex") or "unknown"), "inline": True},
    ]
    payload = {
        "username": "Flight Watcher",
        "embeds": [{
            "title": f"{emoji} {title}",
            "description": description,
            "fields": fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    r = session.post(WEBHOOK, json=payload, timeout=10)
    r.raise_for_status()


def cooldown_ok(key, seconds):
    return time.time() - float(state.get(key, 0)) >= seconds


def mark(key):
    state[key] = time.time()
    save_state()


def update_track_history(ac, target_name, target_lat, target_lon):
    lat = ac.get("lat")
    lon = ac.get("lon")

    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return []

    key = f"{target_name}:{aircraft_key(ac)}"
    distance = nm_distance(lat, lon, target_lat, target_lon)

    history = track_history.setdefault(key, [])
    history.append({
        "time": time.time(),
        "distance": distance,
    })

    # Keep only recent samples.
    history[:] = history[-10:]
    return history


def is_converging(history, required_samples=3):
    if len(history) < required_samples:
        return False

    recent = history[-required_samples:]
    distances = [sample["distance"] for sample in recent]

    for previous, current in zip(distances, distances[1:]):
        if current >= previous:
            return False

    # Prevent tiny position jitter from counting as convergence.
    total_decrease = distances[0] - distances[-1]
    return total_decrease >= 0.15


def path_is_toward_target(
    ac,
    target_name,
    target_lat,
    target_lon,
    max_bearing_error,
    required_samples,
):
    lat = ac.get("lat")
    lon = ac.get("lon")
    track = ac.get("track")

    if not all(isinstance(v, (int, float)) for v in (lat, lon, track)):
        return False, None, None

    desired_bearing = bearing_to(lat, lon, target_lat, target_lon)
    error = heading_error(track, desired_bearing)

    history = update_track_history(
        ac,
        target_name,
        target_lat,
        target_lon,
    )

    converging = is_converging(history, required_samples)
    heading_good = error <= max_bearing_error

    return heading_good and converging, desired_bearing, error


def fetch_aircraft():
    url = f"{ADSBLOL_BASE}/v2/point/{HOME_LAT}/{HOME_LON}/{SEARCH_RADIUS}"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return r.json().get("ac", [])


def evaluate_home(ac):
    lat = ac.get("lat")
    lon = ac.get("lon")

    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return

    alt = altitude_ft(ac)
    if alt is not None and alt > HOME_MAX_ALT:
        return

    key = aircraft_key(ac)
    current = nm_distance(lat, lon, HOME_LAT, HOME_LON)

    toward_home, desired_bearing, bearing_error = path_is_toward_target(
        ac,
        "home",
        HOME_LAT,
        HOME_LON,
        HOME_MAX_BEARING_ERROR,
        HOME_MIN_CONVERGING_SAMPLES,
    )

    # If already physically inside the immediate radius, alert regardless of path.
    if current <= HOME_IMMEDIATE_RADIUS:
        state_key = f"home_immediate:{key}"
        if cooldown_ok(state_key, IMMEDIATE_COOLDOWN):
            send_discord(
                "GO OUTSIDE — aircraft near home",
                (
                    f"Aircraft is currently **{current:.1f} NM** from home.\n\n"
                    f"It is already inside the immediate alert radius."
                ),
                ac,
                "🚨",
            )
            mark(state_key)
        return

    # Predictive alerts require proof that the aircraft is genuinely converging.
    if not toward_home:
        return

    cpa = projected_cpa(ac, HOME_LAT, HOME_LON, HOME_EARLY_ETA)
    if not cpa:
        return

    eta, miss = cpa

    if eta <= HOME_IMMEDIATE_ETA and miss <= HOME_IMMEDIATE_RADIUS:
        state_key = f"home_immediate:{key}"
        if cooldown_ok(state_key, IMMEDIATE_COOLDOWN):
            send_discord(
                "GO OUTSIDE — aircraft heading toward home",
                (
                    f"Current distance **{current:.1f} NM**.\n"
                    f"Projected closest approach **{miss:.1f} NM**.\n"
                    f"ETA **~{eta:.0f} min**.\n\n"
                    f"Track is within **{bearing_error:.0f}°** of the direct bearing toward home "
                    f"and the aircraft has been consistently getting closer."
                ),
                ac,
                "🚨",
            )
            mark(state_key)
        return

    if miss <= HOME_EARLY_RADIUS:
        state_key = f"home_early:{key}"
        if cooldown_ok(state_key, EARLY_COOLDOWN):
            send_discord(
                "Aircraft heading toward home",
                (
                    f"Current distance **{current:.1f} NM**.\n"
                    f"Projected closest approach **{miss:.1f} NM** in **~{eta:.0f} min**.\n\n"
                    f"Track is **{bearing_error:.0f}°** from the direct bearing toward home and "
                    f"the aircraft has been getting closer across "
                    f"**{HOME_MIN_CONVERGING_SAMPLES} observations**."
                ),
                ac,
                "✈️",
            )
            mark(state_key)


def evaluate_stadium_military(ac):
    if not is_military(ac):
        return

    lat = ac.get("lat")
    lon = ac.get("lon")

    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return

    alt = altitude_ft(ac)
    if alt is not None and alt > STADIUM_MAX_ALT:
        return

    key = aircraft_key(ac)
    current = nm_distance(lat, lon, STADIUM_LAT, STADIUM_LON)

    toward_stadium, desired_bearing, bearing_error = path_is_toward_target(
        ac,
        "stadium",
        STADIUM_LAT,
        STADIUM_LON,
        STADIUM_MAX_BEARING_ERROR,
        STADIUM_MIN_CONVERGING_SAMPLES,
    )

    if current <= STADIUM_IMMEDIATE_RADIUS:
        state_key = f"stadium_immediate:{key}"
        if cooldown_ok(state_key, IMMEDIATE_COOLDOWN):
            send_discord(
                "MILITARY AIRCRAFT NEAR LAVELL EDWARDS STADIUM",
                (
                    f"Military aircraft is currently **{current:.1f} NM** from LaVell Edwards Stadium."
                ),
                ac,
                "🇺🇸",
            )
            mark(state_key)
        return

    if not toward_stadium:
        return

    cpa = projected_cpa(ac, STADIUM_LAT, STADIUM_LON, STADIUM_EARLY_ETA)
    if not cpa:
        return

    eta, miss = cpa

    if eta <= STADIUM_IMMEDIATE_ETA and miss <= STADIUM_IMMEDIATE_RADIUS:
        state_key = f"stadium_immediate:{key}"
        if cooldown_ok(state_key, IMMEDIATE_COOLDOWN):
            send_discord(
                "MILITARY FLYOVER IMMINENT — LaVell Edwards Stadium",
                (
                    f"Current stadium distance **{current:.1f} NM**.\n"
                    f"Projected closest approach **{miss:.1f} NM**.\n"
                    f"ETA **~{eta:.0f} min**.\n\n"
                    f"Track is within **{bearing_error:.0f}°** of the stadium bearing and "
                    f"the aircraft has been consistently getting closer."
                ),
                ac,
                "🇺🇸",
            )
            mark(state_key)
        return

    if miss <= STADIUM_EARLY_RADIUS:
        state_key = f"stadium_early:{key}"
        if cooldown_ok(state_key, EARLY_COOLDOWN):
            send_discord(
                "Military aircraft heading toward LaVell Edwards Stadium",
                (
                    f"Current stadium distance **{current:.1f} NM**.\n"
                    f"Projected closest approach **{miss:.1f} NM** in **~{eta:.0f} min**.\n\n"
                    f"Track is **{bearing_error:.0f}°** from the direct stadium bearing and "
                    f"the aircraft has been getting closer across "
                    f"**{STADIUM_MIN_CONVERGING_SAMPLES} observations**."
                ),
                ac,
                "🇺🇸",
            )
            mark(state_key)


def evaluate_watched_tail(ac):
    reg, call = ident(ac)
    if WATCHED_TAIL not in (reg, call):
        return

    lat = ac.get("lat")
    lon = ac.get("lon")

    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return

    # Special PVU activation alert.
    pvu_dist = nm_distance(lat, lon, PVU_LAT, PVU_LON)
    alt = altitude_ft(ac)

    low_near_pvu = (
        pvu_dist <= PVU_RADIUS
        and (
            alt == 0
            or (alt is not None and alt <= PVU_ELEV_FT + PVU_LOW_AGL)
        )
    )

    if low_near_pvu:
        state_key = f"pvu:{WATCHED_TAIL}"
        if cooldown_ok(state_key, PVU_COOLDOWN):
            send_discord(
                f"{WATCHED_TAIL} active at PVU",
                f"Detected **{pvu_dist:.1f} NM** from PVU at low altitude/on ground.",
                ac,
                "🚁",
            )
            mark(state_key)

    # Dedicated watched-tail home alert using the same improved convergence logic.
    current = nm_distance(lat, lon, HOME_LAT, HOME_LON)

    toward_home, desired_bearing, bearing_error = path_is_toward_target(
        ac,
        f"watched_{WATCHED_TAIL}",
        HOME_LAT,
        HOME_LON,
        HOME_MAX_BEARING_ERROR,
        HOME_MIN_CONVERGING_SAMPLES,
    )

    if not toward_home:
        return

    cpa = projected_cpa(ac, HOME_LAT, HOME_LON, HOME_EARLY_ETA)
    if not cpa:
        return

    eta, miss = cpa

    if miss <= HOME_EARLY_RADIUS:
        state_key = f"watched_home:{WATCHED_TAIL}"
        if cooldown_ok(state_key, EARLY_COOLDOWN):
            send_discord(
                f"{WATCHED_TAIL} heading toward home",
                (
                    f"Current distance **{current:.1f} NM**.\n"
                    f"Projected closest approach **{miss:.1f} NM** in **~{eta:.0f} min**.\n\n"
                    f"Track is **{bearing_error:.0f}°** from the direct bearing toward home and "
                    f"the helicopter has been consistently getting closer."
                ),
                ac,
                "🚁",
            )
            mark(state_key)


def prune_track_history():
    cutoff = time.time() - 300
    to_delete = []

    for key, history in track_history.items():
        history[:] = [sample for sample in history if sample["time"] >= cutoff]
        if not history:
            to_delete.append(key)

    for key in to_delete:
        del track_history[key]


def main():
    print(
        f"Flight Watcher v3 starting. "
        f"Regional radius={SEARCH_RADIUS} NM, watched tail={WATCHED_TAIL}"
    )
    print(f"Home={HOME_LAT},{HOME_LON}; Stadium={STADIUM_LAT},{STADIUM_LON}")
    print(
        f"Home convergence: bearing error <= {HOME_MAX_BEARING_ERROR}°, "
        f"samples={HOME_MIN_CONVERGING_SAMPLES}"
    )

    if SEND_STARTUP_TEST:
        send_discord(
            "Flight Watcher online",
            (
                f"Watching all low-altitude traffic near home, military traffic near "
                f"LaVell Edwards Stadium, and **{WATCHED_TAIL}**.\n\n"
                f"Predictive alerts now require multi-sample convergence."
            ),
            {},
            "✅",
        )

    while True:
        started = time.time()

        try:
            aircraft = fetch_aircraft()
            print(f"Received {len(aircraft)} aircraft")

            for ac in aircraft:
                try:
                    evaluate_home(ac)
                    evaluate_stadium_military(ac)
                    evaluate_watched_tail(ac)
                except Exception as e:
                    print(
                        f"Aircraft evaluation error {aircraft_key(ac)}: "
                        f"{type(e).__name__}: {e}"
                    )

            prune_track_history()

        except Exception as e:
            print(f"Feed error: {type(e).__name__}: {e}")

        elapsed = time.time() - started
        time.sleep(max(1, POLL - elapsed))


if __name__ == "__main__":
    main()
