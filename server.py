"""
server.py — AI Race Engineer
Run once on your Mac. Leave it running all session.
  python3 server.py
"""

import json, os, sys, math, csv, webbrowser, threading, time
from pathlib import Path
from io import StringIO
from datetime import datetime

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
LAPS_DIR   = OUTPUT_DIR / "laps"
sys.path.insert(0, str(BASE_DIR / "src"))

from flask import Flask, request, jsonify, send_file, make_response, render_template_string
from extractor import extract_lap, save_lap_json
from analyzer  import run_analysis
from coach     import generate_coaching_report
from dashboard import build_dashboard

app = Flask(__name__)
last_lap = {"t": 0, "lap_time": None, "samples": None, "label": None, "lap_id": None}

FIELD_MAP = {
    "speed_kmh": ["SpeedKmh","SpeedKMH","Speed","speed_kmh"],
    "throttle":  ["Throttle","throttle","Gas","gas"],
    "brake":     ["Brake","brake"],
    "steering":  ["Steering","steering","SteerAngle","steerAngle"],
    "gear":      ["Gear","gear"],
    "rpm":       ["Rpms","RPM","rpm"],
    "ax":        ["GlobalAccelerationG","LongitudinalG","ax"],
    "ay":        ["LateralG","LateralAccelerationG","ay"],
    "time_s":    ["LapTimeCurrent","CurrentLapTime","laptime"],
    "x":         ["CarCoordX","PosX"],
    "y":         ["CarCoordZ","PosZ","CarCoordZ"],
}


# ── Lap index (in-memory + persisted to JSON) ─────────────────────────────────

def load_lap_index():
    idx = LAPS_DIR / "index.json"
    if idx.exists():
        with open(idx) as f:
            return json.load(f)
    return []


def save_lap_index(entries):
    LAPS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LAPS_DIR / "index.json", "w") as f:
        json.dump(entries, f, indent=2)


def next_lap_id():
    entries = load_lap_index()
    return len(entries) + 1


def fmt_time(s):
    s = abs(s)
    m = int(s) // 60
    sec = s % 60
    return f"{m}:{sec:06.3f}" if m > 0 else f"{sec:.3f}s"


# ── CSV normalizer ────────────────────────────────────────────────────────────

def detect_delimiter(text):
    first = text.split("\n")[0]
    return ";" if first.count(";") > first.count(",") else ","


def find_column(headers, candidates):
    hl = {h.lower(): h for h in headers}
    for c in candidates:
        if c in headers: return c
        if c.lower() in hl: return hl[c.lower()]
    return None


def safe_float(val, default=0.0):
    try: return float(str(val).strip().replace(",", "."))
    except: return default


def normalize_value(field, raw):
    if field in ("throttle", "brake"):
        return max(0.0, min(1.0, raw / 100.0 if raw > 1.5 else raw))
    if field == "steering":
        return math.radians(raw) if abs(raw) > 10 else raw
    if field in ("ax", "ay"):
        return raw * 9.81 if abs(raw) < 5 else raw
    if field == "time_s":
        return raw / 1000.0 if raw > 1000 else raw
    return raw


