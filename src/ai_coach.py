"""
ai_coach.py — AI Race Engineer · Live Coaching Engine
Run alongside lala.py (the recorder) on the same Windows PC.

- Reads Assetto Corsa shared memory directly (no polling, no network overhead)
- Applies a priority-ordered rule tree using speed, throttle, brake,
  gear, G-forces, trajectory, and track position
- Pushes every coaching message to localhost:9000/coaching-message by default
  (visible in the local UI for validation)
- Change COACHING_PUSH_URL to your Lovable endpoint when ready

Usage (PowerShell):
    python ai_coach.py

    # To push to Lovable instead of local:
    $env:COACHING_PUSH_URL="https://your-project.lovable.app/api/coaching-message"
    python ai_coach.py
"""

import ctypes, mmap, time, sys, os, json, math, collections
from pathlib import Path
from configparser import ConfigParser

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests


# ── Config ────────────────────────────────────────────────────────────────────

LIVE_PUSH_URL   = os.getenv("LIVE_PUSH_URL",   "http://localhost:9000/coaching-message").strip()
LIVE_PUSH_TOKEN = os.getenv("LIVE_PUSH_TOKEN", "").strip()
LIVE_PUSH_HZ         = float(os.getenv("LIVE_PUSH_HZ",   "2"))   # evaluations per second

TRACK_ROOT  = Path(r"M:\SteamLibrary\steamapps\common\assettocorsa\content\tracks\acu_yasmarina\north")
MAP_INI     = TRACK_ROOT / "data" / "map.ini"

BUFFER_SIZE = 150   # ~3 seconds at 50 Hz


# ── AC Shared Memory Structs ──────────────────────────────────────────────────

class SPageFilePhysics(ctypes.Structure):
    _fields_ = [
        ("packetId",            ctypes.c_int),
        ("gas",                 ctypes.c_float),
        ("brake",               ctypes.c_float),
        ("fuel",                ctypes.c_float),
        ("gear",                ctypes.c_int),
        ("rpms",                ctypes.c_int),
        ("steerAngle",          ctypes.c_float),
        ("speedKmh",            ctypes.c_float),
        ("velocity",            ctypes.c_float * 3),
        ("accG",                ctypes.c_float * 3),
        ("wheelSlip",           ctypes.c_float * 4),
        ("wheelLoad",           ctypes.c_float * 4),
        ("wheelsPressure",      ctypes.c_float * 4),
        ("wheelAngularSpeed",   ctypes.c_float * 4),
        ("tyreWear",            ctypes.c_float * 4),
        ("tyreDirtyLevel",      ctypes.c_float * 4),
        ("tyreCoreTemperature", ctypes.c_float * 4),
        ("camberRAD",           ctypes.c_float * 4),
        ("suspensionTravel",    ctypes.c_float * 4),
        ("drs",                 ctypes.c_float),
        ("tc",                  ctypes.c_float),
        ("heading",             ctypes.c_float),
        ("pitch",               ctypes.c_float),
        ("roll",                ctypes.c_float),
        ("cgHeight",            ctypes.c_float),
        ("carDamage",           ctypes.c_float * 5),
        ("numberOfTyresOut",    ctypes.c_int),
        ("pitLimiterOn",        ctypes.c_int),
        ("abs",                 ctypes.c_float),
        ("kersCharge",          ctypes.c_float),
        ("kersInput",           ctypes.c_float),
        ("autoShifterOn",       ctypes.c_int),
        ("rideHeight",          ctypes.c_float * 2),
        ("turboBoost",          ctypes.c_float),
        ("ballast",             ctypes.c_float),
        ("airDensity",          ctypes.c_float),
        ("airTemp",             ctypes.c_float),
        ("roadTemp",            ctypes.c_float),
        ("localAngularVel",     ctypes.c_float * 3),
        ("finalFF",             ctypes.c_float),
        ("performanceMeter",    ctypes.c_float),
        ("engineBrake",         ctypes.c_int),
        ("ersRecoveryLevel",    ctypes.c_int),
        ("ersPowerLevel",       ctypes.c_int),
        ("ersHeatCharging",     ctypes.c_int),
        ("ersCurrentKJ",        ctypes.c_float),
        ("drsAvailable",        ctypes.c_int),
        ("drsEnabled",          ctypes.c_int),
        ("brakeTemp",           ctypes.c_float * 4),
        ("clutch",              ctypes.c_float),
        ("tyreTempI",           ctypes.c_float * 4),
        ("tyreTempM",           ctypes.c_float * 4),
        ("tyreTempO",           ctypes.c_float * 4),
        ("isAIControlled",      ctypes.c_int),
        ("tyreContactPoint",    ctypes.c_float * 12),
        ("tyreContactNormal",   ctypes.c_float * 12),
        ("tyreContactHeading",  ctypes.c_float * 12),
        ("brakeBias",           ctypes.c_float),
        ("localVelocity",       ctypes.c_float * 3),
    ]


