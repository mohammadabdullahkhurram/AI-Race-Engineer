"""
run.py  —  AI Race Engineer
Press ▶ in VS Code to run everything.
Extracts telemetry → analyzes → generates coaching → opens dashboard.
"""

import json
import os
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from extractor     import extract_lap, save_lap_json
from analyzer      import run_analysis
from coach         import generate_coaching_report, print_coaching_report
from race_analyzer import run_race_analysis

# ── CONFIG — change these if your filenames differ ───────────────────────────
BASE_DIR   = Path(__file__).parent
REF_MCAP   = str(BASE_DIR.parent / "data/hackathon_fast_laps.mcap")
COMP_MCAP  = str(BASE_DIR.parent / "data/hackathon_good_lap.mcap")
OUTPUT_DIR = str(BASE_DIR / "output")
RACE_MCAP  = str(BASE_DIR.parent / "data/hackathon_wheel_to_wheel.mcap")
# ─────────────────────────────────────────────────────────────────────────────


def banner(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def run_pipeline():
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    # ── STEP 1: Extract ───────────────────────────────────────────────────────
    banner("STEP 1 / 3  —  Extracting telemetry")

    ref_json  = f"{OUTPUT_DIR}/fast_laps.json"
    comp_json = f"{OUTPUT_DIR}/good_lap.json"

    ref_data = extract_lap(REF_MCAP, lap_label="fast_laps")
    save_lap_json(ref_data, ref_json)

    comp_data = extract_lap(COMP_MCAP, lap_label="good_lap")
    save_lap_json(comp_data, comp_json)

    # ── STEP 2: Analyze ───────────────────────────────────────────────────────
    banner("STEP 2 / 3  —  Comparing laps")

    analysis = run_analysis(ref_json, comp_json)
    analysis_path = f"{OUTPUT_DIR}/analysis.json"
    with open(analysis_path, "w") as f:
        json.dump(analysis, f, indent=2)

    print(f"  Total delta  : {analysis['total_time_delta_s']:+.3f}s")
    for s in analysis["sectors"]:
        print(f"  {s['sector_name']}: {s['time_delta_s']:+.3f}s  |  "
              f"min speed delta {s['speed_delta_at_min_kmh']:+.1f} km/h")

    # ── STEP 3: Coach ─────────────────────────────────────────────────────────
    banner("STEP 3 / 3  —  Generating coaching report")

    coaching = generate_coaching_report(analysis)
    coaching_path = f"{OUTPUT_DIR}/coaching.json"
    with open(coaching_path, "w") as f:
        json.dump(coaching, f, indent=2)

    print_coaching_report(coaching)

    # ── STEP 4: Race Analysis ────────────────────────────────────────────────────
    banner("STEP 4 / 4  —  Wheel-to-wheel race analysis")
    race_result = None
    try:
        race_result = run_race_analysis(RACE_MCAP, ref_json, OUTPUT_DIR)
        s = race_result["summary"]
        print(f"  Laps: {s['total_laps']}  |  Best: {s['best_lap_time_s']:.1f}s  |  Events: {s['total_events']}")
    except Exception as e:
        print(f"  Race analysis skipped: {e}")

    # ── STEP 5: Dashboard ─────────────────────────────────────────────────────
    banner("Building dashboard")

    html = build_dashboard(analysis, coaching, ref_json, comp_json, race_result)
    dashboard_path = f"{OUTPUT_DIR}/dashboard.html"
    with open(dashboard_path, "w") as f:
        f.write(html)
    print(f"  Saved → {dashboard_path}")

    webbrowser.open(f"file://{os.path.abspath(dashboard_path)}")
    print("  Dashboard opened in browser ✓")


# ── DASHBOARD BUILDER ─────────────────────────────────────────────────────────

def load_trace(json_path):
    try:
        data = json.load(open(json_path))["laps"][0]["channels"]
        step = 5
        return {
            "dist":     data["dist_m"][::step],
            "speed":    data["speed_kmh"][::step],
            "throttle": data["throttle"][::step],
            "brake":    data["brake"][::step],
            "x":        data["x"][::step],
            "y":        data["y"][::step],
        }
    except Exception:
        return None


def build_track_map_data(fast_trace, good_trace, analysis):
    if not fast_trace or not good_trace:
        return None
    import numpy as np
    fast_d = fast_trace["dist"]
    good_d = good_trace["dist"]
    good_s_interp = list(np.interp(fast_d, good_d, good_trace["speed"]))
    delta = [g - f for f, g in zip(fast_trace["speed"], good_s_interp)]
    return {
        "x":     fast_trace["x"],
        "y":     fast_trace["y"],
        "delta": delta,
        "corners": [
            {"name": c["corner_name"], "dist": c["dist_m"], "delta": c["apex_speed_delta_kmh"]}
            for c in analysis.get("corners", [])
        ],
    }


def fmt_time(s):
    s = abs(s)
    mins = int(s) // 60
    secs = s % 60
    return f"{mins}:{secs:06.3f}" if mins > 0 else f"{secs:.3f}s"


def build_dashboard(analysis, coaching, ref_json, comp_json, race_result=None):
    ref_time  = analysis["ref_lap_time_s"]
    comp_time = analysis["comp_lap_time_s"]
    delta     = analysis["total_time_delta_s"]
    sectors   = analysis["sectors"]
    priority  = coaching.get("priority_actions", [])
    corner_coaching = coaching.get("corner_coaching", [])
    summary   = coaching.get("overall_summary", "")
    positives = coaching.get("positive_observations", [])

    delta_sign  = "+" if delta > 0 else ""
    delta_color = "#E8002D" if delta > 0 else "#00D2BE"

    fast_trace = load_trace(ref_json)
    good_trace = load_trace(comp_json)

    speed_trace_js = ""
    if fast_trace and good_trace:
        speed_trace_js = f"""
        hasSpeedData = true;
        const fastDist     = {json.dumps(fast_trace['dist'])};
        const fastSpeed    = {json.dumps(fast_trace['speed'])};
        const fastThrottle = {json.dumps(fast_trace['throttle'])};
        const fastBrake    = {json.dumps(fast_trace['brake'])};
        const goodDist     = {json.dumps(good_trace['dist'])};
        const goodSpeed    = {json.dumps(good_trace['speed'])};
        const goodThrottle = {json.dumps(good_trace['throttle'])};
        const goodBrake    = {json.dumps(good_trace['brake'])};
        """

    # Track map data
    track_map_js = ""
    map_data = build_track_map_data(fast_trace, good_trace, analysis)
    if map_data:
        track_map_js = f"""
        hasMapData = true;
        const trackX     = {json.dumps(map_data['x'])};
        const trackY     = {json.dumps(map_data['y'])};
        const trackDelta = {json.dumps(map_data['delta'])};
        const corners    = {json.dumps(map_data['corners'])};
        """

    def sector_cards():
        html = ""
        for s in sectors:
            dt = s["time_delta_s"]
            color = "#E8002D" if dt > 0 else "#00D2BE"
            sign  = "+" if dt > 0 else ""
            pct   = min(abs(dt) / 4.0 * 100, 100)
            html += f"""
            <div class="sector-card">
              <div class="sector-header">
                <span class="sector-name">{s['sector_name'].upper()}</span>
                <span class="sector-delta" style="color:{color}">{sign}{dt:.3f}s</span>
              </div>
              <div class="delta-bar-wrap"><div class="delta-bar" style="width:{pct}%;background:{color}"></div></div>
              <div class="sector-stats">
                <div class="stat-item">
                  <div class="stat-label">MIN SPEED</div>
                  <div class="stat-val">{s['comp_min_speed_kmh']:.0f} <span class="stat-ref">/ {s['ref_min_speed_kmh']:.0f}</span></div>
                </div>
                <div class="stat-item">
                  <div class="stat-label">DELTA</div>
                  <div class="stat-val" style="color:{('#E8002D' if s['speed_delta_at_min_kmh']<0 else '#00D2BE')}">{s['speed_delta_at_min_kmh']:+.1f} km/h</div>
                </div>
                <div class="stat-item">
                  <div class="stat-label">THROTTLE</div>
                  <div class="stat-val">{s['comp_avg_throttle']*100:.0f}% <span class="stat-ref">/ {s['ref_avg_throttle']*100:.0f}%</span></div>
                </div>
              </div>
            </div>"""
        return html

    def priority_cards():
        html = ""
        for a in priority[:5]:
            conf_color = "#E8002D" if a["confidence"] == "high" else "#FF9800"
            html += f"""
            <div class="action-card" style="animation-delay:{a['priority']*0.08}s">
              <div class="action-header">
                <span class="action-num">#{a['priority']}</span>
                <span class="action-loc">{a['location'].upper()}</span>
                <span class="action-gain">~{a['time_gain_s']:.3f}s</span>
                <span class="action-conf" style="border-color:{conf_color};color:{conf_color}">{a['confidence'].upper()}</span>
              </div>
              <div class="action-problem">{a['issue']}</div>
              <div class="action-fix">{a['instruction']}</div>
              <div class="action-evidence">📊 {a['evidence']}</div>
            </div>"""
        return html

    def corner_cards():
        html = ""
        for c in corner_coaching[:8]:
            html += f"""
            <div class="corner-card">
              <div class="corner-header">
                <span class="corner-name">{c['corner'].upper()}</span>
                <span class="corner-dist">@ {c['dist_m']:.0f}m</span>
                <span class="corner-gain">~{c['time_gain_s']:.3f}s</span>
              </div>
              <div class="corner-type">{c['corner_type'].replace('_',' ').upper()}</div>
              <div class="corner-issue">{c['technique_issue']}</div>
              <div class="corner-fix">{c['fix']}</div>
              <div class="corner-evidence">{c['data_evidence']}</div>
            </div>"""
        return html

    def positive_items():
        return "".join(f'<div class="positive-item">✓ {p}</div>' for p in positives)

    def race_section():
        if not race_result:
            return ""
        s   = race_result["summary"]
        laps = race_result["laps"]
        events = race_result["all_events"]

        stat_html = f"""
        <div class="race-grid">
          <div class="race-stat">
            <div class="race-stat-label">Total Laps</div>
            <div class="race-stat-val">{s['total_laps']}</div>
          </div>
          <div class="race-stat">
            <div class="race-stat-label">Best Lap</div>
            <div class="race-stat-val" style="color:var(--teal)">{fmt_time(s['best_lap_time_s'])}</div>
            <div class="race-stat-sub">Lap {s['best_lap_number']}</div>
          </div>
          <div class="race-stat">
            <div class="race-stat-label">Pace vs Reference</div>
            <div class="race-stat-val" style="color:var(--red)">+{s['pace_vs_ref_s']:.3f}s</div>
            <div class="race-stat-sub">Best race lap</div>
          </div>
          <div class="race-stat">
            <div class="race-stat-label">Lap Variation</div>
            <div class="race-stat-val">{s['lap_time_range_s']:.3f}s</div>
            <div class="race-stat-sub">Best to worst</div>
          </div>
          <div class="race-stat">
            <div class="race-stat-label">Race Events</div>
            <div class="race-stat-val" style="color:var(--yellow)">{s['total_events']}</div>
            <div class="race-stat-sub">{s['defensive_brakes']} brake · {s['lift_offs']} lift</div>
          </div>
        </div>"""

        # Lap table
        rows = ""
        for l in laps:
            best_cls = " class=\"lap-best\"" if l['lap_number'] == s['best_lap_number'] else ""
            sign = "+" if l['time_delta_s'] > 0 else ""
            delta_col = "var(--red)" if l['time_delta_s'] > 0 else "var(--teal)"
            rows += f"""<tr{best_cls}>
              <td>LAP {l['lap_number']}{'  ★' if l['lap_number'] == s['best_lap_number'] else ''}</td>
              <td>{fmt_time(l['lap_time_s'])}</td>
              <td style="color:{delta_col}">{sign}{l['time_delta_s']:.3f}s</td>
              <td>{l['avg_speed_delta']:+.1f} km/h</td>
              <td>{l['avg_throttle']*100:.0f}%</td>
            </tr>"""

        table_html = f"""
        <table class="lap-table">
          <thead><tr>
            <th>Lap</th><th>Time</th><th>vs Reference</th><th>Avg Speed Delta</th><th>Avg Throttle</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

        # Events
        event_html = ""
        for e in events[:8]:
            event_html += f"""
        <div class="event-card">
          <div class="event-header">
            <span class="event-type">{e['label'].upper()}</span>
            <span class="event-dist">@ {e['dist_m']:.0f}m</span>
            <span class="event-speed">−{e['speed_drop_kmh']:.0f} km/h</span>
          </div>
          <div class="event-desc">{e['description']}</div>
        </div>"""

        if not event_html:
            event_html = '<div style="font-size:12px;color:var(--muted);padding:12px 0">No anomalous events detected — clean race pace throughout.</div>'

        return f"""
        <div class="sec">Race Analysis — Wheel to Wheel</div>
        {stat_html}
        {table_html}
        <div class="sec">Race Events</div>
        {event_html}"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Race Engineer — Yas Marina</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{{
  --red:#E8002D;--teal:#00D2BE;--yellow:#FFD700;
  --bg:#050505;--bg2:#0d0d0d;--bg3:#141414;
  --border:#1e1e1e;--border2:#2a2a2a;
  --muted:#555;--dim:#222;
  --fd:'Barlow Condensed',sans-serif;
  --fm:'JetBrains Mono',monospace;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:#fff;font-family:var(--fm);font-size:13px;min-height:100vh;overflow-x:hidden}}
body::before{{content:'';position:fixed;inset:0;background-image:repeating-linear-gradient(45deg,rgba(255,255,255,.012) 0,rgba(255,255,255,.012) 1px,transparent 1px,transparent 8px),repeating-linear-gradient(-45deg,rgba(255,255,255,.012) 0,rgba(255,255,255,.012) 1px,transparent 1px,transparent 8px);pointer-events:none;z-index:0}}
.wrap{{position:relative;z-index:1;max-width:1400px;margin:0 auto;padding:0 24px 80px}}

/* top bar */
.topbar{{display:flex;align-items:center;justify-content:space-between;padding:16px 0;border-bottom:1px solid var(--border);margin-bottom:32px}}
.brand{{display:flex;align-items:center;gap:12px}}
.brand-flag{{width:4px;height:34px;background:var(--red)}}
.brand-title{{font-family:var(--fd);font-size:22px;font-weight:800;letter-spacing:4px;text-transform:uppercase}}
.brand-sub{{font-size:10px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-top:3px}}
.session{{text-align:right;font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}}
.session span{{display:block;color:#fff;font-size:12px;margin-top:2px}}

/* hero */
.hero{{display:grid;grid-template-columns:1fr auto 1fr;background:var(--bg2);border:1px solid var(--border);margin-bottom:4px;position:relative;overflow:hidden;animation:fadeUp .5s both}}
.hero::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--red)}}
.hero-lap{{padding:28px 32px}}
.hero-lap.ref{{border-right:1px solid var(--border)}}
.hero-lap.comp{{border-left:1px solid var(--border)}}
.hero-label{{font-size:10px;letter-spacing:3px;text-transform:uppercase;color:var(--muted);margin-bottom:8px}}
.hero-time{{font-family:var(--fd);font-size:60px;font-weight:700;letter-spacing:-1px;line-height:1}}
.hero-lap.comp .hero-time{{color:#999}}
.hero-tag{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;margin-top:6px}}
.hero-mid{{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px 44px;background:var(--bg3)}}
.mid-label{{font-size:10px;letter-spacing:3px;text-transform:uppercase;color:var(--muted);margin-bottom:6px}}
.mid-val{{font-family:var(--fd);font-size:52px;font-weight:800;letter-spacing:-1px;line-height:1;color:{delta_color}}}
.mid-sub{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:2px;margin-top:6px;text-align:center}}

