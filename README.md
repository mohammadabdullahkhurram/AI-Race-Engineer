# AI Race Engineer
**Constructor GenAI Hackathon 2026 — Autonomous Track**

An end-to-end race engineering system that ingests real autonomous racing telemetry from Yas Marina Circuit, compares laps against a reference, detects race events, and generates corner-by-corner coaching feedback — all visualised in an F1-style dashboard.

No AI API key required. Runs fully offline.

---

## Project Structure

```
AI-Race-Engineer/
├── test.py              ← MCAP testing only — compares good lap vs fast lap, press ▶
├── server.py            ← upload server — drag & drop your own CSV or MCAP lap
├── normalize.py         ← SimHub CSV converter (command line alternative to server.py)
├── requirements.txt
├── src/
│   ├── extractor.py     ← parses MCAP files → lap JSON + extra CAN channels
│   ├── analyzer.py      ← auto-detects corners/sectors, aligns laps, computes deltas
│   ├── coach.py         ← rule-based coaching engine with motivational feedback
│   ├── race_analyzer.py ← wheel-to-wheel race event detection
│   └── dashboard.py     ← shared F1-style dashboard builder
└── output/              ← generated files land here
    ├── fast_laps.json
    ├── good_lap.json
    ├── analysis.json
    ├── coaching.json
    ├── race_laps.json
    ├── race_analysis.json
    └── dashboard.html   ← opens automatically in browser
```

Data files live one level up in a shared `data/` folder:
```
data/
├── hackathon_fast_laps.mcap
├── hackathon_good_lap.mcap
├── hackathon_wheel_to_wheel.mcap
├── yas_marina_bnd.json
├── sd_msgs/
└── intrinsics/          ← camera calibration YAMLs (future use)
```

---

## Setup

```bash
pip install -r requirements.txt
```

No API keys needed.

---

## Running

### Option 1 — Test with provided MCAP data
Open `test.py` in VS Code and press **▶**.

Runs the full pipeline automatically:
1. Extracts telemetry from both MCAP files
2. Auto-detects corners and sectors from GPS data
3. Aligns laps by distance and computes deltas
4. Generates motivational corner-by-corner coaching report
5. Extracts brake disc and tyre temperature data from CAN bus
6. Analyses wheel-to-wheel race file for events
7. Builds dashboard and opens it in your browser

### Option 2 — Upload your own lap (SimHub CSV or MCAP)
```bash
python3 server.py
```
Opens `http://localhost:5000` in your browser. Drag and drop your lap file — coaching dashboard appears automatically.

**Supported formats:**
- `.csv` — exported from SimHub after driving in Assetto Corsa
- `.mcap` — raw ROS 2 bag file

Your lap is compared against the real A2RL autonomous car's fastest lap at Yas Marina.

---

## Architecture

```
hackathon_fast_laps.mcap  ──┐
                             ├──► extractor.py ──► lap JSON
hackathon_good_lap.mcap   ──┘         │
      (or your uploaded lap)           │
                                       ▼
                                  analyzer.py ──► analysis JSON
                                  (auto-detects corners + sectors
                                   from GPS — works on any track)
                                       │
                                       ▼
                                  coach.py ──► coaching JSON
                                  (rule-based, no LLM needed)
                                       │
hackathon_wheel_to_wheel.mcap ──► race_analyzer.py ──► race JSON
                                       │
                              extra CAN channels ──► brake/tyre temps
                                       │
                                       ▼
                                 dashboard.py ──► dashboard.html
```

### Key design decisions

**Rule-based coaching, not LLM.** The coaching engine uses deterministic rules derived from motorsport technique — brake point delta, apex speed loss, throttle pick-up delay. Runs offline, produces consistent output, fully explainable reasoning.

**Track-agnostic corner detection.** Corners are auto-detected from local speed minima in the reference lap GPS data. No hardcoded track positions — works on any circuit, not just Yas Marina.

**Distance-aligned comparison.** Laps are compared on a common distance grid (5m resolution), not by time. This correctly handles sections where one lap is faster — the comparison stays spatially meaningful.

