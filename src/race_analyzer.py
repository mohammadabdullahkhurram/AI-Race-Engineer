"""
race_analyzer.py
Analyzes the wheel-to-wheel MCAP file.
Extracts multiple race laps, detects race events (traffic/defensive moves),
compares race pace vs reference, and builds a race report.
"""

import json
import math
import numpy as np
from pathlib import Path

YAS_MARINA_LAP_M   = 5280
EVENT_BRAKE_THRESH = 0.6    # brake > 60% with no preceding braking = anomaly
EVENT_SPEED_DROP   = 15     # km/h sudden speed drop = event
EVENT_COOLDOWN_M   = 200    # metres between events


def extract_race_laps(mcap_path: str) -> list:
    """
    Extract all laps from the wheel-to-wheel MCAP.
    Returns list of lap dicts with channels.
    """
    from mcap_ros2.reader import read_ros2_messages

    STATE_TOPIC = "/constructor0/state_estimation"
    records = []

    print(f"Reading {Path(mcap_path).name}...")
    for item in read_ros2_messages(mcap_path, topics=[STATE_TOPIC]):
        ros_msg = item.ros_msg
        ts = item.log_time_ns * 1e-9

        def g(attr, default=0.0):
            try:
                v = getattr(ros_msg, attr)
                return float(v) if v is not None else default
            except AttributeError:
                return default

        records.append({
            "ts":         ts,
            "x":          g("x_m"),
            "y":          g("y_m"),
            "speed_kmh":  g("v_mps") * 3.6,
            "throttle":   g("gas"),
            "brake":      g("brake"),
            "steering":   g("delta_wheel_rad"),
            "gear":       int(g("gear")),
            "rpm":        g("rpm"),
            "ax":         g("ax_mps2"),
            "ay":         g("ay_mps2"),
        })

    if not records:
        raise ValueError("No state estimation messages found.")

    print(f"  Extracted {len(records)} messages")

    # Normalize timestamps
    t0 = records[0]["ts"]
    for r in records:
        r["ts"] -= t0

    # Compute cumulative distance
    dist = 0.0
    prev = None
    for r in records:
        if prev:
            dt = r["ts"] - prev["ts"]
            avg_spd = (r["speed_kmh"] + prev["speed_kmh"]) / 2 / 3.6
            dist += max(0, avg_spd * dt)
        r["dist_m"] = dist
        prev = r

    # Split into laps by distance
    total_dist = records[-1]["dist_m"]
    n_laps = max(1, round(total_dist / YAS_MARINA_LAP_M))
    print(f"  Total distance: {total_dist:.0f}m → {n_laps} lap(s)")

    laps = []
    lap_target = YAS_MARINA_LAP_M
    current = []
    lap_start_dist = 0.0

    for r in records:
        current.append(r)
        if (r["dist_m"] - lap_start_dist) >= lap_target * 0.92 and len(current) > 50:
            laps.append(current)
            lap_start_dist = r["dist_m"]
            current = []

    if current and len(current) > 50:
        if laps:
            laps[-1].extend(current)
        else:
            laps.append(current)

    lap_dicts = []
    for i, lap in enumerate(laps):
        t0l = lap[0]["ts"]
        d0l = lap[0]["dist_m"]
        lap_time = lap[-1]["ts"] - lap[0]["ts"]
        lap_dist = lap[-1]["dist_m"] - lap[0]["dist_m"]

        def arr(key):
            return [r[key] for r in lap]

        lap_dicts.append({
            "lap_number":  i + 1,
            "lap_time_s":  round(lap_time, 3),
            "lap_dist_m":  round(lap_dist, 1),
            "n_samples":   len(lap),
            "channels": {
                "time_s":    [round(r["ts"] - t0l, 4) for r in lap],
                "dist_m":    [round(r["dist_m"] - d0l, 2) for r in lap],
                "x":         arr("x"),
                "y":         arr("y"),
                "speed_kmh": arr("speed_kmh"),
                "throttle":  arr("throttle"),
                "brake":     arr("brake"),
                "steering":  arr("steering"),
                "gear":      arr("gear"),
                "rpm":       arr("rpm"),
                "ax":        arr("ax"),
                "ay":        arr("ay"),
            }
        })
        print(f"    Lap {i+1}: {lap_time:.1f}s  {lap_dist:.0f}m")

    return lap_dicts


