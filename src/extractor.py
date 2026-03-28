"""
extractor.py
Reads Constructor MCAP files and extracts lap telemetry into structured JSON.
Handles the custom StateEstimation ROS2 message type.
"""

import json
import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np


STATE_TOPIC = "/constructor0/state_estimation"


def _safe_get(msg, *keys, default=0.0):
    """Safely traverse a ROS2 message object by attribute chain."""
    obj = msg
    for key in keys:
        try:
            obj = getattr(obj, key)
        except AttributeError:
            try:
                obj = obj[key]
            except (KeyError, TypeError, IndexError):
                return default
    if obj is None:
        return default
    return float(obj)


def _safe_get_list(msg, key, length=4, default=0.0):
    """Extract a repeated/array field from a ROS2 message."""
    try:
        val = getattr(msg, key)
        return [float(v) for v in val[:length]]
    except (AttributeError, TypeError):
        return [default] * length


def inspect_message_schema(mcap_path: str, topic: str = STATE_TOPIC, max_msgs: int = 1):
    """
    Print the schema of the first message on a topic.
    Run this first if extraction fails, to discover actual field names.
    """
    from mcap_ros2.reader import read_ros2_messages

    print(f"\n--- Schema inspection: {Path(mcap_path).name} ---")
    count = 0
    for item in read_ros2_messages(mcap_path, topics=[topic]):
        schema = item.schema
        channel = item.channel
        ros_msg = item.ros_msg
        print(f"Message type: {schema.name}")
        print(f"Fields: {[f for f in dir(ros_msg) if not f.startswith('_')]}")
        # Try to print values of top-level fields
        for field in dir(ros_msg):
            if field.startswith("_"):
                continue
            try:
                val = getattr(ros_msg, field)
                if not callable(val):
                    print(f"  {field}: {val}")
            except Exception:
                pass
        count += 1
        if count >= max_msgs:
            break
    print("--- End schema inspection ---\n")


