# AI Race Coaching Engine
Constructor GenAI Hackathon 2026 — Autonomous Track

Analyzes real autonomous racing telemetry from Yas Marina Circuit and generates
corner-by-corner coaching feedback using the Claude API.

## Setup

```bash
# Clone / place your MCAP files in a data/ folder
mkdir data
cp hackathon_*.mcap data/

# Install dependencies
pip install -r requirements.txt

# Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...
```

## Quick Start (full pipeline)

```bash
python main.py run data/hackathon_fast_laps.mcap data/hackathon_good_lap.mcap
```

This runs all four steps automatically and prints the coaching report.

---

## Step-by-Step

### Step 0: Inspect message schema (run this first if extraction fails)
```bash
python main.py inspect data/hackathon_fast_laps.mcap
```
This prints the actual field names in the StateEstimation message.
If extraction fails, check these names match what's in `src/extractor.py`.

### Step 1: Extract telemetry from MCAP files
```bash
python main.py extract data/hackathon_fast_laps.mcap
python main.py extract data/hackathon_good_lap.mcap
```
Outputs: `output/hackathon_fast_laps.json`, `output/hackathon_good_lap.json`

### Step 2: Analyze — compare good lap vs fast lap
```bash
# fast_laps = reference, good_lap = driver being coached
python main.py analyze output/hackathon_fast_laps.json output/hackathon_good_lap.json
```
Outputs: `output/analysis.json`

For fast_laps which has two laps, specify which lap:
```bash
python main.py analyze output/hackathon_fast_laps.json output/hackathon_good_lap.json --ref-lap 1
```

### Step 3: Generate coaching report
```bash
python main.py coach output/analysis.json
```
Outputs: `output/analysis_coaching.json` + prints report to terminal

---

## Output Files

| File | Contents |
|------|----------|
| `output/*.json` | Extracted lap telemetry (time, distance, speed, inputs, wheel data) |
| `output/analysis.json` | Sector/corner deltas between two laps |
| `output/analysis_coaching.json` | AI coaching report with priority actions |

## Troubleshooting

**"No StateEstimation messages found"**
→ Run `python main.py inspect <file.mcap>` to see what topics are in the file.
→ The topic may be `/constructor0/state_estimation` or similar.

**Lap split wrong (e.g., only 1 lap detected in fast_laps)**
→ Edit `YAS_MARINA_LAP_M` in `src/extractor.py` — try 2640 (half the circuit) 
→ Or the MCAP file has data for only one lap in each file.

**Field values all zero**
→ Run inspect mode to find the actual field names
→ Edit `_safe_get()` calls in `src/extractor.py` to match real field names

## Architecture

```
MCAP file → extractor.py → lap JSON
                                ↓
                          analyzer.py → analysis JSON
                                            ↓
                                      coach.py → Claude API → coaching report
```

## Adding Sim Data (tomorrow)

When your simulator is set up, you'll get a CSV from SimHub.
Run the normalizer (coming in Phase 2):
```bash
python normalize.py sim_lap.csv --output output/sim_lap.json
python main.py analyze output/hackathon_fast_laps.json output/sim_lap.json
python main.py coach output/analysis.json
```
Same coaching engine, zero changes needed.