/* summary */
.summary{{background:var(--bg3);border:1px solid var(--border);border-top:none;padding:14px 24px;font-size:12px;color:#aaa;line-height:1.7;margin-bottom:28px;animation:fadeUp .5s .1s both}}

/* section label */
.sec{{font-family:var(--fd);font-size:11px;letter-spacing:4px;text-transform:uppercase;color:var(--muted);margin-bottom:12px;display:flex;align-items:center;gap:10px}}
.sec::after{{content:'';flex:1;height:1px;background:var(--border)}}

/* main grid */
.main-grid{{display:grid;grid-template-columns:320px 1fr;gap:20px;margin-bottom:28px;animation:fadeUp .5s .2s both}}

/* sectors */
.sector-card{{background:var(--bg2);border:1px solid var(--border);padding:16px;margin-bottom:8px}}
.sector-header{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}}
.sector-name{{font-family:var(--fd);font-size:15px;font-weight:700;letter-spacing:2px}}
.sector-delta{{font-size:18px;font-weight:700}}
.delta-bar-wrap{{height:2px;background:var(--dim);margin-bottom:12px}}
.delta-bar{{height:100%;width:0;transition:width 1.2s cubic-bezier(.4,0,.2,1)}}
.sector-stats{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}}
.stat-item{{display:flex;flex-direction:column;gap:3px}}
.stat-label{{font-size:9px;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase}}
.stat-val{{font-size:13px;font-weight:700}}
.stat-ref{{font-size:11px;color:var(--muted);font-weight:400}}