**Event detection for race scenarios.** The wheel-to-wheel analyzer looks for unplanned braking spikes and lift-off events that are out of character with normal corner profiles — signatures of the autonomous car reacting to other vehicles.

**Dual entry points.** `test.py` uses the provided MCAP dataset directly. `server.py` accepts any driver's lap via file upload, normalizes it, and runs the same pipeline — so the system coaches real drivers against the autonomous reference.

---

## Dashboard

The generated `output/dashboard.html` includes:

- **Hero lap time comparison** — reference vs driver with gap
- **Sector breakdown** — time delta, min speed, throttle per sector
- **Telemetry trace** — speed / throttle / brake overlaid by distance, switchable tabs
- **Track map** — GPS path color-coded by speed delta (red = slower, teal = faster) with real Yas Marina boundary overlay
- **Priority actions** — ranked coaching cards with exact numbers, fixes, and motivational language
- **Corner analysis** — per-corner breakdown of braking, apex speed, throttle pick-up
- **Brake disc temperatures** — all 4 corners over lap time with peak values (reference car)
- **Tyre temperatures** — all 4 tyres over lap time with average values (reference car)
- **Race analysis** — multi-lap pace table, lap variation, detected race events with descriptions

---

## Data

Three MCAP files from real autonomous racing at Yas Marina Circuit (Abu Dhabi):

| File | Duration | Description |
|------|----------|-------------|
| `hackathon_fast_laps.mcap` | 74.3s | Two fastest laps — used as reference |
| `hackathon_good_lap.mcap` | 81.3s | Conservative lap — used as comparison in test.py |
| `hackathon_wheel_to_wheel.mcap` | 226s | Multi-lap race scenario |

### Telemetry channels used

**StateEstimation topic (~100 Hz):**

| Channel | Field |
|---------|-------|
| Position | `x_m`, `y_m`, `z_m` |
| Speed | `v_mps` |
| Acceleration | `ax_mps2`, `ay_mps2` |
| Inputs | `gas`, `brake`, `delta_wheel_rad` |
| Wheel speeds | `omega_w_fl/fr/rl/rr` |
| Slip ratios | `lambda_fl/fr/rl/rr_perc` |
| Slip angles | `alpha_fl/fr/rl/rr_rad` |
| Brake pressure | `cba_actual_pressure_fl/fr/rl/rr_pa` |

**CAN bus topics (extra channels):**

| Topic | Data |
|-------|------|
| `badenia_560_brake_disk_temp` | Brake disc temps per corner |
| `badenia_560_tpms_front/rear` | Tyre pressure and temperature |
| `badenia_560_tyre_surface_temp_front/rear` | Tyre surface temps (inner/mid/outer) |

### Also available (not yet used)
- `camera_fl` and `camera_r` — onboard compressed video at 10 Hz
- `intrinsics/` — camera calibration YAMLs for undistortion
- `vectornav/raw/gps` — raw GNSS from dual VectorNav receivers
- Suspension, ride height, powertrain CAN channels

---

## Troubleshooting

**Port 5000 already in use (server.py)**
→ macOS AirPlay Receiver blocks port 5000. Disable it in System Settings → General → AirDrop & Handoff, or change port in `server.py` to 8080.

**FileNotFoundError on MCAP files**
→ Paths are resolved relative to the script. Ensure `data/` is one level above `AI-Race-Engineer/` and `BASE_DIR.parent` points there.

**"No StateEstimation messages found"**
→ Verify the topic with:
```bash
python3 -c "
from mcap_ros2.reader import read_ros2_messages
for item in read_ros2_messages('path/to/file.mcap'):
    print(item.channel.topic); break
"
```

**Brake/tyre temp panels missing from dashboard**
→ The CAN field names may differ. This is non-critical — the coaching analysis still works fully without them.

**Race analysis shows 0 events**
→ Tune `EVENT_BRAKE_THRESH` and `EVENT_SPEED_DROP` in `src/race_analyzer.py`.

**Friend gets SyntaxError on f-string backslash**
→ Ensure Python 3.12+ is installed. Download from python.org/downloads.