def detect_race_events(lap: dict) -> list:
    """
    Detect anomalous events in a race lap that indicate traffic or defensive moves.
    Returns list of event dicts.
    """
    dist   = lap["channels"]["dist_m"]
    speed  = lap["channels"]["speed_kmh"]
    brake  = lap["channels"]["brake"]
    throttle = lap["channels"]["throttle"]
    n = len(dist)

    events = []
    last_event_dist = -9999

    for i in range(5, n - 5):
        d = dist[i]
        if d - last_event_dist < EVENT_COOLDOWN_M:
            continue

        # Event type 1: Sudden heavy braking not near a normal corner
        # Look for brake spike that's out of character with surroundings
        avg_brake_before = sum(brake[max(0,i-20):i]) / 20
        if brake[i] > EVENT_BRAKE_THRESH and avg_brake_before < 0.1:
            # Speed drop around this point
            speed_before = max(speed[max(0,i-10):i]) if i > 10 else speed[i]
            speed_after  = min(speed[i:min(n,i+15)])
            drop = speed_before - speed_after
            if drop > 10:
                events.append({
                    "type":        "defensive_brake",
                    "label":       "Defensive Braking",
                    "dist_m":      round(d, 1),
                    "time_s":      round(lap["channels"]["time_s"][i], 2),
                    "speed_kmh":   round(speed[i], 1),
                    "speed_drop_kmh": round(drop, 1),
                    "brake_pct":   round(brake[i] * 100, 1),
                    "description": f"Unplanned brake application at {d:.0f}m — "
                                   f"{drop:.0f} km/h speed scrubbed. "
                                   f"Possible traffic ahead or defensive move.",
                })
                last_event_dist = d

        # Event type 2: Sudden speed drop without braking (lift-off)
        elif i > 15:
            speed_window_before = speed[max(0,i-15):i]
            if speed_window_before:
                avg_spd_before = sum(speed_window_before) / len(speed_window_before)
                if (avg_spd_before - speed[i] > EVENT_SPEED_DROP
                        and brake[i] < 0.1
                        and throttle[i] < 0.2
                        and avg_spd_before > 100):
                    events.append({
                        "type":        "lift_off",
                        "label":       "Lift-off Event",
                        "dist_m":      round(d, 1),
                        "time_s":      round(lap["channels"]["time_s"][i], 2),
                        "speed_kmh":   round(speed[i], 1),
                        "speed_drop_kmh": round(avg_spd_before - speed[i], 1),
                        "brake_pct":   0.0,
                        "description": f"Throttle lift at {d:.0f}m without braking — "
                                       f"{avg_spd_before - speed[i]:.0f} km/h speed reduction. "
                                       f"Likely traffic management or gap control.",
                    })
                    last_event_dist = d

    return events