/* chart */
.chart-wrap{{background:var(--bg2);border:1px solid var(--border);padding:20px}}
.chart-legend{{display:flex;gap:20px;margin-bottom:14px}}
.leg-item{{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}}
.leg-line{{width:24px;height:2px}}
.chart-tabs{{display:flex;gap:2px;margin-bottom:14px}}
.ctab{{padding:6px 14px;font-family:var(--fm);font-size:10px;letter-spacing:2px;text-transform:uppercase;background:var(--bg3);border:1px solid var(--border);color:var(--muted);cursor:pointer;transition:all .15s}}
.ctab.active{{background:var(--red);border-color:var(--red);color:#fff}}
.chart-container{{position:relative;height:280px}}

/* priority actions */
.action-card{{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--red);padding:16px;margin-bottom:8px;animation:slideIn .4s both}}
.action-header{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
.action-num{{font-family:var(--fd);font-size:24px;font-weight:800;color:var(--red);line-height:1;min-width:30px}}
.action-loc{{font-family:var(--fd);font-size:17px;font-weight:700;letter-spacing:2px;flex:1}}
.action-gain{{font-size:14px;font-weight:700;color:var(--teal)}}
.action-conf{{font-size:9px;letter-spacing:2px;text-transform:uppercase;border:1px solid;padding:2px 6px}}
.action-problem{{font-size:12px;color:#ccc;margin-bottom:6px;line-height:1.5}}
.action-fix{{font-size:12px;color:var(--teal);line-height:1.5;margin-bottom:6px;padding:8px 10px;border-left:2px solid var(--teal);background:rgba(0,210,190,.05)}}
.action-evidence{{font-size:11px;color:var(--muted)}}

/* corners grid */
.corners-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;margin-bottom:28px;animation:fadeUp .5s .3s both}}
.corner-card{{background:var(--bg2);border:1px solid var(--border);padding:16px;transition:border-color .2s,transform .2s}}
.corner-card:hover{{border-color:var(--red);transform:translateY(-2px)}}
.corner-header{{display:flex;align-items:baseline;gap:8px;margin-bottom:4px}}
.corner-name{{font-family:var(--fd);font-size:18px;font-weight:800;letter-spacing:2px}}
.corner-dist{{font-size:11px;color:var(--muted);flex:1}}
.corner-gain{{font-size:13px;font-weight:700;color:var(--teal)}}
.corner-type{{font-size:9px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-bottom:10px}}
.corner-issue{{font-size:12px;color:#bbb;line-height:1.5;margin-bottom:8px}}
.corner-fix{{font-size:12px;color:var(--teal);line-height:1.5;padding:8px 10px;background:rgba(0,210,190,.06);border-left:2px solid var(--teal);margin-bottom:8px}}
.corner-evidence{{font-size:11px;color:var(--muted)}}

/* track map */
.map-wrap{{background:var(--bg2);border:1px solid var(--border);padding:20px;margin-bottom:28px;animation:fadeUp .5s .15s both}}
.map-container{{position:relative;width:100%;height:480px}}
.map-container canvas{{width:100%!important;height:100%!important}}
.map-legend{{display:flex;align-items:center;gap:16px;margin-top:12px;flex-wrap:wrap}}
.map-legend-grad{{width:120px;height:6px;border-radius:3px;background:linear-gradient(to right,#E8002D,#333,#00D2BE)}}
.map-legend-labels{{display:flex;justify-content:space-between;width:120px;font-size:9px;color:var(--muted);letter-spacing:1px;margin-top:3px}}
.map-legend-item{{display:flex;align-items:center;gap:6px;font-size:10px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}}

/* positives */
.positives{{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--teal);padding:16px 20px;margin-bottom:28px}}
.positive-item{{font-size:12px;color:var(--teal);line-height:1.9}}

/* race section */
.race-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px;margin-bottom:20px}}
.race-stat{{background:var(--bg2);border:1px solid var(--border);padding:14px 16px}}
.race-stat-label{{font-size:9px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-bottom:6px}}
.race-stat-val{{font-family:var(--fd);font-size:28px;font-weight:700;line-height:1}}
.race-stat-sub{{font-size:10px;color:var(--muted);margin-top:4px}}
.lap-table{{width:100%;border-collapse:collapse;margin-bottom:20px;font-size:12px}}
.lap-table th{{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}}
.lap-table td{{padding:10px 12px;border-bottom:1px solid var(--dim)}}
.lap-table tr:hover td{{background:var(--bg3)}}
.lap-best td{{color:var(--teal)!important}}
.event-card{{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--yellow);padding:14px;margin-bottom:8px}}
.event-header{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
.event-type{{font-family:var(--fd);font-size:14px;font-weight:700;letter-spacing:1px;color:var(--yellow)}}
.event-dist{{font-size:11px;color:var(--muted)}}
.event-speed{{font-size:12px;font-weight:700;color:var(--red)}}
.event-desc{{font-size:12px;color:#aaa;line-height:1.5}}

/* animations */
@keyframes fadeUp{{from{{opacity:0;transform:translateY(16px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes slideIn{{from{{opacity:0;transform:translateX(-12px)}}to{{opacity:1;transform:translateX(0)}}}}

@media(max-width:900px){{
  .main-grid{{grid-template-columns:1fr}}
  .hero{{grid-template-columns:1fr}}
  .hero-lap.ref{{border-right:none;border-bottom:1px solid var(--border)}}
  .hero-lap.comp{{border-left:none;border-top:1px solid var(--border)}}
  .hero-mid{{border-top:1px solid var(--border)}}
  .hero-time{{font-size:44px}}
}}
</style>
</head>
<body>
<div class="wrap">

<div class="topbar">
  <div class="brand">
    <div class="brand-flag"></div>
    <div>
      <div class="brand-title">AI Race Engineer</div>
      <div class="brand-sub">Yas Marina Circuit · Autonomous Track</div>
    </div>
  </div>
  <div class="session">Constructor GenAI Hackathon 2026<span>Lap Comparison Report</span></div>
</div>

<div class="hero">
  <div class="hero-lap ref">
    <div class="hero-label">Reference Lap</div>
    <div class="hero-time">{fmt_time(ref_time)}</div>
    <div class="hero-tag">Fast Laps · Fastest</div>
  </div>
  <div class="hero-mid">
    <div class="mid-label">Gap</div>
    <div class="mid-val">{delta_sign}{fmt_time(abs(delta))}</div>
    <div class="mid-sub">{'Slower' if delta > 0 else 'Faster'} than reference</div>
  </div>
  <div class="hero-lap comp">
    <div class="hero-label">Driver Lap</div>
    <div class="hero-time">{fmt_time(comp_time)}</div>
    <div class="hero-tag">Good Lap · Conservative</div>
  </div>
</div>

<div class="summary">{summary}</div>

<div class="main-grid">
  <div>
    <div class="sec">Sector Breakdown</div>
    {sector_cards()}
  </div>
  <div>
    <div class="chart-wrap">
      <div class="sec">Telemetry Trace</div>
      <div class="chart-legend">
        <div class="leg-item"><div class="leg-line" style="background:var(--red)"></div>Reference</div>
        <div class="leg-item"><div class="leg-line" style="background:#555"></div>Driver</div>
      </div>
      <div class="chart-tabs">
        <button class="ctab active" onclick="switchChart('speed',this)">Speed</button>
        <button class="ctab" onclick="switchChart('throttle',this)">Throttle</button>
        <button class="ctab" onclick="switchChart('brake',this)">Brake</button>
      </div>
      <div class="chart-container"><canvas id="tc"></canvas></div>
    </div>
  </div>
</div>

<div class="map-wrap">
  <div class="sec">Track Map — Delta Overlay</div>
  <div class="map-container"><canvas id="mapCanvas"></canvas></div>
  <div class="map-legend">
    <div>
      <div class="map-legend-grad"></div>
      <div class="map-legend-labels"><span>Slower</span><span>Even</span><span>Faster</span></div>
    </div>
    <div class="map-legend-item" style="margin-left:16px">
      <div style="width:16px;height:2px;background:#E8002D"></div>Reference line
    </div>
    <div class="map-legend-item">
      <div style="width:16px;height:2px;background:#555"></div>Driver line
    </div>
  </div>
</div>

<div class="sec">Priority Actions</div>
{priority_cards()}

<br>
<div class="sec">Corner Analysis</div>
<div class="corners-grid">{corner_cards()}</div>

{'<div class="sec">What\'s Working</div><div class="positives">' + positive_items() + '</div>' if positives else ''}

{race_section()}

</div>
<script>
let hasSpeedData = false;
{speed_trace_js}
let chart = null;
const colors = {{speed:{{r:'#E8002D',c:'#555'}},throttle:{{r:'#00D2BE',c:'#336655'}},brake:{{r:'#FFD700',c:'#665500'}}}};
const ylabels = {{speed:'Speed (km/h)',throttle:'Throttle (0-1)',brake:'Brake (0-1)'}};
function buildChart(mode){{
  const ctx = document.getElementById('tc');
  if(!hasSpeedData) return;
  const src = {{speed:{{r:fastSpeed,c:goodSpeed}},throttle:{{r:fastThrottle,c:goodThrottle}},brake:{{r:fastBrake,c:goodBrake}}}};
  const d = src[mode]; const col = colors[mode];
  if(chart) chart.destroy();
  chart = new Chart(ctx,{{
    type:'line',
    data:{{
      labels:fastDist,
      datasets:[
        {{label:'Reference',data:d.r,borderColor:col.r,borderWidth:1.5,pointRadius:0,tension:0.3,fill:false}},
        {{label:'Driver',data:d.c,borderColor:col.c,borderWidth:1.5,pointRadius:0,tension:0.3,fill:false}}
      ]
    }},
    options:{{
      responsive:true,maintainAspectRatio:false,
      animation:{{duration:500,easing:'easeInOutQuart'}},
      plugins:{{
        legend:{{display:false}},
        tooltip:{{mode:'index',intersect:false,backgroundColor:'#0d0d0d',borderColor:'#222',borderWidth:1,titleColor:'#555',bodyColor:'#fff',titleFont:{{family:'JetBrains Mono',size:10}},bodyFont:{{family:'JetBrains Mono',size:12}},callbacks:{{title:i=>`${{Math.round(i[0].label)}}m`}}}}
      }},
      scales:{{
        x:{{type:'linear',title:{{display:true,text:'Distance (m)',color:'#333',font:{{family:'JetBrains Mono',size:10}}}},ticks:{{color:'#333',font:{{family:'JetBrains Mono',size:10}},maxTicksLimit:8}},grid:{{color:'#111'}}}},
        y:{{title:{{display:true,text:ylabels[mode],color:'#333',font:{{family:'JetBrains Mono',size:10}}}},ticks:{{color:'#333',font:{{family:'JetBrains Mono',size:10}}}},grid:{{color:'#111'}}}}
      }}
    }}
  }});
}}
function switchChart(mode,btn){{
  document.querySelectorAll('.ctab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  buildChart(mode);
}}
window.addEventListener('load',()=>{{
  buildChart('speed');
  setTimeout(()=>document.querySelectorAll('.delta-bar').forEach(b=>{{const w=b.style.width;b.style.width='0';setTimeout(()=>b.style.width=w,50)}}),400);
  drawTrackMap();
}});

// ── TRACK MAP ────────────────────────────────────────────────────────────────
let hasMapData = false;
{track_map_js}

function deltaToColor(d){{
  // d < 0 = driver slower = red, d > 0 = driver faster = teal
  const scale = 20; // km/h range
  const t = Math.max(-1, Math.min(1, d / scale));
  if(t < 0){{
    const r = 232, g = Math.round(0 + (1+t)*45), b = Math.round(45 + (1+t)*0);
    return `rgb(${{r}},${{g}},${{Math.round(45*(1+t))}})`;
  }} else {{
    const r = Math.round(t < 0.5 ? 80 : 0), g = Math.round(210 * t), b = Math.round(190 * t);
    return `rgb(${{r}},${{g}},${{b}})`;
  }}
}}

function drawTrackMap(){{
  const canvas = document.getElementById('mapCanvas');
  if(!canvas || !hasMapData) return;
  const parent = canvas.parentElement;
  canvas.width  = parent.clientWidth;
  canvas.height = parent.clientHeight;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0,0,W,H);

  const xs = trackX, ys = trackY;
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const rangeX = maxX - minX, rangeY = maxY - minY;

  const pad = 60;
  const scaleX = (W - pad*2) / rangeX;
  const scaleY = (H - pad*2) / rangeY;
  const scale  = Math.min(scaleX, scaleY);
  const offX   = (W - rangeX * scale) / 2 - minX * scale;
  const offY   = (H - rangeY * scale) / 2 - minY * scale;

  function toCanvas(x, y){{
    return [x * scale + offX, H - (y * scale + offY)];
  }}

  // Draw thick shadow/glow for track base
  ctx.lineWidth = 14;
  ctx.strokeStyle = 'rgba(255,255,255,0.04)';
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  const [sx0, sy0] = toCanvas(xs[0], ys[0]);
  ctx.moveTo(sx0, sy0);
  for(let i=1;i<xs.length;i++){{ const [cx,cy]=toCanvas(xs[i],ys[i]); ctx.lineTo(cx,cy); }}
  ctx.stroke();

  // Draw delta-colored segments
  ctx.lineWidth = 5;
  ctx.lineCap = 'round';
  for(let i=1;i<xs.length;i++){{
    const [x1,y1] = toCanvas(xs[i-1],ys[i-1]);
    const [x2,y2] = toCanvas(xs[i],ys[i]);
    ctx.strokeStyle = deltaToColor(trackDelta[i] || 0);
    ctx.beginPath();
    ctx.moveTo(x1,y1);
    ctx.lineTo(x2,y2);
    ctx.stroke();
  }}

  // Draw corner labels
  if(corners && corners.length){{
    corners.forEach(c => {{
      // Find closest point by distance
      const maxDist = Math.max(...trackX.map((_,i)=>i)) * 5; // approx
      // Find index closest to corner dist
      const idx = Math.min(Math.round(c.dist / 5), xs.length-1);
      if(idx < 0 || idx >= xs.length) return;
      const [cx,cy] = toCanvas(xs[idx], ys[idx]);
      const isLoss = c.delta < -3;
      const isGain = c.delta > 3;
      const col = isLoss ? '#E8002D' : isGain ? '#00D2BE' : '#666';

      // Dot
      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, Math.PI*2);
      ctx.fillStyle = col;
      ctx.fill();
      ctx.strokeStyle = '#000';
      ctx.lineWidth = 1;
      ctx.stroke();

      // Label
      ctx.font = 'bold 10px "JetBrains Mono", monospace';
      ctx.fillStyle = col;
      ctx.textAlign = 'center';
      ctx.fillText(c.name, cx, cy - 10);

      // Delta
      const sign = c.delta > 0 ? '+' : '';
      ctx.font = '9px "JetBrains Mono", monospace';
      ctx.fillStyle = col;
      ctx.fillText(`${{sign}}${{c.delta.toFixed(1)}}`, cx, cy - 20);
    }});
  }}

  // Start/finish line
  const [fx,fy] = toCanvas(xs[0], ys[0]);
  ctx.beginPath();
  ctx.arc(fx,fy,7,0,Math.PI*2);
  ctx.fillStyle = '#FFD700';
  ctx.fill();
  ctx.font = 'bold 10px "JetBrains Mono", monospace';
  ctx.fillStyle = '#FFD700';
  ctx.textAlign = 'center';
  ctx.fillText('S/F', fx, fy - 12);
}}

window.addEventListener('resize', drawTrackMap);
</script>
</body>
</html>"""


if __name__ == "__main__":
    run_pipeline()