class SPageFileGraphic(ctypes.Structure):
    _fields_ = [
        ("packetId",                ctypes.c_int),
        ("status",                  ctypes.c_int),
        ("session",                 ctypes.c_int),
        ("currentTime",             ctypes.c_wchar * 15),
        ("lastTime",                ctypes.c_wchar * 15),
        ("bestTime",                ctypes.c_wchar * 15),
        ("split",                   ctypes.c_wchar * 15),
        ("completedLaps",           ctypes.c_int),
        ("position",                ctypes.c_int),
        ("iCurrentTime",            ctypes.c_int),
        ("iLastTime",               ctypes.c_int),
        ("iBestTime",               ctypes.c_int),
        ("sessionTimeLeft",         ctypes.c_float),
        ("distanceTraveled",        ctypes.c_float),
        ("isInPit",                 ctypes.c_int),
        ("currentSectorIndex",      ctypes.c_int),
        ("lastSectorTime",          ctypes.c_int),
        ("numberOfLaps",            ctypes.c_int),
        ("tyreCompound",            ctypes.c_wchar * 33),
        ("replayTimeMultiplier",    ctypes.c_float),
        ("normalizedCarPosition",   ctypes.c_float),
        ("carCoordinates",          ctypes.c_float * 3),
        ("penaltyTime",             ctypes.c_float),
        ("flag",                    ctypes.c_int),
        ("idealLineOn",             ctypes.c_int),
        ("isInPitLane",             ctypes.c_int),
        ("surfaceGrip",             ctypes.c_float),
        ("mandatoryPitDone",        ctypes.c_int),
        ("windSpeed",               ctypes.c_float),
        ("windDirection",           ctypes.c_float),
        ("isSetupMenuVisible",      ctypes.c_int),
        ("mainDisplayIndex",        ctypes.c_int),
        ("secondaryDisplayIndex",   ctypes.c_int),
        ("tc",                      ctypes.c_int),
        ("tcCut",                   ctypes.c_int),
        ("engineMap",               ctypes.c_int),
        ("abs",                     ctypes.c_int),
        ("fuelXLap",                ctypes.c_float),
        ("rainLights",              ctypes.c_int),
        ("flashingLights",          ctypes.c_int),
        ("lightsStage",             ctypes.c_int),
        ("exhaustTemperature",      ctypes.c_float),
        ("wiperLV",                 ctypes.c_int),
        ("driverStintTotalTimeLeft",ctypes.c_float),
        ("driverStintTimeLeft",     ctypes.c_float),
        ("rainTyres",               ctypes.c_int),
    ]


# ── Yas Marina North — corner definitions ─────────────────────────────────────
# (name, apex_x, apex_z, line, brake_dist_m, apex_kmh, exit_kmh)
YAS_MARINA_CORNERS = [
    ("T1 Hairpin",    -148.0,  -362.0,  "inside",  180,  65,  110),
    ("T2",             212.0,  -305.0,  "outside",  80, 130,  175),
    ("T3",             310.0,  -240.0,  "inside",   60, 115,  155),
    ("T4 Chicane-L",   370.0,   -95.0,  "outside",  50, 100,  130),
    ("T5 Chicane-R",   355.0,   -55.0,  "inside",   30, 100,  130),
    ("T6",             210.0,    85.0,  "outside",  90, 120,  160),
    ("T7",              50.0,   140.0,  "inside",   70, 110,  150),
    ("T8",            -110.0,   200.0,  "outside", 120,  80,  130),
    ("T9 Hairpin",    -290.0,   250.0,  "inside",  160,  60,  105),
    ("T10",           -370.0,   120.0,  "outside",  80, 100,  140),
    ("T11",           -390.0,   -40.0,  "inside",   60, 115,  155),
    ("T12",           -310.0,  -150.0,  "outside",  70, 120,  160),
    ("T13 Final",     -180.0,  -280.0,  "inside",  100,  90,  140),
]

