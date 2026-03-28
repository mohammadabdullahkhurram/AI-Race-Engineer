"""
analyzer.py
Aligns two laps by track distance and computes channel-by-channel deltas.
Identifies braking zones, corners, and acceleration phases.
Produces a structured analysis ready for the coaching engine.
"""

import json
import math
from pathlib import Path
from typing import Optional
import numpy as np


def auto_detect_sectors(lap: dict, n_sectors: int = 3) -> list:
    """
    Auto-detect sector boundaries by splitting the lap into equal distance thirds.
    Works on any track — no hardcoded distances needed.
    """
    dist = lap["channels"]["dist_m"]
    total = dist[-1]
    sector_len = total / n_sectors
    sectors = []
    for i in range(n_sectors):
        start = i * sector_len
        end   = (i + 1) * sector_len
        sectors.append({
            "id":       i + 1,
            "name":     f"Sector {i + 1}",
            "start_m":  round(start, 1),
            "end_m":    round(end, 1),
        })
    return sectors


def auto_detect_corners(lap: dict, min_gap_m: float = 150.0) -> list:
    """
    Auto-detect corners from any lap by finding local speed minima.
    No hardcoded corner positions — works on any circuit.

    Algorithm:
      1. Smooth the speed trace
      2. Find all local minima below the median speed
      3. Classify each by how slow it is relative to surroundings
      4. Enforce minimum spacing between corners
    """
    import numpy as np
    from scipy.ndimage import uniform_filter1d

    dist  = np.array(lap["channels"]["dist_m"])
    speed = np.array(lap["channels"]["speed_kmh"])

    # Smooth over ~50m window
    pts_per_50m = max(1, int(50 / ((dist[-1] - dist[0]) / len(dist))))
    smoothed = uniform_filter1d(speed, size=pts_per_50m)

    median_speed = float(np.median(smoothed))
    threshold    = median_speed * 0.92  # corners are below 92% of median speed

    # Find local minima
    minima = []
    window = pts_per_50m * 2
    for i in range(window, len(smoothed) - window):
        local_min = np.min(smoothed[i - window:i + window])
        if smoothed[i] == local_min and smoothed[i] < threshold:
            minima.append(i)

    # Enforce minimum gap between corners
    corners = []
    last_dist = -min_gap_m * 2
    corner_num = 1

    for idx in minima:
        d = float(dist[idx])
        if d - last_dist < min_gap_m:
            continue

        apex_speed = float(smoothed[idx])
        # Classify corner type by how slow it is
        pct = apex_speed / median_speed
        if pct < 0.55:
            corner_type = "heavy_brake"
        elif pct < 0.70:
            corner_type = "medium_corner"
        elif pct < 0.82:
            corner_type = "light_corner"
        else:
            corner_type = "fast_corner"

        corners.append({
            "id":       f"T{corner_num}",
            "name":     f"Turn {corner_num}",
            "dist_m":   round(d, 1),
            "type":     corner_type,
        })
        last_dist = d
        corner_num += 1

    print(f"  Auto-detected {len(corners)} corners, {len(set(c['type'] for c in corners))} types")
    return corners


def load_lap(json_path: str, lap_index: int = 0) -> dict:
    """Load a specific lap from an extracted JSON file."""
    with open(json_path) as f:
        data = json.load(f)
    laps = data.get("laps", [])
    if not laps:
        raise ValueError(f"No laps found in {json_path}")
    if lap_index >= len(laps):
        lap_index = -1  # take last lap
    lap = laps[lap_index]
    lap["source_file"] = data.get("source", json_path)
    lap["label"] = data.get("label", Path(json_path).stem)
    return lap


def _interp_channel(dist_ref: list, dist_src: list, values: list) -> list:
    """
    Interpolate a channel from source distance points to reference distance points.
    Both dist_ref and dist_src must be monotonically increasing.
    """
    return list(np.interp(dist_ref, dist_src, values))


