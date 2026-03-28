"""
normalize.py
Converts SimHub CSV telemetry export into the same JSON format
used by the AI Race Engineer pipeline.

After running this, feed the output JSON into run.py as the comparison lap:
    python3 normalize.py sim_lap.csv
    # Then edit run.py COMP_MCAP → COMP_JSON and point it at output/sim_lap.json

Usage:
    python3 normalize.py                          # auto-finds latest CSV in ~/Documents/SimHub
    python3 normalize.py path/to/lap.csv          # specific file
    python3 normalize.py path/to/lap.csv --run    # normalize then immediately run full pipeline

SimHub Setup (do this once):
    1. Open SimHub → click "Additional Plugins" → enable "Raw Log"
    2. Go to Raw Log settings, enable these fields:
       SpeedKmh, Throttle, Brake, Steering, Gear, Rpms,
       GlobalAccelerationG, LateralG, CurrentLap, LapTimeCurrent,
       TrackPositionPercent, CarCoordX, CarCoordZ
    3. Set export format: CSV, delimiter: semicolon (;)
    4. SimHub auto-saves a new file each lap to Documents/SimHub/Logs/
"""

import csv
import json
import math
import os
import sys
import glob
import argparse
from pathlib import Path


# ── SimHub field name mappings ────────────────────────────────────────────────
# Maps SimHub CSV column names → our internal field names
# SimHub may use different names depending on version — we try all variants

FIELD_MAP = {
    "speed_kmh": [
        "SpeedKmh", "SpeedKMH", "Speed", "speed_kmh", "GPS_Speed",
        "DataCorePlugin.GameRawData.NewData.SpeedKmh",
    ],
    "throttle": [
        "Throttle", "throttle", "ThrottlePercent", "Gas",
        "DataCorePlugin.GameRawData.NewData.Throttle",
    ],
    "brake": [
        "Brake", "brake", "BrakePercent",
        "DataCorePlugin.GameRawData.NewData.Brake",
    ],
    "steering": [
        "Steering", "steering", "SteeringAngle", "SteeringWheelAngle",
        "DataCorePlugin.GameRawData.NewData.Steering",
    ],
    "gear": [
        "Gear", "gear", "CurrentGear",
        "DataCorePlugin.GameRawData.NewData.Gear",
    ],
    "rpm": [
        "Rpms", "RPM", "rpm", "EngineRPM",
        "DataCorePlugin.GameRawData.NewData.Rpms",
    ],
    "ax": [
        "GlobalAccelerationG", "AccelerationX", "LongitudinalG", "ax",
        "DataCorePlugin.GameRawData.NewData.GlobalAccelerationG",
    ],
    "ay": [
        "LateralG", "LateralAccelerationG", "AccelerationY", "ay",
        "DataCorePlugin.GameRawData.NewData.LateralG",
    ],
    "time_s": [
        "LapTimeCurrent", "CurrentLapTime", "laptime", "TimeSeconds",
        "DataCorePlugin.GameRawData.NewData.LapTimeCurrent",
    ],
    "track_pct": [
        "TrackPositionPercent", "TrackPosition", "LapProgress",
        "DataCorePlugin.GameRawData.NewData.TrackPositionPercent",
    ],
    "x": [
        "CarCoordX", "WorldPositionX", "PosX", "GPS_Lat",
        "DataCorePlugin.GameRawData.NewData.CarCoordX",
    ],
    "y": [
        "CarCoordZ", "WorldPositionZ", "PosZ", "GPS_Lon",
        "DataCorePlugin.GameRawData.NewData.CarCoordZ",
    ],
}


def find_latest_simhub_csv() -> str | None:
    """Search common SimHub log locations for the most recent CSV."""
    search_paths = [
        os.path.expanduser("~/Documents/SimHub/Logs/*.csv"),
        os.path.expanduser("~/Documents/SimHub/Logs/**/*.csv"),
        os.path.expanduser("~/SimHub/Logs/*.csv"),
        "*.csv",
    ]
    candidates = []
    for pattern in search_paths:
        candidates.extend(glob.glob(pattern, recursive=True))

    if not candidates:
        return None

    # Return most recently modified
    return max(candidates, key=os.path.getmtime)