def compare_race_to_reference(race_laps: list, ref_lap_path: str) -> dict:
    """
    Compare each race lap against the reference fast lap.
    """
    with open(ref_lap_path) as f:
        ref_data = json.load(f)
    ref_lap = ref_data["laps"][0]

    comparisons = []
    for lap in race_laps:
        delta = lap["lap_time_s"] - ref_lap["lap_time_s"]

        # Align speeds by distance and compute avg delta
        ref_dist  = ref_lap["channels"]["dist_m"]
        ref_speed = ref_lap["channels"]["speed_kmh"]
        lap_dist  = lap["channels"]["dist_m"]
        lap_speed = lap["channels"]["speed_kmh"]

        max_d = min(ref_dist[-1], lap_dist[-1])
        grid  = list(np.arange(0, max_d, 10))

        ref_spd_aligned  = np.interp(grid, ref_dist, ref_speed)
        lap_spd_aligned  = np.interp(grid, lap_dist, lap_speed)
        speed_delta_avg  = float(np.mean(lap_spd_aligned - ref_spd_aligned))
        speed_delta_min  = float(np.min(lap_spd_aligned - ref_spd_aligned))

        comparisons.append({
            "lap_number":       lap["lap_number"],
            "lap_time_s":       lap["lap_time_s"],
            "ref_time_s":       ref_lap["lap_time_s"],
            "time_delta_s":     round(delta, 3),
            "avg_speed_delta":  round(speed_delta_avg, 2),
            "min_speed_delta":  round(speed_delta_min, 2),
            "avg_throttle":     round(sum(lap["channels"]["throttle"]) / len(lap["channels"]["throttle"]), 3),
            "avg_brake":        round(sum(lap["channels"]["brake"]) / len(lap["channels"]["brake"]), 3),
        })

    return comparisons


def build_race_summary(race_laps: list, comparisons: list, all_events: list) -> dict:
    """Build overall race summary stats."""
    lap_times = [l["lap_time_s"] for l in race_laps]
    best_race_lap = min(range(len(lap_times)), key=lambda i: lap_times[i])

    return {
        "total_laps":       len(race_laps),
        "total_time_s":     round(sum(lap_times), 3),
        "best_lap_number":  best_race_lap + 1,
        "best_lap_time_s":  round(lap_times[best_race_lap], 3),
        "worst_lap_time_s": round(max(lap_times), 3),
        "lap_time_range_s": round(max(lap_times) - min(lap_times), 3),
        "total_events":     len(all_events),
        "defensive_brakes": sum(1 for e in all_events if e["type"] == "defensive_brake"),
        "lift_offs":        sum(1 for e in all_events if e["type"] == "lift_off"),
        "pace_vs_ref_s":    round(comparisons[best_race_lap]["time_delta_s"], 3) if comparisons else 0,
    }


def run_race_analysis(mcap_path: str, ref_lap_path: str, output_dir: str = "output") -> dict:
    """Full race analysis pipeline."""
    from pathlib import Path
    Path(output_dir).mkdir(exist_ok=True)

    # Extract laps
    race_laps = extract_race_laps(mcap_path)

    # Save raw lap data
    race_json_path = f"{output_dir}/race_laps.json"
    with open(race_json_path, "w") as f:
        json.dump({"source": Path(mcap_path).name, "laps": race_laps}, f, indent=2)
    print(f"  Race laps saved → {race_json_path}")

    # Detect events per lap
    all_events = []
    lap_events = []
    for lap in race_laps:
        events = detect_race_events(lap)
        lap_events.append(events)
        all_events.extend(events)
        print(f"  Lap {lap['lap_number']}: {len(events)} race event(s) detected")

    # Compare to reference
    comparisons = compare_race_to_reference(race_laps, ref_lap_path)

    # Summary
    summary = build_race_summary(race_laps, comparisons, all_events)

    result = {
        "summary":     summary,
        "laps":        comparisons,
        "lap_events":  lap_events,
        "all_events":  all_events,
    }

    race_analysis_path = f"{output_dir}/race_analysis.json"
    with open(race_analysis_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  Race analysis saved → {race_analysis_path}")

    return result


if __name__ == "__main__":
    import sys
    mcap = sys.argv[1] if len(sys.argv) > 1 else "data/hackathon_wheel_to_wheel.mcap"
    ref  = sys.argv[2] if len(sys.argv) > 2 else "output/fast_laps.json"
    result = run_race_analysis(mcap, ref)
    print(json.dumps(result["summary"], indent=2))