def align_laps(ref_lap: dict, comp_lap: dict, resolution_m: float = 5.0) -> dict:
    """
    Align two laps to a common distance grid.
    ref_lap: the reference (fast lap), comp_lap: the lap being analyzed.
    resolution_m: grid spacing in metres.

    Returns a dict with aligned channels and delta channels.
    """
    ref_dist = ref_lap["channels"]["dist_m"]
    comp_dist = comp_lap["channels"]["dist_m"]

    # Common grid: from 0 to min(max_ref, max_comp)
    max_dist = min(ref_dist[-1], comp_dist[-1])
    grid = list(np.arange(0, max_dist, resolution_m))

    def align(lap, channel):
        return _interp_channel(grid, lap["channels"]["dist_m"], lap["channels"][channel])

    channels_to_align = [
        "speed_kmh", "throttle", "brake", "steering",
        "gear", "rpm", "ax", "ay",
        "wheel_speed_fl", "wheel_speed_fr", "wheel_speed_rl", "wheel_speed_rr",
        "slip_ratio_fl", "slip_ratio_fr", "slip_ratio_rl", "slip_ratio_rr",
        "brake_pressure_fl", "brake_pressure_fr",
    ]

    ref_aligned = {}
    comp_aligned = {}

    for ch in channels_to_align:
        if ch in ref_lap["channels"] and ch in comp_lap["channels"]:
            ref_aligned[ch] = align(ref_lap, ch)
            comp_aligned[ch] = align(comp_lap, ch)

    # Time alignment (cumulative time at each distance point)
    ref_time = _interp_channel(grid, ref_lap["channels"]["dist_m"], ref_lap["channels"]["time_s"])
    comp_time = _interp_channel(grid, comp_lap["channels"]["dist_m"], comp_lap["channels"]["time_s"])

    # Delta time: positive = comp is slower than ref
    delta_time = [c - r for r, c in zip(ref_time, comp_time)]

    # Delta speed: positive = comp is faster
    delta_speed = [
        c - r for r, c in zip(ref_aligned["speed_kmh"], comp_aligned["speed_kmh"])
    ]

    return {
        "grid_m": grid,
        "ref_time": ref_time,
        "comp_time": comp_time,
        "delta_time": delta_time,
        "delta_speed": delta_speed,
        "ref": ref_aligned,
        "comp": comp_aligned,
        "ref_lap_time": ref_lap["lap_time_s"],
        "comp_lap_time": comp_lap["lap_time_s"],
        "ref_label": ref_lap.get("label", "reference"),
        "comp_label": comp_lap.get("label", "comparison"),
    }


def compute_sector_analysis(aligned: dict, sectors_def: list = None) -> list:
    """
    Compute per-sector statistics from aligned lap data.
    Works on any track — sectors_def is auto-detected, not hardcoded.
    """
    grid = aligned["grid_m"]
    sectors = []
    if sectors_def is None:
        sectors_def = []

    for sec in sectors_def:
        # Find indices within this sector
        indices = [i for i, d in enumerate(grid) if sec["start_m"] <= d < sec["end_m"]]
        if not indices:
            continue

        def sector_vals(channel_dict, key):
            return [channel_dict[key][i] for i in indices if i < len(channel_dict[key])]

        ref_speeds = sector_vals(aligned["ref"], "speed_kmh")
        comp_speeds = sector_vals(aligned["comp"], "speed_kmh")
        delta_times = [aligned["delta_time"][i] for i in indices]

        # Time lost/gained in this sector
        sector_time_delta = delta_times[-1] - delta_times[0] if delta_times else 0.0

        # Min speed (corner speed indicator)
        ref_min_speed = min(ref_speeds) if ref_speeds else 0
        comp_min_speed = min(comp_speeds) if comp_speeds else 0

        # Max speed (straight line speed)
        ref_max_speed = max(ref_speeds) if ref_speeds else 0
        comp_max_speed = max(comp_speeds) if comp_speeds else 0

        # Average brake
        ref_brakes = sector_vals(aligned["ref"], "brake")
        comp_brakes = sector_vals(aligned["comp"], "brake")
        ref_avg_brake = sum(ref_brakes) / len(ref_brakes) if ref_brakes else 0
        comp_avg_brake = sum(comp_brakes) / len(comp_brakes) if comp_brakes else 0

        # Throttle application
        ref_throttles = sector_vals(aligned["ref"], "throttle")
        comp_throttles = sector_vals(aligned["comp"], "throttle")
        ref_avg_throttle = sum(ref_throttles) / len(ref_throttles) if ref_throttles else 0
        comp_avg_throttle = sum(comp_throttles) / len(comp_throttles) if comp_throttles else 0

        sectors.append({
            "sector_id": sec["id"],
            "sector_name": sec["name"],
            "start_m": sec["start_m"],
            "end_m": sec["end_m"],
            "time_delta_s": round(sector_time_delta, 3),
            "ref_min_speed_kmh": round(ref_min_speed, 1),
            "comp_min_speed_kmh": round(comp_min_speed, 1),
            "speed_delta_at_min_kmh": round(comp_min_speed - ref_min_speed, 1),
            "ref_max_speed_kmh": round(ref_max_speed, 1),
            "comp_max_speed_kmh": round(comp_max_speed, 1),
            "ref_avg_brake": round(ref_avg_brake, 3),
            "comp_avg_brake": round(comp_avg_brake, 3),
            "ref_avg_throttle": round(ref_avg_throttle, 3),
            "comp_avg_throttle": round(comp_avg_throttle, 3),
        })

    return sectors