CORNERS = [
    {
        "name":       c[0],
        "apex_x":     c[1],
        "apex_z":     c[2],
        "line":       c[3],
        "brake_dist": c[4],
        "apex_kmh":   c[5],
        "exit_kmh":   c[6],
    }
    for c in YAS_MARINA_CORNERS
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_map_ini(path: Path):
    if not path.exists():
        return {}
    parser = ConfigParser()
    parser.read(path, encoding="utf-8")
    p = parser["PARAMETERS"]
    return {
        "scale_factor": float(p.get("SCALE_FACTOR", "1")),
        "x_offset":     float(p.get("X_OFFSET",     "0")),
        "z_offset":     float(p.get("Z_OFFSET",     "0")),
    }


def dist2d(x1, z1, x2, z2):
    return math.sqrt((x1 - x2) ** 2 + (z1 - z2) ** 2)


def compute_curvature(buf):
    pts = [(s["x"], s["z"]) for s in buf if s["x"] is not None]
    if len(pts) < 5:
        return 0.0, "straight"
    n  = len(pts)
    p1 = pts[max(0, n - 10)]
    p2 = pts[max(0, n - 5)]
    p3 = pts[-1]
    ax, ay = p2[0] - p1[0], p2[1] - p1[1]
    bx, by = p3[0] - p2[0], p3[1] - p2[1]
    cross  = ax * by - ay * bx
    d1     = math.hypot(ax, ay)
    d2     = math.hypot(bx, by)
    d3     = dist2d(p1[0], p1[1], p3[0], p3[1])
    denom  = d1 * d2 * d3
    if denom < 1e-6:
        return 0.0, "straight"
    curvature = abs(2 * cross) / denom
    direction = "right" if cross > 0.01 else ("left" if cross < -0.01 else "straight")
    return curvature, direction


def speed_trend(buf, n=10):
    if len(buf) < n + 1:
        return 0.0
    return buf[-1]["speed"] - buf[-n]["speed"]


def find_approaching_corner(car_x, car_z, heading_rad):
    hx = math.cos(heading_rad)
    hz = math.sin(heading_rad)
    best      = None
    best_dist = float("inf")
    for c in CORNERS:
        dx = c["apex_x"] - car_x
        dz = c["apex_z"] - car_z
        d  = math.hypot(dx, dz)
        if d > 450:
            continue
        dot = (dx / (d + 1e-6)) * hx + (dz / (d + 1e-6)) * hz
        if dot > 0.3 and d < best_dist:
            best_dist = d
            best      = c
    return best, best_dist


# ── Rule engine ───────────────────────────────────────────────────────────────

def evaluate_coaching(buf, car_x, car_z, heading_rad):
    if not buf:
        return _msg("WAITING FOR DATA", "", "info", "idle")

    cur      = buf[-1]
    speed    = cur["speed"]
    throttle = cur["throttle"]
    brake    = cur["brake"]
    gear     = cur["gear"]
    lat_g    = cur["lat_g"]
    long_g   = cur["long_g"]
    rpms     = cur["rpms"]
    steer    = cur["steer"]

    curvature, turn_dir = compute_curvature(buf)
    spd_trend = speed_trend(buf, n=8)
    corner, dist_to_apex = find_approaching_corner(car_x, car_z, heading_rad)

    # ── P1: Critical overspeed into corner ───────────────────────────────────
    if (corner
            and dist_to_apex < corner["brake_dist"] * 0.6
            and brake < 0.15
            and speed > corner["apex_kmh"] * 1.4):
        excess = speed - corner["apex_kmh"]
        return _msg(
            "BRAKE NOW!",
            f"{corner['name']} — {excess:.0f} km/h over target",
            "danger", "braking"
        )

    # ── P2: Entering brake zone ───────────────────────────────────────────────
    if corner and brake < 0.1 and speed > corner["apex_kmh"] * 1.2:
        if dist_to_apex < corner["brake_dist"] * 1.1:
            return _msg(
                "BRAKE NOW",
                f"{corner['name']} — apex {corner['apex_kmh']} km/h in {dist_to_apex:.0f}m",
                "danger", "braking"
            )
        elif dist_to_apex < corner["brake_dist"] * 1.5:
            return _msg(
                "PREPARE TO BRAKE",
                f"{corner['name']} in {dist_to_apex:.0f}m — apex {corner['apex_kmh']} km/h",
                "warn", "braking"
            )

    # ── P3: Active braking feedback ───────────────────────────────────────────
    if brake > 0.1 and corner:
        if dist_to_apex < 30 and brake > 0.4:
            return _msg(
                "RELEASE BRAKE",
                f"Past {corner['name']} apex — get on throttle",
                "warn", "braking"
            )
        if 30 < dist_to_apex < 80 and 0.2 < brake < 0.6 and speed > corner["apex_kmh"]:
            return _msg(
                "GOOD BRAKING",
                f"Trail brake into {corner['name']}",
                "good", "braking"
            )

    # ── P4: Line advice on approach ───────────────────────────────────────────
    if (corner
            and corner["brake_dist"] * 0.8 < dist_to_apex < corner["brake_dist"] * 2.0):
        if corner["line"] == "inside":
            return _msg(
                "TAKE INSIDE LINE",
                f"{corner['name']} — hug the inside kerb at apex",
                "info", "line"
            )
        elif corner["line"] == "outside":
            return _msg(
                "START WIDE",
                f"{corner['name']} — outside entry, inside apex",
                "info", "line"
            )

    # ── P5: Too slow at apex ──────────────────────────────────────────────────
    if corner and dist_to_apex < 25 and speed < corner["apex_kmh"] * 0.85:
        deficit = corner["apex_kmh"] - speed
        return _msg(
            "MORE SPEED AT APEX",
            f"{corner['name']} — {deficit:.0f} km/h below target",
            "warn", "speed"
        )

    # ── P6: Post-apex throttle ────────────────────────────────────────────────
    if (corner
            and 10 < dist_to_apex < 60
            and brake < 0.05
            and throttle < 0.4
            and speed < corner["exit_kmh"] * 0.9):
        return _msg(
            "FULL THROTTLE",
            f"Exit of {corner['name']} — apply full power",
            "warn", "throttle"
        )

    # ── P7: Understeer ────────────────────────────────────────────────────────
    if abs(steer) > 0.25 and abs(lat_g) < 0.3 * abs(steer) and speed > 60 and brake < 0.1:
        return _msg(
            "UNDERSTEER — LIFT",
            "Reduce throttle, let front grip recover",
            "danger", "line"
        )

    # ── P8: Oversteer ─────────────────────────────────────────────────────────
    if abs(lat_g) > 0.8 and abs(steer) < 0.1 and speed > 80:
        direction_str = "RIGHT" if lat_g > 0 else "LEFT"
        return _msg(
            f"OVERSTEER — STEER {direction_str}",
            "Counter-steer and ease throttle",
            "danger", "line"
        )

    # ── P9: Gear and throttle on straight ────────────────────────────────────
    if curvature < 0.005 and speed > 60:
        if rpms > 7000 and gear < 6 and throttle > 0.7:
            return _msg(
                "SHIFT UP",
                f"G{gear} at {rpms} RPM — more to give",
                "warn", "gear"
            )
        if rpms < 3000 and gear > 2 and throttle > 0.5:
            return _msg(
                "SHIFT DOWN",
                f"G{gear} at {rpms} RPM — engine too low",
                "warn", "gear"
            )
        if throttle < 0.5 and brake < 0.05 and speed > 80:
            return _msg(
                "FULL THROTTLE",
                f"Clear straight — {speed:.0f} km/h, floor it",
                "warn", "throttle"
            )

    # ── P10: Unexplained speed loss in corner ─────────────────────────────────
    if curvature > 0.012 and spd_trend < -8 and brake < 0.05 and throttle < 0.3:
        return _msg(
            "MAINTAIN SPEED",
            f"Losing {abs(spd_trend):.0f} km/h — check line",
            "warn", "speed"
        )

    # ── P11: Good cornering ───────────────────────────────────────────────────
    if (curvature > 0.012
            and abs(lat_g) > 0.5
            and corner
            and speed >= corner["apex_kmh"] * 0.95):
        return _msg(
            "GOOD LINE",
            f"{corner['name']} — on pace",
            "good", "line"
        )

    # ── P12: Committed on straight ────────────────────────────────────────────
    if curvature < 0.003 and throttle > 0.9 and speed > 150:
        return _msg(
            "KEEP IT FLAT",
            f"{speed:.0f} km/h — stay committed",
            "good", "throttle"
        )

    # ── Default ───────────────────────────────────────────────────────────────
    if speed < 10:
        return _msg("WAITING", "Car stationary", "info", "idle")

    return _msg(
        "ON TRACK",
        f"{speed:.0f} km/h · G{gear} · {rpms} RPM",
        "info", "idle"
    )


def _msg(message, detail, severity, category):
    return {
        "message":   message,
        "detail":    detail,
        "severity":  severity,
        "category":  category,
        "timestamp": time.time(),
    }


# ── Push ──────────────────────────────────────────────────────────────────────

last_pushed_msg = ""
push_count      = 0
push_ok_count   = 0


def push_coaching(msg_dict, force=False):
    global last_pushed_msg, push_count, push_ok_count

    if not LIVE_PUSH_URL:
        return

    # Skip only if idle category AND same message text — all real coaching fires every time
    if msg_dict["category"] == "idle" and msg_dict["message"] == last_pushed_msg and not force:
        return

    last_pushed_msg = msg_dict["message"]
    push_count += 1

    headers = {"Content-Type": "application/json"}
    if LIVE_PUSH_TOKEN:
        headers["Authorization"] = f"Bearer {LIVE_PUSH_TOKEN}"
        headers["x-telemetry-token"] = LIVE_PUSH_TOKEN
        headers["x-live-push-token"] = LIVE_PUSH_TOKEN

    try:
        resp = requests.post(
            LIVE_PUSH_URL,
            json=msg_dict,
            headers=headers,
            timeout=1.5,
        )
        if 200 <= resp.status_code < 300:
            push_ok_count += 1
        else:
            print(f"\n  [PUSH FAIL] HTTP {resp.status_code}: {resp.text[:120]}", flush=True)
    except requests.exceptions.ConnectionError:
        print(f"\n  [PUSH ERROR] Cannot reach {LIVE_PUSH_URL}", flush=True)
    except Exception as e:
        print(f"\n  [PUSH ERROR] {type(e).__name__}: {e}", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  AI Race Engineer — Coaching Engine")
    print("=" * 55)

    if sys.platform != "win32":
        print("ERROR: Must run on Windows where Assetto Corsa is installed.")
        sys.exit(1)

    print(f"Push URL : {LIVE_PUSH_URL}")
    if "localhost:9000" in LIVE_PUSH_URL:
        print("  → LOCAL VALIDATION MODE")
        print("  → Open http://localhost:9000 — AI Coach panel at bottom right")
        print("  → Set $env:LIVE_PUSH_URL to switch to cloud\n")
    else:
        print()

    map_params = load_map_ini(MAP_INI)
    if map_params:
        print(f"Map ini  : scale={map_params['scale_factor']}, "
              f"x_off={map_params['x_offset']}, z_off={map_params['z_offset']}")
    else:
        print("WARNING: map.ini not found — corner detection uses raw world coords")

    # ── Connect to AC shared memory ───────────────────────────────────────────
    print("\nConnecting to AC shared memory...", end="", flush=True)
    try:
        phys_mm  = mmap.mmap(-1, ctypes.sizeof(SPageFilePhysics),  "Local\\acpmf_physics")
        graph_mm = mmap.mmap(-1, ctypes.sizeof(SPageFileGraphic), "Local\\acpmf_graphics")
        print(" Connected ✓")
    except Exception as e:
        print(f"\nERROR: {e}")
        print("Make sure Assetto Corsa is running.")
        input("Press ENTER to exit...")
        sys.exit(1)

    def read_p():
        phys_mm.seek(0)
        return SPageFilePhysics.from_buffer_copy(
            phys_mm.read(ctypes.sizeof(SPageFilePhysics)))

    def read_g():
        graph_mm.seek(0)
        return SPageFileGraphic.from_buffer_copy(
            graph_mm.read(ctypes.sizeof(SPageFileGraphic)))

    # ── Wait for live session ─────────────────────────────────────────────────
    print("Waiting for AC session (status=2)...", end="", flush=True)
    while True:
        if read_g().status == 2:
            print(" Ready!")
            break
        time.sleep(0.5)
        print(".", end="", flush=True)

    # ── Startup connectivity test ─────────────────────────────────────────────
    print(f"\nTesting push endpoint {LIVE_PUSH_URL}...", end="", flush=True)
    test_msg = {
        "message":   "COACH ONLINE",
        "detail":    "AI coaching engine connected and ready",
        "severity":  "good",
        "category":  "idle",
        "timestamp": time.time(),
    }
    headers = {"Content-Type": "application/json"}
    if LIVE_PUSH_TOKEN:
        headers["Authorization"] = f"Bearer {LIVE_PUSH_TOKEN}"
        headers["x-telemetry-token"] = LIVE_PUSH_TOKEN
        headers["x-live-push-token"] = LIVE_PUSH_TOKEN
    try:
        resp = requests.post(LIVE_PUSH_URL, json=test_msg, headers=headers, timeout=3.0)
        if 200 <= resp.status_code < 300:
            print(f" OK ✓ (HTTP {resp.status_code})")
            print("  → You should see 'COACH ONLINE' in the AI Coach panel at localhost:9000")
        else:
            print(f"\n  HTTP {resp.status_code} — response: {resp.text[:200]}")
            print("  Make sure you are running the UPDATED lala.py (with do_POST method)")
    except requests.exceptions.ConnectionError:
        print(f"\n  [CONNECTION ERROR] Cannot reach {LIVE_PUSH_URL}")
        print("  → Make sure lala.py is running first (it serves the /coaching-message endpoint)")
        print("  → Continuing anyway — will retry each push...")
    except Exception as e:
        print(f"\n  [ERROR] {e}")

    print(f"\nEval rate : {LIVE_PUSH_HZ} Hz  |  Buffer: {BUFFER_SIZE} samples (~{BUFFER_SIZE/50:.1f}s)")
    print("Press Ctrl+C to stop\n")
    print(f"{'─'*55}")
    print(f"  {'MESSAGE':<28}  {'SPEED':>6}  {'G':>2}  {'THR':>4}  {'BRK':>4}  PUSHES")
    print(f"{'─'*55}")

    # ── Rolling telemetry buffer ──────────────────────────────────────────────
    buf = collections.deque(maxlen=BUFFER_SIZE)

    SAMPLE_INTERVAL   = 0.02
    PUSH_INTERVAL = 1.0 / max(LIVE_PUSH_HZ, 0.5)

    last_sample_t   = 0.0
    last_coaching_t = 0.0
    last_heading    = 0.0
    last_x          = None
    last_z          = None
    eval_count      = 0

    try:
        while True:
            now = time.perf_counter()

            # ── Sample at 50 Hz ───────────────────────────────────────────────
            if now - last_sample_t >= SAMPLE_INTERVAL:
                last_sample_t = now
                try:
                    p = read_p()
                    g = read_g()
                except Exception as e:
                    print(f"\n  [READ ERROR] {e}", flush=True)
                    time.sleep(0.1)
                    continue

                cx = float(g.carCoordinates[0])
                cz = float(g.carCoordinates[2])

                if last_x is not None:
                    dx = cx - last_x
                    dz = cz - last_z
                    if abs(dx) + abs(dz) > 0.01:
                        last_heading = math.atan2(dz, dx)
                last_x, last_z = cx, cz

                buf.append({
                    "speed":    float(p.speedKmh),
                    "throttle": float(p.gas),
                    "brake":    float(p.brake),
                    "gear":     int(p.gear),
                    "rpms":     int(p.rpms),
                    "steer":    float(p.steerAngle),
                    "lat_g":    float(p.accG[0]),
                    "long_g":   float(p.accG[2]),
                    "x":        cx,
                    "z":        cz,
                    "heading":  last_heading,
                    "t":        now,
                })

            # ── Evaluate + push at COACHING_HZ ────────────────────────────────
            if now - last_coaching_t >= PUSH_INTERVAL and len(buf) > 5:
                last_coaching_t = now
                eval_count += 1

                cur = buf[-1]

                try:
                    msg = evaluate_coaching(
                        list(buf),
                        cur["x"],
                        cur["z"],
                        cur["heading"],
                    )
                except Exception as e:
                    import traceback
                    print(f"\n  [EVAL ERROR] {e}", flush=True)
                    traceback.print_exc()
                    continue

                # Console status line
                sev_icon = {
                    "danger": "🔴", "warn": "🟡",
                    "good":   "🟢", "info": "⚪"
                }.get(msg["severity"], "·")

                print(
                    f"\r{sev_icon} {msg['message']:<28}"
                    f"  {cur['speed']:5.1f}km/h"
                    f"  G{cur['gear']}"
                    f"  T{cur['throttle']*100:3.0f}%"
                    f"  B{cur['brake']*100:3.0f}%"
                    f"  {push_ok_count}/{push_count} ok"
                    f"  #{eval_count}   ",
                    end="", flush=True
                )

                push_coaching(msg)

            time.sleep(0.004)

    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        phys_mm.close()
        graph_mm.close()
        print(f"Session summary: {eval_count} evaluations, "
              f"{push_ok_count}/{push_count} pushes succeeded.")
        try:
            input("Press ENTER to exit...")
        except EOFError:
            pass


if __name__ == "__main__":
    main()