def detect_delimiter(filepath: str) -> str:
    """Auto-detect CSV delimiter (semicolon or comma)."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        first_line = f.readline()
    return ";" if first_line.count(";") > first_line.count(",") else ","


def find_column(headers: list, candidates: list) -> str | None:
    """Find the first matching column name from a list of candidates."""
    headers_lower = {h.lower(): h for h in headers}
    for candidate in candidates:
        if candidate in headers:
            return candidate
        if candidate.lower() in headers_lower:
            return headers_lower[candidate.lower()]
    return None


def safe_float(val: str, default: float = 0.0) -> float:
    """Parse a float safely, handling comma decimals and empty strings."""
    if not val or val.strip() == "":
        return default
    try:
        return float(val.strip().replace(",", "."))
    except ValueError:
        return default


def normalize_value(field: str, raw: float) -> float:
    """
    Convert SimHub raw values to our internal units.
    - Throttle/Brake: SimHub uses 0-100 or 0-1 — normalize to 0-1
    - Steering: degrees or normalized — normalize to radians
    - G-forces: already in G, convert to m/s²
    - Speed: already km/h
    """
    if field in ("throttle", "brake"):
        if raw > 1.5:
            return raw / 100.0  # was 0-100, normalize to 0-1
        return max(0.0, min(1.0, raw))

    if field == "steering":
        if abs(raw) > 10:
            return math.radians(raw)  # degrees → radians
        return raw  # already normalized/radians

    if field in ("ax", "ay"):
        if abs(raw) < 5:
            return raw * 9.81  # G → m/s²
        return raw  # already m/s²

    return raw


def parse_simhub_csv(filepath: str) -> list:
    """Parse a SimHub CSV file and return list of record dicts."""
    delimiter = detect_delimiter(filepath)
    print(f"  Delimiter: '{delimiter}'")

    records = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        headers = reader.fieldnames or []
        print(f"  Columns found: {len(headers)}")

        # Map our fields to actual CSV columns
        col_map = {}
        for field, candidates in FIELD_MAP.items():
            col = find_column(headers, candidates)
            if col:
                col_map[field] = col
                print(f"  {field} → '{col}'")
            else:
                print(f"  {field} → NOT FOUND (will be zero)")

        for row in reader:
            rec = {}
            for field, col in col_map.items():
                raw = safe_float(row.get(col, "0"))
                rec[field] = normalize_value(field, raw)

            # Fill missing fields with defaults
            for field in FIELD_MAP:
                if field not in rec:
                    rec[field] = 0.0

            records.append(rec)

    print(f"  Parsed {len(records)} rows")
    return records


def compute_distance(records: list) -> list:
    """
    Compute cumulative distance from speed and time.
    If track_pct is available and reliable, use it instead.
    """
    # Check if we have reliable time data
    times = [r["time_s"] for r in records]
    has_time = any(t > 0 for t in times)

    if has_time:
        # Sort by lap time to ensure order
        records = sorted(records, key=lambda r: r["time_s"])
        # Normalize time to start at 0
        t0 = records[0]["time_s"]
        for r in records:
            r["time_s"] = r["time_s"] - t0

    # Compute distance from speed integration
    dist = 0.0
    prev = None
    for r in records:
        if prev is not None:
            if has_time:
                dt = r["time_s"] - prev["time_s"]
            else:
                dt = 0.01  # assume ~100Hz if no time data
            dt = max(0.0, min(dt, 0.5))  # clamp to reasonable range
            avg_speed = (r["speed_kmh"] + prev["speed_kmh"]) / 2.0 / 3.6
            dist += avg_speed * dt
        r["dist_m"] = dist
        prev = r

    return records


def build_lap_json(records: list, label: str = "sim_lap") -> dict:
    """Convert parsed records into the standard lap JSON format."""
    if not records:
        raise ValueError("No records to convert")

    lap_time = records[-1]["time_s"] if records[-1]["time_s"] > 0 else 0.0
    lap_dist = records[-1]["dist_m"]

    def arr(key):
        return [round(r[key], 4) for r in records]

    return {
        "source": f"{label}.csv",
        "label": label,
        "laps": [
            {
                "lap_number": 1,
                "lap_time_s": round(lap_time, 3),
                "lap_dist_m": round(lap_dist, 1),
                "n_samples": len(records),
                "channels": {
                    "time_s":    arr("time_s"),
                    "dist_m":    arr("dist_m"),
                    "x":         arr("x"),
                    "y":         arr("y"),
                    "speed_kmh": arr("speed_kmh"),
                    "speed_ms":  [round(r["speed_kmh"] / 3.6, 4) for r in records],
                    "throttle":  arr("throttle"),
                    "brake":     arr("brake"),
                    "steering":  arr("steering"),
                    "gear":      [int(r["gear"]) for r in records],
                    "rpm":       arr("rpm"),
                    "ax":        arr("ax"),
                    "ay":        arr("ay"),
                    # Wheel data not available from sim — fill with zeros
                    "wheel_speed_fl": [0.0] * len(records),
                    "wheel_speed_fr": [0.0] * len(records),
                    "wheel_speed_rl": [0.0] * len(records),
                    "wheel_speed_rr": [0.0] * len(records),
                    "slip_ratio_fl":  [0.0] * len(records),
                    "slip_ratio_fr":  [0.0] * len(records),
                    "slip_ratio_rl":  [0.0] * len(records),
                    "slip_ratio_rr":  [0.0] * len(records),
                    "brake_pressure_fl": arr("brake"),
                    "brake_pressure_fr": arr("brake"),
                },
            }
        ],
    }


def run_full_pipeline(sim_json_path: str):
    """After normalizing, run the full analysis pipeline automatically."""
    import subprocess
    print("\nRunning full pipeline with your sim lap...")
    result = subprocess.run(
        [sys.executable, "run.py", "--comp-json", sim_json_path],
        capture_output=False
    )


def main():
    parser = argparse.ArgumentParser(
        description="Convert SimHub CSV to AI Race Engineer format",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "csv_file",
        nargs="?",
        help="Path to SimHub CSV file (auto-detects latest if not specified)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON path (default: output/sim_lap.json)",
    )
    parser.add_argument(
        "--label", "-l",
        default="sim_lap",
        help="Label for this lap (default: sim_lap)",
    )
    parser.add_argument(
        "--run", "-r",
        action="store_true",
        help="Run full pipeline after normalizing",
    )
    args = parser.parse_args()

    # Find CSV file
    csv_path = args.csv_file
    if not csv_path:
        print("No CSV specified — searching for latest SimHub export...")
        csv_path = find_latest_simhub_csv()
        if not csv_path:
            print("ERROR: No CSV file found.")
            print("Either specify a path or set up SimHub Raw Log export.")
            print("  python3 normalize.py path/to/lap.csv")
            sys.exit(1)
        print(f"Found: {csv_path}")

    if not os.path.exists(csv_path):
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(1)

    # Output path
    output_path = args.output or "output/sim_lap.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"\nNormalizing: {Path(csv_path).name}")
    print("-" * 40)

    # Parse
    records = parse_simhub_csv(csv_path)
    if not records:
        print("ERROR: No data rows found in CSV.")
        sys.exit(1)

    # Compute distance
    records = compute_distance(records)

    # Build JSON
    lap_data = build_lap_json(records, label=args.label)
    lap = lap_data["laps"][0]

    # Save
    with open(output_path, "w") as f:
        json.dump(lap_data, f, indent=2)

    print("-" * 40)
    print(f"Lap time : {lap['lap_time_s']:.3f}s")
    print(f"Distance : {lap['lap_dist_m']:.0f}m")
    print(f"Samples  : {lap['n_samples']}")
    print(f"Saved    → {output_path}")
    print()
    print("Next step — compare against the reference lap:")
    print(f"  Edit COMP_JSON in run.py to point at {output_path}")
    print("  Then press ▶ in VS Code")

    if args.run:
        run_full_pipeline(output_path)


if __name__ == "__main__":
    main()