def compute_corner_analysis(aligned: dict, corners_def: list = None) -> list:
    """
    Analyse each detected corner for braking point, entry speed,
    apex speed, throttle pick-up, and time delta at that corner.
    Works on any track — corners_def is auto-detected, not hardcoded.
    """
    grid = aligned["grid_m"]
    corners = []
    if corners_def is None:
        corners_def = []

    for corner in corners_def:
        apex_dist = corner["dist_m"]
        # Window: 200m before to 300m after apex
        window_start = max(0, apex_dist - 200)
        window_end = min(grid[-1], apex_dist + 300)

        indices = [
            i for i, d in enumerate(grid)
            if window_start <= d <= window_end
        ]
        if len(indices) < 5:
            continue

        def window_vals(ch_dict, key):
            return [(grid[i], ch_dict[key][i]) for i in indices if i < len(ch_dict[key])]

        ref_speeds = window_vals(aligned["ref"], "speed_kmh")
        comp_speeds = window_vals(aligned["comp"], "speed_kmh")
        ref_brakes = window_vals(aligned["ref"], "brake")
        comp_brakes = window_vals(aligned["comp"], "brake")
        ref_throttles = window_vals(aligned["ref"], "throttle")
        comp_throttles = window_vals(aligned["comp"], "throttle")

        # Apex speed = minimum speed in window
        ref_apex_speed = min(s for _, s in ref_speeds)
        comp_apex_speed = min(s for _, s in comp_speeds)

        # Braking point: first point where brake > 0.1
        ref_brake_start = next((d for d, b in ref_brakes if b > 0.1), None)
        comp_brake_start = next((d for d, b in comp_brakes if b > 0.1), None)

        # Throttle pick-up: first point after apex where throttle > 0.3
        post_apex_ref = [(d, t) for d, t in ref_throttles if d > apex_dist]
        post_apex_comp = [(d, t) for d, t in comp_throttles if d > apex_dist]
        ref_throttle_pickup = next((d for d, t in post_apex_ref if t > 0.3), None)
        comp_throttle_pickup = next((d for d, t in post_apex_comp if t > 0.3), None)

        # Time delta at apex
        apex_idx = min(indices, key=lambda i: abs(grid[i] - apex_dist))
        time_delta_at_apex = aligned["delta_time"][apex_idx] if apex_idx < len(aligned["delta_time"]) else 0.0

        # Entry speed (100m before apex)
        entry_idx_ref = min(
            [i for i in indices if grid[i] <= apex_dist - 50] or [indices[0]],
            key=lambda i: abs(grid[i] - (apex_dist - 100))
        )
        ref_entry_speed = aligned["ref"]["speed_kmh"][entry_idx_ref]
        comp_entry_speed = aligned["comp"]["speed_kmh"][entry_idx_ref]

        corners.append({
            "corner_id": corner["id"],
            "corner_name": corner["name"],
            "corner_type": corner["type"],
            "dist_m": apex_dist,
            "time_delta_s": round(time_delta_at_apex, 3),
            "ref_apex_speed_kmh": round(ref_apex_speed, 1),
            "comp_apex_speed_kmh": round(comp_apex_speed, 1),
            "apex_speed_delta_kmh": round(comp_apex_speed - ref_apex_speed, 1),
            "ref_entry_speed_kmh": round(ref_entry_speed, 1),
            "comp_entry_speed_kmh": round(comp_entry_speed, 1),
            "entry_speed_delta_kmh": round(comp_entry_speed - ref_entry_speed, 1),
            "ref_brake_point_m": round(ref_brake_start, 1) if ref_brake_start else None,
            "comp_brake_point_m": round(comp_brake_start, 1) if comp_brake_start else None,
            "brake_point_delta_m": (
                round(comp_brake_start - ref_brake_start, 1)
                if ref_brake_start and comp_brake_start else None
            ),
            "ref_throttle_pickup_m": round(ref_throttle_pickup, 1) if ref_throttle_pickup else None,
            "comp_throttle_pickup_m": round(comp_throttle_pickup, 1) if comp_throttle_pickup else None,
            "throttle_pickup_delta_m": (
                round(comp_throttle_pickup - ref_throttle_pickup, 1)
                if ref_throttle_pickup and comp_throttle_pickup else None
            ),
        })

    return corners