def csv_to_lap_json(csv_text, label="sim_lap"):
    delim  = detect_delimiter(csv_text)
    reader = csv.DictReader(StringIO(csv_text), delimiter=delim)
    headers = reader.fieldnames or []
    print(f"  Delimiter: '{delim}' | Columns: {headers[:5]}...")

    col_map = {}
    for field, candidates in FIELD_MAP.items():
        col = find_column(headers, candidates)
        if col: col_map[field] = col

    records = []
    for row in reader:
        rec = {}
        for field, col in col_map.items():
            rec[field] = normalize_value(field, safe_float(row.get(col, "0")))
        for field in FIELD_MAP:
            if field not in rec: rec[field] = 0.0
        records.append(rec)

    if not records:
        raise ValueError("No data rows found in CSV")

    times = [r["time_s"] for r in records]
    if any(t > 0 for t in times):
        records = sorted(records, key=lambda r: r["time_s"])
        t0 = records[0]["time_s"]
        for r in records: r["time_s"] -= t0

    dist, prev = 0.0, None
    for r in records:
        if prev is not None:
            dt = max(0, min(0.5, r["time_s"] - prev["time_s"]))
            dist += (r["speed_kmh"] + prev["speed_kmh"]) / 2.0 / 3.6 * dt
        r["dist_m"] = dist
        prev = r

    lap_time = records[-1]["time_s"] if records[-1]["time_s"] > 0 else 0.0

    def arr(k): return [round(r[k], 4) for r in records]

    return {
        "source": f"{label}.csv", "label": label,
        "laps": [{
            "lap_number": 1, "lap_time_s": round(lap_time, 3),
            "lap_dist_m": round(records[-1]["dist_m"], 1), "n_samples": len(records),
            "channels": {
                "time_s": arr("time_s"), "dist_m": arr("dist_m"),
                "x": arr("x"), "y": arr("y"),
                "speed_kmh": arr("speed_kmh"),
                "speed_ms": [round(r["speed_kmh"]/3.6, 4) for r in records],
                "throttle": arr("throttle"), "brake": arr("brake"),
                "steering": arr("steering"), "gear": [int(r["gear"]) for r in records],
                "rpm": arr("rpm"), "ax": arr("ax"), "ay": arr("ay"),
                "wheel_speed_fl": [0.0]*len(records), "wheel_speed_fr": [0.0]*len(records),
                "wheel_speed_rl": [0.0]*len(records), "wheel_speed_rr": [0.0]*len(records),
                "slip_ratio_fl": [0.0]*len(records),  "slip_ratio_fr": [0.0]*len(records),
                "slip_ratio_rl": [0.0]*len(records),  "slip_ratio_rr": [0.0]*len(records),
                "brake_pressure_fl": arr("brake"), "brake_pressure_fr": arr("brake"),
            }
        }]
    }


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(lap_id, comp_json_path):
    OUTPUT_DIR.mkdir(exist_ok=True)
    LAPS_DIR.mkdir(parents=True, exist_ok=True)
    lap_dir = LAPS_DIR / f"lap_{lap_id}"
    lap_dir.mkdir(exist_ok=True)

    ref_json = str(OUTPUT_DIR / "fast_laps.json")
    ref_mcap = str(BASE_DIR.parent / "data/hackathon_fast_laps.mcap")
    if not Path(ref_json).exists():
        ref_data = extract_lap(ref_mcap, lap_label="fast_laps")
        save_lap_json(ref_data, ref_json)

    analysis = run_analysis(ref_json, comp_json_path)
    coaching  = generate_coaching_report(analysis)
    html      = build_dashboard(analysis, coaching, ref_json, comp_json_path, None)

    # Save per-lap files
    with open(lap_dir / "analysis.json", "w") as f: json.dump(analysis, f, indent=2)
    with open(lap_dir / "coaching.json", "w") as f: json.dump(coaching, f, indent=2)
    with open(lap_dir / "dashboard.html","w") as f: f.write(html)

    return analysis, coaching


# ── Home page — lap history overview ─────────────────────────────────────────