def extract_lap(mcap_path: str, lap_label: str = "lap") -> dict:
    """
    Extract all StateEstimation messages from an MCAP file.
    Returns a structured dict with time-series telemetry arrays.

    The MCAP files are already segmented (one session per file), so we
    extract everything and treat it as one continuous run. For fast_laps
    which contains two laps, we use distance-based lap splitting.
    """
    from mcap_ros2.reader import read_ros2_messages

    records = []
    print(f"Reading {Path(mcap_path).name}...")

    for item in read_ros2_messages(mcap_path, topics=[STATE_TOPIC]):
        schema  = item.schema
        channel = item.channel
        ros_msg = item.ros_msg
        ts = item.log_time_ns * 1e-9

        # --- Position ---
        px = _safe_get(ros_msg, "x_m")
        py = _safe_get(ros_msg, "y_m")
        pz = _safe_get(ros_msg, "z_m")

        # --- Velocity ---
        vx = _safe_get(ros_msg, "vx_mps")
        vy = _safe_get(ros_msg, "vy_mps")
        vz = _safe_get(ros_msg, "vz_mps")
        speed_ms = _safe_get(ros_msg, "v_mps")
        speed_kmh = speed_ms * 3.6

        # --- Orientation ---
        yaw = _safe_get(ros_msg, "yaw_rad")

        # --- Driver inputs ---
        throttle = _safe_get(ros_msg, "gas")
        brake = _safe_get(ros_msg, "brake")
        steering = _safe_get(ros_msg, "delta_wheel_rad")
        gear = int(_safe_get(ros_msg, "gear", default=0))
        rpm = _safe_get(ros_msg, "rpm")

        # --- Acceleration (directly available) ---
        ax = _safe_get(ros_msg, "ax_mps2")
        ay = _safe_get(ros_msg, "ay_mps2")

        # --- Wheel data ---
        wheel_speeds = [
            _safe_get(ros_msg, "omega_w_fl"),
            _safe_get(ros_msg, "omega_w_fr"),
            _safe_get(ros_msg, "omega_w_rl"),
            _safe_get(ros_msg, "omega_w_rr"),
        ]
        slip_ratios = [
            _safe_get(ros_msg, "lambda_fl_perc"),
            _safe_get(ros_msg, "lambda_fr_perc"),
            _safe_get(ros_msg, "lambda_rl_perc"),
            _safe_get(ros_msg, "lambda_rr_perc"),
        ]
        slip_angles = [
            _safe_get(ros_msg, "alpha_fl_rad"),
            _safe_get(ros_msg, "alpha_fr_rad"),
            _safe_get(ros_msg, "alpha_rl_rad"),
            _safe_get(ros_msg, "alpha_rr_rad"),
        ]
        brake_pressures = [
            _safe_get(ros_msg, "cba_actual_pressure_fl_pa"),
            _safe_get(ros_msg, "cba_actual_pressure_fr_pa"),
            _safe_get(ros_msg, "cba_actual_pressure_rl_pa"),
            _safe_get(ros_msg, "cba_actual_pressure_rr_pa"),
        ]

        records.append({
            "ts": ts,
            "x": px, "y": py, "z": pz,
            "vx": vx, "vy": vy, "vz": vz,
            "speed_ms": speed_ms,
            "speed_kmh": speed_kmh,
            "yaw": yaw,
            "throttle": throttle,
            "brake": brake,
            "steering": steering,
            "gear": gear,
            "rpm": rpm,
            "ax": ax, "ay": ay,
            "wheel_speeds": wheel_speeds,
            "slip_ratios": slip_ratios,
            "slip_angles": slip_angles,
            "brake_pressures": brake_pressures,
        })

    if not records:
        raise ValueError(
            f"No StateEstimation messages found in {mcap_path}.\n"
            f"Run inspect_message_schema('{mcap_path}') to check topic names."
        )

    print(f"  Extracted {len(records)} messages")

    # Normalize timestamps to start at 0
    t0 = records[0]["ts"]
    for r in records:
        r["ts"] -= t0

    # Post-process: compute cumulative distance and accelerations
    records = _compute_derived_channels(records)

    # Split into individual laps based on cumulative distance
    laps = _split_laps(records)

    print(f"  Detected {len(laps)} lap(s)")
    for i, lap in enumerate(laps):
        dur = lap[-1]["ts"] - lap[0]["ts"]
        dist = lap[-1]["dist_m"]
        print(f"    Lap {i+1}: {dur:.1f}s, {dist:.0f}m")

    result = {
        "source": str(Path(mcap_path).name),
        "label": lap_label,
        "laps": [_records_to_lap_dict(lap, i + 1) for i, lap in enumerate(laps)],
    }

    return result


def _compute_derived_channels(records: list) -> list:
    """Add cumulative distance and acceleration channels."""
    dist = 0.0
    prev = None

    for r in records:
        if prev is not None:
            dt = r["ts"] - prev["ts"]
            if dt > 0:
                # Distance increment from average speed
                avg_speed = (r["speed_ms"] + prev["speed_ms"]) / 2
                dist += avg_speed * dt

                # Accelerations from velocity derivatives
                r["ax"] = (r["vx"] - prev["vx"]) / dt  # longitudinal
                r["ay"] = (r["vy"] - prev["vy"]) / dt  # lateral
            else:
                r["ax"] = prev.get("ax", 0.0)
                r["ay"] = prev.get("ay", 0.0)
        r["dist_m"] = dist
        prev = r

    return records