def find_worst_sections(aligned: dict, n: int = 5) -> list:
    """
    Find the N sections of track where the most time is being lost.
    Returns list of (start_m, end_m, time_lost_s) sorted by worst first.
    """
    grid = aligned["grid_m"]
    delta = aligned["delta_time"]

    # Smooth the delta signal
    window = 10
    smoothed = []
    for i in range(len(delta)):
        start = max(0, i - window)
        end = min(len(delta), i + window + 1)
        smoothed.append(sum(delta[start:end]) / (end - start))

    # Find peaks of time loss (positive delta = slower)
    # Look for local maxima in smoothed delta
    peaks = []
    for i in range(5, len(smoothed) - 5):
        if smoothed[i] > smoothed[i - 1] and smoothed[i] > smoothed[i + 1]:
            if smoothed[i] > 0.05:  # at least 50ms loss
                peaks.append((grid[i], smoothed[i]))

    # Sort by magnitude
    peaks.sort(key=lambda x: x[1], reverse=True)
    return peaks[:n]


def run_analysis(ref_json: str, comp_json: str,
                 ref_lap_idx: int = 0, comp_lap_idx: int = 0) -> dict:
    """
    Full analysis pipeline: load → align → auto-detect corners/sectors → analyze.
    Fully track-agnostic — works on any circuit without hardcoded values.
    """
    ref_lap = load_lap(ref_json, ref_lap_idx)
    comp_lap = load_lap(comp_json, comp_lap_idx)

    print(f"Reference: {ref_lap['label']} - Lap {ref_lap['lap_number']} ({ref_lap['lap_time_s']:.1f}s)")
    print(f"Compared:  {comp_lap['label']} - Lap {comp_lap['lap_number']} ({comp_lap['lap_time_s']:.1f}s)")

    # Auto-detect track structure from reference lap GPS/speed data
    sectors_def = auto_detect_sectors(ref_lap, n_sectors=3)
    corners_def = auto_detect_corners(ref_lap)

    aligned = align_laps(ref_lap, comp_lap)
    sectors = compute_sector_analysis(aligned, sectors_def)
    corners = compute_corner_analysis(aligned, corners_def)
    worst = find_worst_sections(aligned)

    total_time_delta = comp_lap["lap_time_s"] - ref_lap["lap_time_s"]

    analysis = {
        "ref_lap_time_s": ref_lap["lap_time_s"],
        "comp_lap_time_s": comp_lap["lap_time_s"],
        "total_time_delta_s": round(total_time_delta, 3),
        "ref_label": aligned["ref_label"],
        "comp_label": aligned["comp_label"],
        "sectors": sectors,
        "corners": corners,
        "worst_sections": [{"dist_m": d, "time_lost_s": round(t, 3)} for d, t in worst],
        "lap_dist_m": ref_lap["lap_dist_m"],
    }

    return analysis


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python analyzer.py <ref.json> <comp.json>")
        sys.exit(1)

    result = run_analysis(sys.argv[1], sys.argv[2])
    print(json.dumps(result, indent=2))