HOME_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Race Engineer</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;700;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--red:#E8002D;--teal:#00D2BE;--yellow:#FFD700;--bg:#050505;--bg2:#0d0d0d;--bg3:#141414;--border:#1e1e1e;--muted:#555;--fd:'Barlow Condensed',sans-serif;--fm:'JetBrains Mono',monospace}
body{background:var(--bg);color:#fff;font-family:var(--fm);min-height:100vh}
body::before{content:'';position:fixed;inset:0;background-image:repeating-linear-gradient(45deg,rgba(255,255,255,.012) 0,rgba(255,255,255,.012) 1px,transparent 1px,transparent 8px),repeating-linear-gradient(-45deg,rgba(255,255,255,.012) 0,rgba(255,255,255,.012) 1px,transparent 1px,transparent 8px);pointer-events:none}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:14px 32px;border-bottom:1px solid var(--border);position:relative;z-index:1}
.topbar::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--red)}
.brand{display:flex;align-items:center;gap:10px}
.brand-flag{width:3px;height:28px;background:var(--red)}
.brand-name{font-family:var(--fd);font-size:20px;font-weight:800;letter-spacing:4px;text-transform:uppercase}
.brand-sub{font-size:9px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-top:2px}
.status-pill{display:flex;align-items:center;gap:8px;padding:6px 14px;background:var(--bg2);border:1px solid var(--border);font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--muted)}
.status-dot{width:7px;height:7px;border-radius:50%;background:var(--muted)}
.status-dot.live{background:var(--teal);animation:pulse .8s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.wrap{max-width:1200px;margin:0 auto;padding:32px;position:relative;z-index:1}

/* Incoming lap banner */
.incoming{background:var(--bg2);border:1px solid var(--red);border-left:3px solid var(--red);padding:14px 20px;margin-bottom:24px;display:none;align-items:center;gap:12px}
.incoming-dot{width:8px;height:8px;border-radius:50%;background:var(--red);animation:pulse .6s infinite;flex-shrink:0}
.incoming-text{font-size:11px;letter-spacing:1px;color:#fff}

/* Section label */
.sec{font-family:var(--fd);font-size:11px;letter-spacing:4px;text-transform:uppercase;color:var(--muted);margin-bottom:16px;display:flex;align-items:center;gap:12px}
.sec::after{content:'';flex:1;height:1px;background:var(--border)}

/* Lap cards grid */
.laps-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;margin-bottom:32px}
.lap-card{background:var(--bg2);border:1px solid var(--border);padding:0;cursor:pointer;transition:border-color .2s,transform .15s;text-decoration:none;display:block;position:relative}
.lap-card::before{content:'';display:block;height:2px;background:var(--border)}
.lap-card.best::before{background:var(--teal)}
.lap-card.latest::before{background:var(--red)}
.lap-card:hover{border-color:#444;transform:translateY(-2px)}
.lap-card-body{padding:20px}
.lap-card-header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px}
.lap-num{font-family:var(--fd);font-size:13px;font-weight:700;letter-spacing:3px;text-transform:uppercase;color:var(--muted)}
.lap-badge{font-size:9px;letter-spacing:2px;text-transform:uppercase;padding:2px 8px;border:1px solid}
.lap-badge.best{color:var(--teal);border-color:var(--teal)}
.lap-badge.latest{color:var(--red);border-color:var(--red)}
.lap-time{font-family:var(--fd);font-size:40px;font-weight:800;letter-spacing:-1px;line-height:1;margin-bottom:4px}
.lap-label{font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:14px}
.lap-stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding-top:12px;border-top:1px solid var(--border)}
.lap-stat-label{font-size:9px;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-bottom:3px}
.lap-stat-val{font-size:13px;font-weight:700}
.lap-card-footer{padding:10px 20px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
.view-btn{font-family:var(--fd);font-size:13px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#fff}
.dl-link{font-size:10px;color:var(--muted);text-decoration:none;letter-spacing:1px}
.dl-link:hover{color:var(--teal)}

/* Empty state */
.empty{text-align:center;padding:80px 24px;color:var(--muted)}
.empty-icon{font-size:48px;margin-bottom:16px;opacity:.3}
.empty-title{font-family:var(--fd);font-size:22px;font-weight:700;letter-spacing:3px;text-transform:uppercase;margin-bottom:8px}
.empty-sub{font-size:11px;letter-spacing:1px}

/* Upload zone */
.upload-section{background:var(--bg2);border:1px solid var(--border);padding:20px}
.upload-section::before{content:'';display:block;height:1px;background:var(--border);margin:-20px -20px 20px}
.upload-inner{display:flex;gap:16px;align-items:center}
.upload-zone{flex:1;border:1px dashed #2a2a2a;padding:16px;text-align:center;cursor:pointer;position:relative;transition:border-color .2s}
.upload-zone:hover{border-color:#444}
.upload-zone input{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.upload-zone-text{font-size:11px;color:#444;letter-spacing:1px}
.upload-btn{padding:14px 24px;background:var(--red);border:none;color:#fff;font-family:var(--fd);font-size:16px;font-weight:700;letter-spacing:3px;text-transform:uppercase;cursor:pointer;white-space:nowrap;display:none}
.upload-btn:disabled{background:#333;cursor:not-allowed}
.upload-fname{font-size:11px;color:var(--teal);margin-top:6px;display:none}
</style>
</head>
<body>

<div class="topbar">
  <div class="brand">
    <div class="brand-flag"></div>
    <div>
      <div class="brand-name">AI Race Engineer</div>
      <div class="brand-sub">Yas Marina Circuit · Session Overview</div>
    </div>
  </div>
  <div class="status-pill">
    <div class="status-dot live" id="sdot"></div>
    <span id="stext">Live — waiting for lap</span>
  </div>
</div>

<div class="wrap">

  <div class="incoming" id="incoming">
    <div class="incoming-dot"></div>
    <div class="incoming-text" id="incomingText">Processing lap...</div>
  </div>

  <div class="sec" id="lapsLabel">Lap History</div>
  <div class="laps-grid" id="lapsGrid">
    <div class="empty">
      <div class="empty-icon">🏁</div>
      <div class="empty-title">No Laps Yet</div>
      <div class="empty-sub">Drive a lap — coaching appears automatically</div>
    </div>
  </div>

  <div class="sec">Manual Upload</div>
  <div class="upload-section">
    <div class="upload-inner">
      <div>
        <div class="upload-zone" id="zone">
          <input type="file" id="fi" accept=".csv,.mcap">
          <div class="upload-zone-text">Drop CSV or MCAP here if auto-send fails</div>
        </div>
        <div class="upload-fname" id="fname"></div>
      </div>
      <button class="upload-btn" id="ubtn" onclick="doUpload()">Analyse →</button>
    </div>
  </div>

</div>

<script>
let lastT = 0, file = null, lapsData = [];

function fmtTime(s) {
  const m = Math.floor(s/60), sec = (s%60).toFixed(3).padStart(6,'0');
  return m > 0 ? m+':'+sec : s.toFixed(3)+'s';
}
function fmtGap(s) {
  const sign = s > 0 ? '+' : '';
  return sign + s.toFixed(3) + 's';
}
function gapColor(s) { return s > 0 ? '#E8002D' : '#00D2BE'; }

function renderLaps(laps) {
  const grid = document.getElementById('lapsGrid');
  const label = document.getElementById('lapsLabel');
  if (!laps || laps.length === 0) {
    grid.innerHTML = '<div class="empty"><div class="empty-icon">🏁</div><div class="empty-title">No Laps Yet</div><div class="empty-sub">Drive a lap — coaching appears automatically</div></div>';
    label.textContent = 'Lap History';
    return;
  }

  label.textContent = 'Lap History — ' + laps.length + ' Lap' + (laps.length > 1 ? 's' : '');

  // Find best lap
  const bestId = laps.reduce((b, l) => l.lap_time_s < (b ? b.lap_time_s : Infinity) ? l : b, null)?.lap_id;
  const latestId = laps[laps.length-1].lap_id;

  // Newest first
  grid.innerHTML = [...laps].reverse().map(l => {
    const isBest   = l.lap_id === bestId && laps.length > 1;
    const isLatest = l.lap_id === latestId;
    const badge    = isBest ? '<span class="lap-badge best">★ Best</span>' :
                     isLatest ? '<span class="lap-badge latest">Latest</span>' : '';
    const gapStr   = fmtGap(l.gap_s);
    const gapCol   = gapColor(l.gap_s);
    const ts       = l.timestamp ? new Date(l.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';

    return `
    <div class="lap-card ${isBest ? 'best' : isLatest ? 'latest' : ''}" onclick="window.location='/dashboard/${l.lap_id}'">
      <div class="lap-card-body">
        <div class="lap-card-header">
          <div class="lap-num">Lap ${l.lap_id}</div>
          ${badge}
        </div>
        <div class="lap-time">${fmtTime(l.lap_time_s)}</div>
        <div class="lap-label">${l.label || 'Driver'} · ${ts}</div>
        <div class="lap-stats">
          <div>
            <div class="lap-stat-label">Gap to A2RL</div>
            <div class="lap-stat-val" style="color:${gapCol}">${gapStr}</div>
          </div>
          <div>
            <div class="lap-stat-label">Samples</div>
            <div class="lap-stat-val">${l.samples.toLocaleString()}</div>
          </div>
        </div>
      </div>
      <div class="lap-card-footer">
        <span class="view-btn">View Analysis →</span>
        <a class="dl-link" href="/download/csv/${l.lap_id}" onclick="event.stopPropagation()" download="lap_${l.lap_id}.csv">↓ CSV</a>
      </div>
    </div>`;
  }).join('');
}

async function poll() {
  try {
    const res  = await fetch('/status');
    const data = await res.json();

    // Update status pill
    if (data.t > 0 && data.t > lastT) {
      lastT = data.t;
      document.getElementById('stext').textContent = 'Live — last lap: ' + fmtTime(data.lap_time || 0);
      document.getElementById('incoming').style.display = 'none';
      // Refresh laps
      fetchLaps();
    }
  } catch(e) {}

  try {
    const res2  = await fetch('/laps_json');
    const laps2 = await res2.json();
    if (JSON.stringify(laps2) !== JSON.stringify(lapsData)) {
      lapsData = laps2;
      renderLaps(laps2);
    }
  } catch(e) {}
}

async function fetchLaps() {
  try {
    const res  = await fetch('/laps_json');
    const laps = await res.json();
    lapsData = laps;
    renderLaps(laps);
  } catch(e) {}
}

setInterval(poll, 2000);
fetchLaps();

// Manual upload
const zone = document.getElementById('zone');
const fi   = document.getElementById('fi');
zone.addEventListener('dragover', e => e.preventDefault());
zone.addEventListener('drop', e => { e.preventDefault(); pick(e.dataTransfer.files[0]); });
fi.addEventListener('change', () => pick(fi.files[0]));
function pick(f) {
  if (!f) return; file = f;
  document.getElementById('fname').textContent = f.name;
  document.getElementById('fname').style.display = 'block';
  document.getElementById('ubtn').style.display = 'inline-block';
}
async function doUpload() {
  if (!file) return;
  const btn = document.getElementById('ubtn');
  btn.disabled = true; btn.textContent = 'Processing...';
  document.getElementById('incoming').style.display = 'flex';
  document.getElementById('incomingText').textContent = 'Processing ' + file.name + '...';
  const fd = new FormData(); fd.append('file', file);
  try {
    const res  = await fetch('/upload', {method:'POST', body:fd});
    const data = await res.json();
    document.getElementById('incoming').style.display = 'none';
    if (data.ok) { fetchLaps(); btn.textContent='Done ✓'; }
    else { btn.textContent = 'Error'; btn.disabled = false; }
  } catch(e) { btn.textContent = 'Error'; btn.disabled = false; }
}
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HOME_PAGE)


@app.route("/status")
def status():
    return jsonify(last_lap)


@app.route("/laps_json")
def laps_json():
    return jsonify(load_lap_index())


@app.route("/api/boundaries")
def api_boundaries():
    """Serves track boundary data immediately - no lap processing needed."""
    left_bnd, right_bnd = [], []
    for bnd_path in [BASE_DIR / "yas_marina_bnd.json",
                     BASE_DIR.parent / "data/yas_marina_bnd.json"]:
        if bnd_path.exists():
            with open(bnd_path) as f:
                bnd = json.load(f)["boundaries"]
            step = 8
            left_bnd  = bnd["left_border"][::step]
            right_bnd = bnd["right_border"][::step]
            break
    return jsonify({
        "ok":        len(left_bnd) > 0,
        "left_bnd":  left_bnd,
        "right_bnd": right_bnd,
    })


@app.route("/api/reference")
def api_reference():
    """
    Returns the reference lap data for real-time coaching on the PC.
    Downsampled to every 5th point to keep it fast.
    """
    ref_json = OUTPUT_DIR / "fast_laps.json"
    if not ref_json.exists():
        # Try to extract it
        ref_mcap = str(BASE_DIR.parent / "data/hackathon_fast_laps.mcap")
        try:
            ref_data = extract_lap(ref_mcap, lap_label="fast_laps")
            save_lap_json(ref_data, str(ref_json))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    with open(ref_json) as f:
        data = json.load(f)

    ch   = data["laps"][0]["channels"]
    step = 5  # downsample

    # Build corner lookup from last analysis if available
    corners = []
    analysis_path = OUTPUT_DIR / "laps"
    # Find any existing analysis
    for lap_dir in sorted(analysis_path.glob("lap_*")):
        ap = lap_dir / "analysis.json"
        if ap.exists():
            with open(ap) as af:
                an = json.load(af)
            corners = an.get("corners", [])
            break

    # Load track boundaries
    left_bnd, right_bnd = [], []
    bnd_path = BASE_DIR.parent / "data/yas_marina_bnd.json"
    if not bnd_path.exists():
        bnd_path = BASE_DIR / "yas_marina_bnd.json"
    if bnd_path.exists():
        with open(bnd_path) as bf:
            bnd = json.load(bf)["boundaries"]
        bstep = 10  # downsample boundaries
        left_bnd  = bnd["left_border"][::bstep]
        right_bnd = bnd["right_border"][::bstep]

    return jsonify({
        "x":         ch["x"][::step],
        "y":         ch["y"][::step],
        "dist_m":    ch["dist_m"][::step],
        "speed_kmh": ch["speed_kmh"][::step],
        "throttle":  ch["throttle"][::step],
        "brake":     ch["brake"][::step],
        "corners":   corners,
        "total_dist": ch["dist_m"][-1],
        "left_bnd":  left_bnd,
        "right_bnd": right_bnd,
    })


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file"})

    f        = request.files["file"]
    filename = f.filename.lower()

    if not (filename.endswith(".csv") or filename.endswith(".mcap")):
        return jsonify({"ok": False, "error": "Only .csv or .mcap accepted"})

    try:
        OUTPUT_DIR.mkdir(exist_ok=True)
        LAPS_DIR.mkdir(parents=True, exist_ok=True)

        lap_id = next_lap_id()
        lap_dir = LAPS_DIR / f"lap_{lap_id}"
        lap_dir.mkdir(exist_ok=True)

        label = Path(f.filename).stem

        if filename.endswith(".mcap"):
            tmp = str(lap_dir / f"tmp.mcap")
            f.save(tmp)
            lap_data = extract_lap(tmp, lap_label=label)
            save_lap_json(lap_data, str(lap_dir / "sim_lap.json"))
            lap = lap_data["laps"][0]
            os.remove(tmp)
        else:
            csv_text = f.read().decode("utf-8-sig")
            # Save raw CSV
            with open(lap_dir / "lap.csv", "w", encoding="utf-8") as cf:
                cf.write(csv_text)
            lap_data = csv_to_lap_json(csv_text, label=label)
            lap = lap_data["laps"][0]
            with open(lap_dir / "sim_lap.json", "w") as jf:
                json.dump(lap_data, jf, indent=2)

        # Override lap time with AC's reported time if provided
        ac_lap_time_ms = request.form.get("ac_lap_time_ms")
        if ac_lap_time_ms:
            try:
                ac_time_s = int(ac_lap_time_ms) / 1000.0
                lap["lap_time_s"] = round(ac_time_s, 3)
                with open(lap_dir / "sim_lap.json", "r") as jf:
                    jdata = json.load(jf)
                jdata["laps"][0]["lap_time_s"] = round(ac_time_s, 3)
                with open(lap_dir / "sim_lap.json", "w") as jf:
                    json.dump(jdata, jf, indent=2)
                print(f"  AC lap time override: {ac_time_s:.3f}s")
            except Exception as e:
                print(f"  Could not parse ac_lap_time_ms: {e}")

        # Run pipeline
        analysis, coaching = run_pipeline(lap_id, str(lap_dir / "sim_lap.json"))
        gap = analysis.get("total_time_delta_s", 0)

        # Update lap index
        entries = load_lap_index()
        entries.append({
            "lap_id":     lap_id,
            "label":      label,
            "lap_time_s": lap["lap_time_s"],
            "gap_s":      round(gap, 3),
            "samples":    lap["n_samples"],
            "timestamp":  datetime.now().isoformat(),
        })
        save_lap_index(entries)

        # Update status for polling
        last_lap["t"]        = time.time()
        last_lap["lap_time"] = lap["lap_time_s"]
        last_lap["samples"]  = lap["n_samples"]
        last_lap["label"]    = label
        last_lap["lap_id"]   = lap_id

        print(f"  ✓ Lap {lap_id}: {lap['lap_time_s']}s | gap {gap:+.3f}s | {lap['n_samples']} samples")

        return jsonify({
            "ok": True, "lap_id": lap_id,
            "samples": lap["n_samples"], "lap_time": lap["lap_time_s"],
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.route("/dashboard/<int:lap_id>")
def dashboard(lap_id):
    dash = LAPS_DIR / f"lap_{lap_id}" / "dashboard.html"
    if not dash.exists():
        return f"Lap {lap_id} not found.", 404
    resp = make_response(send_file(str(dash)))
    resp.headers["Cache-Control"] = "no-store, no-cache"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/dashboard")
def dashboard_latest():
    entries = load_lap_index()
    if not entries:
        return "No laps recorded yet.", 404
    latest_id = entries[-1]["lap_id"]
    return dashboard(latest_id)


@app.route("/dashboard.html")
def dashboard_html():
    return dashboard_latest()


@app.route("/download/csv/<int:lap_id>")
def download_csv_lap(lap_id):
    csv_path = LAPS_DIR / f"lap_{lap_id}" / "lap.csv"
    if not csv_path.exists():
        return f"No CSV for lap {lap_id}.", 404
    resp = make_response(send_file(
        str(csv_path.resolve()), as_attachment=True,
        download_name=f"lap_{lap_id}.csv", mimetype="text/csv"
    ))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/download/csv")
def download_csv_latest():
    entries = load_lap_index()
    if not entries:
        return "No laps recorded yet.", 404
    return download_csv_lap(entries[-1]["lap_id"])


def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://localhost:8080")


if __name__ == "__main__":
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "unknown"

    # Pre-generate reference lap on startup so PC can load it immediately
    OUTPUT_DIR.mkdir(exist_ok=True)
    ref_json = OUTPUT_DIR / "fast_laps.json"
    if not ref_json.exists():
        ref_mcap = BASE_DIR.parent / "data/hackathon_fast_laps.mcap"
        if ref_mcap.exists():
            print("  Pre-generating reference lap...", end="", flush=True)
            try:
                ref_data = extract_lap(str(ref_mcap), lap_label="fast_laps")
                save_lap_json(ref_data, str(ref_json))
                print(" Done ✓")
            except Exception as e:
                print(f" Failed: {e}")
        else:
            print(f"  Note: {ref_mcap} not found — reference loads after first lap")

    print("\n" + "="*50)
    print("  AI Race Engineer — Session Manager")
    print("="*50)
    print(f"  Mac:  http://localhost:8080")
    print(f"  Network: http://{local_ip}:8080")
    print(f"  Enter this IP in ac_recorder: {local_ip}")
    print("="*50 + "\n")

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=8080, debug=False)