def _split_laps(records: list) -> list[list]:
    """
    Split records into individual laps using distance-based detection.
    Estimates lap length from total distance and known lap count heuristics.
    For files that are already single laps, returns a list with one entry.
    """
    if not records:
        return []

    total_dist = records[-1]["dist_m"]
    total_time = records[-1]["ts"]

    # Yas Marina circuit length is approximately 5.28 km
    # If total distance is roughly 1 lap, don't split
    YAS_MARINA_LAP_M = 5280

    estimated_laps = round(total_dist / YAS_MARINA_LAP_M)
    estimated_laps = max(1, estimated_laps)

    if estimated_laps == 1:
        return [records]

    # Split by distance segments
    lap_dist = total_dist / estimated_laps
    laps = []
    current_lap = []
    lap_start_dist = 0.0

    for r in records:
        current_lap.append(r)
        if r["dist_m"] - lap_start_dist >= lap_dist * 0.95 and len(current_lap) > 10:
            # Check we're near the expected lap distance
            laps.append(current_lap)
            current_lap = []
            lap_start_dist = r["dist_m"]

    if current_lap:
        if laps:
            laps[-1].extend(current_lap)
        else:
            laps.append(current_lap)

    return laps


def _records_to_lap_dict(records: list, lap_number: int) -> dict:
    """Convert a list of record dicts to a clean lap dictionary with arrays."""
    if not records:
        return {}

    # Normalize lap timestamps and distance
    t0 = records[0]["ts"]
    d0 = records[0]["dist_m"]

    def arr(key):
        return [r[key] for r in records]

    def arr_nested(key, idx):
        return [r[key][idx] if len(r[key]) > idx else 0.0 for r in records]

    lap_time = records[-1]["ts"] - records[0]["ts"]
    lap_dist = records[-1]["dist_m"] - records[0]["dist_m"]

    return {
        "lap_number": lap_number,
        "lap_time_s": round(lap_time, 3),
        "lap_dist_m": round(lap_dist, 1),
        "n_samples": len(records),
        "channels": {
            # Time base
            "time_s": [round(r["ts"] - t0, 4) for r in records],
            "dist_m": [round(r["dist_m"] - d0, 2) for r in records],
            # Position
            "x": arr("x"),
            "y": arr("y"),
            # Speed
            "speed_kmh": [round(v, 2) for v in arr("speed_kmh")],
            "speed_ms": [round(v, 4) for v in arr("speed_ms")],
            # Inputs
            "throttle": [round(v, 4) for v in arr("throttle")],
            "brake": [round(v, 4) for v in arr("brake")],
            "steering": [round(v, 4) for v in arr("steering")],
            "gear": arr("gear"),
            "rpm": [round(v, 1) for v in arr("rpm")],
            # Acceleration
            "ax": [round(v, 4) for v in arr("ax")],
            "ay": [round(v, 4) for v in arr("ay")],
            # Per-wheel
            "wheel_speed_fl": [round(v, 4) for v in arr_nested("wheel_speeds", 0)],
            "wheel_speed_fr": [round(v, 4) for v in arr_nested("wheel_speeds", 1)],
            "wheel_speed_rl": [round(v, 4) for v in arr_nested("wheel_speeds", 2)],
            "wheel_speed_rr": [round(v, 4) for v in arr_nested("wheel_speeds", 3)],
            "slip_ratio_fl": [round(v, 4) for v in arr_nested("slip_ratios", 0)],
            "slip_ratio_fr": [round(v, 4) for v in arr_nested("slip_ratios", 1)],
            "slip_ratio_rl": [round(v, 4) for v in arr_nested("slip_ratios", 2)],
            "slip_ratio_rr": [round(v, 4) for v in arr_nested("slip_ratios", 3)],
            "brake_pressure_fl": [round(v, 2) for v in arr_nested("brake_pressures", 0)],
            "brake_pressure_fr": [round(v, 2) for v in arr_nested("brake_pressures", 1)],
        },
    }


def save_lap_json(lap_data: dict, output_path: str):
    """Save extracted lap data to JSON."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(lap_data, f, indent=2)
    print(f"  Saved → {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extractor.py <path/to/file.mcap> [--inspect]")
        sys.exit(1)

    path = sys.argv[1]

    if "--inspect" in sys.argv:
        inspect_message_schema(path)
    else:
        data = extract_lap(path, lap_label=Path(path).stem)
        out = f"output/{Path(path).stem}.json"
        save_lap_json(data, out)
        print(f"Done. {len(data['laps'])} lap(s) extracted.")
